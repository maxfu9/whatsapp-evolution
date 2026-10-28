"""Authenticated receipt storage and read-only provider reconciliation."""

import hashlib
import hmac
from urllib.parse import quote

import frappe
import requests
from frappe.utils import now_datetime, add_to_date


def authenticate_webhook(request):
    secret = frappe.conf.get("whatsapp_evolution_webhook_secret")
    supplied = request.headers.get("X-Webhook-Secret", "")
    if not secret or not isinstance(supplied, str) or not hmac.compare_digest(str(secret), supplied):
        frappe.local.response.http_status_code = 401
        return False
    return True


def apply_receipt(account, message_id, status, received_on=None):
    from whatsapp_evolution.whatsapp_evolution.providers.evolution import _status_rank
    if not account or not message_id or not _status_rank(status):
        return False
    name = hashlib.sha256(f"{account}|{message_id}|{status}".encode()).hexdigest()
    received_on = received_on or now_datetime()
    if not frappe.db.exists("WhatsApp Delivery Receipt", name):
        receipt = frappe.get_doc({
            "doctype": "WhatsApp Delivery Receipt", "name": name,
            "whatsapp_account": account, "message_id": message_id,
            "status": status, "received_on": received_on, "applied": 0,
        })
        # Only authenticated callbacks or trusted account-scoped lookups reach
        # this internal receipt log; provider guests cannot have Desk roles.
        receipt.insert(ignore_permissions=True, ignore_if_duplicate=True)
    else:
        received_on = frappe.db.get_value("WhatsApp Delivery Receipt", name, "received_on")
    row = frappe.db.get_value("WhatsApp Message", {
        "message_id": message_id, "whatsapp_account": account, "type": "Outgoing",
    }, ["name", "status", "sent_at", "delivered_at", "read_at"], as_dict=True, for_update=True)
    if not row:
        return False
    values = {}
    if _status_rank(status) > _status_rank(row.status):
        values["status"] = status
    field = {"Sent": "sent_at", "Delivered": "delivered_at", "Read": "read_at", "Played": "read_at"}.get(status)
    if field and not row.get(field):
        values[field] = received_on
    if values:
        frappe.db.set_value("WhatsApp Message", row.name, values)
        frappe.publish_realtime("doc_update", {"doctype": "WhatsApp Message", "name": row.name},
                                doctype="WhatsApp Message", docname=row.name, after_commit=True)
    frappe.db.set_value("WhatsApp Delivery Receipt", name, "applied", 1, update_modified=False)
    return True


def refresh_receipts(name):
    from whatsapp_evolution.utils import get_evolution_settings
    from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider, _map_evolution_status
    doc = frappe.get_doc("WhatsApp Message", name)
    if doc.type != "Outgoing" or not doc.message_id or not doc.whatsapp_account:
        return doc.status
    if frappe.db.get_value("WhatsApp Account", doc.whatsapp_account, "status") != "Active":
        frappe.throw("The message account is inactive.")
    settings = get_evolution_settings(doc.whatsapp_account)
    if settings.get("whatsapp_account") != doc.whatsapp_account:
        frappe.throw("The message account is unavailable.")
    provider = EvolutionProvider(settings)
    response = requests.post(
        provider.api_base + "/chat/findStatusMessage/" + quote(provider.instance, safe=""),
        headers=provider._headers(), json={"where": {"id": doc.message_id}, "offset": 100, "page": 1},
        timeout=15, allow_redirects=False,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Invalid provider receipt response")
    for row in rows:
        if (isinstance(row, dict) and row.get("keyId") == doc.message_id
                and row.get("fromMe") is True):
            status = _map_evolution_status(row.get("status"))
            if status:
                apply_receipt(doc.whatsapp_account, doc.message_id, status)
    frappe.db.set_value("WhatsApp Message", name, "receipt_checked_at", now_datetime(), update_modified=False)
    return frappe.db.get_value("WhatsApp Message", name, "status")


@frappe.whitelist(methods=["POST"])
def refresh_message_status(name):
    doc = frappe.get_doc("WhatsApp Message", name)
    doc.check_permission("read")
    doc.check_permission("write")
    try:
        return {"status": refresh_receipts(name)}
    except Exception:
        frappe.log_error(frappe.get_traceback(), "WhatsApp Receipt Refresh Failed")
        frappe.throw("Unable to refresh delivery status. Check the account connection and Error Log.")


def reconcile_receipts():
    pending = frappe.get_all("WhatsApp Delivery Receipt", filters={"applied": 0},
                             fields=["whatsapp_account", "message_id", "status", "received_on"],
                             limit_page_length=200, order_by="creation asc")
    for row in pending:
        apply_receipt(row.whatsapp_account, row.message_id, row.status, row.received_on)
        frappe.db.commit()
    rows = frappe.get_all("WhatsApp Message", filters={
        "type": "Outgoing", "status": ["in", ["Success", "Sent", "Delivered"]],
        "creation": [">=", add_to_date(now_datetime(), days=-30)], "message_id": ["is", "set"],
    }, fields=["name"], order_by="receipt_checked_at asc, creation desc", limit_page_length=30)
    for row in rows:
        try:
            refresh_receipts(row.name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            # Advance failed checks too, so one broken account cannot starve
            # all other messages in this bounded round-robin reconciliation.
            frappe.db.set_value("WhatsApp Message", row.name, "receipt_checked_at", now_datetime(), update_modified=False)
            frappe.db.commit()
            frappe.log_error(frappe.get_traceback(), "WhatsApp Receipt Reconciliation Failed")
