"""Revoke invoice-linked outgoing messages only after local deletion commits."""

import hashlib
import time
from datetime import timedelta
from urllib.parse import quote

import frappe
import requests
from frappe.utils import get_datetime, now_datetime

INVOICES = {"Sales Invoice", "Purchase Invoice"}


def _locked_message(name):
    return frappe.db.get_value("WhatsApp Message", name,
        ["name", "type", "status", "message_id", "whatsapp_account", "to", "creation", "sent_at",
         "reference_doctype", "reference_name"], as_dict=True, for_update=True)


def _check_not_sending(row):
    if row and row.status == "Started":
        frappe.throw("A linked WhatsApp message is currently being sent. Wait for it to finish before deleting.")


def invoice_deleted(doc, method=None):
    rows = frappe.get_all("WhatsApp Message", filters={
        "reference_doctype": doc.doctype, "reference_name": doc.name,
    }, pluck="name", order_by="name asc", limit_page_length=0)
    locked = [_locked_message(name) for name in rows]
    for row in locked:
        _check_not_sending(row)
    for row in locked:
        if not row:
            continue
        _queue_deletion(row, doc.doctype, doc.name)
        # Retain the message/audit trail without a Dynamic Link that would
        # prevent the invoice's authorized deletion. All changes roll back if
        # any other invoice deletion check fails.
        frappe.db.set_value("WhatsApp Message", row.name,
                            {"reference_doctype": "", "reference_name": ""})


def message_deleted(doc, method=None):
    row = _locked_message(doc.name)
    if not row or row.reference_doctype not in INVOICES:
        return
    _check_not_sending(row)
    _queue_deletion(row, "WhatsApp Message", doc.name)


def _queue_deletion(row, source_doctype, source_name):
    if row.type != "Outgoing":
        return
    provider_id = row.message_id or ""
    sent = bool(provider_id and not provider_id.startswith("queue-"))
    name = hashlib.sha256(f"{row.whatsapp_account}|{provider_id if sent else row.name}".encode()).hexdigest()
    if frappe.db.exists("WhatsApp Deletion Request", name):
        return
    status, details = "Pending", "Awaiting the committed local deletion."
    if not sent:
        if row.status in {"Success", "Sent", "Delivered", "Read", "Played", "Needs Review"}:
            status, details = "Needs Review", "Original provider ID is missing. Remote deletion cannot be verified; check WhatsApp manually."
        else:
            status, details = "Stopped", "No provider message ID; pending sends have been stopped locally."
            frappe.db.set_value("WhatsApp Message", row.name, "status", "Cancelled")
    elif not row.whatsapp_account or not row.get("to"):
        status, details = "Failed", "Original account or recipient is missing; no remote deletion attempted."
    elif now_datetime() - get_datetime(row.sent_at or row.creation) > timedelta(days=2):
        status, details = "Expired", "Outside the two-day deletion window; no remote deletion attempted."
    request = frappe.get_doc({
        "doctype": "WhatsApp Deletion Request", "name": name,
        "message_record": row.name, "message_id": provider_id if sent else "",
        "whatsapp_account": row.whatsapp_account, "recipient": row.get("to"),
        "reference_doctype": row.reference_doctype, "reference_name": row.reference_name,
        "source_doctype": source_doctype, "source_name": source_name,
        "requested_by": frappe.session.user, "status": status, "details": details,
    })
    # ERPNext already authorized the source deletion. Keep its audit request
    # even when the deleting user lacks a WhatsApp Manager Desk role.
    request.insert(ignore_permissions=True, ignore_if_duplicate=True)
    if sent and status == "Pending":
        frappe.enqueue("whatsapp_evolution.utils.deletion.process_deletion", name=name,
                       job_id=f"wa-delete-{name}", queue="short", enqueue_after_commit=True)


def process_deletion(name):
    row = frappe.db.get_value("WhatsApp Deletion Request", name, "*", as_dict=True, for_update=True)
    if not row or row.status != "Pending":
        frappe.db.rollback()
        return
    if frappe.db.exists(row.source_doctype, row.source_name):
        _finish(name, "Stopped", "Source still exists; no remote deletion attempted.")
        frappe.db.commit()
        return
    frappe.db.set_value("WhatsApp Deletion Request", name, "status", "Started")
    frappe.db.commit()
    try:
        from whatsapp_evolution.utils import get_evolution_settings
        from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider
        if frappe.db.get_value("WhatsApp Account", row.whatsapp_account, "status") != "Active":
            raise ValueError("Original account is inactive")
        settings = get_evolution_settings(row.whatsapp_account)
        if settings.get("whatsapp_account") != row.whatsapp_account:
            raise ValueError("Original account is unavailable")
        provider = EvolutionProvider(settings)
        # Retrieve the exact original WhatsApp key, rather than guess a JID
        # from a telephone number (linked-device IDs can differ).
        lookup = requests.post(provider.api_base + "/chat/findMessages/" + quote(provider.instance, safe=""),
            headers=provider._headers(), json={"where": {"key": {"id": row.message_id}}, "offset": 10, "page": 1},
            timeout=20, allow_redirects=False)
        lookup.raise_for_status()
        data = lookup.json()
        records = (data.get("messages") or {}).get("records", [])
        matches = [entry for entry in records if isinstance(entry, dict)
                   and (entry.get("key") or {}).get("id") == row.message_id
                   and (entry.get("key") or {}).get("fromMe") is True]
        if len(matches) != 1:
            raise ValueError("Cannot uniquely verify the original outgoing WhatsApp message")
        entry = matches[0]
        timestamp = entry.get("messageTimestamp")
        if not timestamp or time.time() - float(timestamp) > 2 * 24 * 3600:
            _finish(name, "Expired", "Original message is outside the deletion window or its age is unverifiable.")
            frappe.db.commit()
            return
        key = entry["key"]
        if not key.get("remoteJid"):
            raise ValueError("Original chat identifier is missing")
    except Exception:
        _finish(name, "Failed", "Could not verify the original message/account. No remote deletion attempted; see Error Log.")
        frappe.log_error(frappe.get_traceback(), "WhatsApp Deletion Lookup Failed")
        frappe.db.commit()
        return
    try:
        payload = {k: key[k] for k in ("id", "remoteJid", "fromMe", "participant") if k in key}
        response = requests.delete(provider.api_base + "/chat/deleteMessageForEveryone/" + quote(provider.instance, safe=""),
            headers=provider._headers(), json=payload, timeout=25, allow_redirects=False)
        if 200 <= response.status_code < 300:
            _finish(name, "Requested", "WhatsApp delete-for-everyone request accepted. Recipient removal is not guaranteed.")
        elif response.status_code >= 500 or response.status_code == 408:
            _finish(name, "Needs Review", "Deletion outcome is unknown. Check WhatsApp; no automatic retry will be made.")
        else:
            _finish(name, "Failed", f"Provider rejected deletion (HTTP {response.status_code}).")
    except requests.RequestException:
        _finish(name, "Needs Review", "Deletion outcome is unknown. Check WhatsApp; no automatic retry will be made.")
        frappe.log_error(frappe.get_traceback(), "WhatsApp Deletion Outcome Unknown")
    frappe.db.commit()


def _finish(name, status, details):
    frappe.db.set_value("WhatsApp Deletion Request", name, {
        "status": status, "details": details, "processed_on": now_datetime(),
    })


def recover_deletions():
    for row in frappe.get_all("WhatsApp Deletion Request", filters={"status": "Pending"},
                             pluck="name", order_by="creation asc", limit_page_length=50):
        process_deletion(row)
    cutoff = now_datetime() - timedelta(minutes=15)
    for name in frappe.get_all("WhatsApp Deletion Request", filters={"status": "Started", "modified": ["<", cutoff]},
                              pluck="name", limit_page_length=50):
        current = frappe.db.get_value("WhatsApp Deletion Request", name, "status", for_update=True)
        if current == "Started":
            _finish(name, "Needs Review", "Deletion worker interrupted. Check WhatsApp before another deletion attempt.")
        frappe.db.commit()
