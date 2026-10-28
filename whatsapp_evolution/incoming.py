import frappe
import base64
import mimetypes
from html import escape
from urllib.parse import quote
import requests
from frappe.utils.synchronization import filelock


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _numbers_match(a, b):
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    return len(da) >= 10 and len(db) >= 10 and da[-10:] == db[-10:]


def _find_reference_by_number(number):
    if len(_digits(number)) < 8:
        return None, None
    suffix = _digits(number)[-4:]
    rows = frappe.get_all(
        "Contact Phone",
        filters={"phone": ["like", f"%{suffix}"]},
        fields=["parent", "phone", "is_primary_mobile_no"],
        limit_page_length=100,
    )
    for row in rows:
        if _numbers_match(row.get("phone"), number):
            return "Contact", row.get("parent")

    for dt in ("Customer", "Lead", "Supplier", "Employee"):
        if not frappe.db.exists("DocType", dt):
            continue
        for field in ("mobile_no", "cell_number", "personal_mobile_no", "phone"):
            if not frappe.db.has_column(dt, field):
                continue
            party_rows = frappe.get_all(dt, fields=["name", field],
                filters={field: ["like", f"%{suffix}"]}, limit_page_length=100)
            matches = [row for row in party_rows if _numbers_match(row.get(field), number)]
            if len(matches) == 1:
                return dt, matches[0].get("name")

    return None, None


def handle_incoming_message(msg, whatsapp_account=None):
    if not whatsapp_account or not msg.get("message_id"):
        return None
    # Serialize duplicate callbacks on this site's shared worker filesystem.
    with filelock(f"incoming-wa-{whatsapp_account}-{msg['message_id']}", timeout=15):
        return _store_incoming_message(msg, whatsapp_account)


def _store_incoming_message(msg, whatsapp_account):
    number = (msg.get("from") or "").strip()
    message_id = (msg.get("message_id") or "").strip()

    if message_id:
        existing = frappe.db.get_value("WhatsApp Message", {
            "message_id": message_id, "whatsapp_account": whatsapp_account, "type": "Incoming"
        }, "name")
        if existing:
            if whatsapp_account and not frappe.db.get_value("WhatsApp Message", existing, "whatsapp_account"):
                frappe.db.set_value("WhatsApp Message", existing, "whatsapp_account", whatsapp_account)
            return existing

    if not number:
        return None

    reference_doctype, reference_name = _find_reference_by_number(number)

    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": number,
            "message": escape(msg.get("body") or ""),
            "message_id": message_id,
            "content_type": msg.get("content_type") or "text",
            "profile_name": msg.get("profile_name"),
            "whatsapp_account": whatsapp_account or "",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
        }
    )
    # The webhook was authenticated and resolved to an active account; external
    # senders cannot be given Desk roles just to preserve their messages.
    doc.insert(ignore_permissions=True)
    if doc.content_type in {"image", "document", "video", "audio"}:
        try:
            _save_incoming_media(doc, msg, whatsapp_account)
        except Exception:
            frappe.db.set_value("WhatsApp Message", doc.name, "last_error",
                                "Message received; attachment could not be downloaded. Check Error Log.")
            frappe.log_error(frappe.get_traceback(), "WhatsApp Incoming Media Failed")
    return doc.name


def _save_incoming_media(doc, msg, account):
    from whatsapp_evolution.utils import get_evolution_settings
    from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider
    from frappe.utils.file_manager import save_file
    encoded = msg.get("media_base64")
    mime = msg.get("media_mimetype") or "application/octet-stream"
    if not encoded:
        provider = EvolutionProvider(get_evolution_settings(account))
        response = requests.post(provider.api_base + "/chat/getBase64FromMediaMessage/" + quote(provider.instance, safe=""),
            headers=provider._headers(), json={"message": {"key": msg["media_key"]}, "convertToMp4": False},
            timeout=25, allow_redirects=False)
        response.raise_for_status()
        body = response.json()
        encoded = body.get("base64")
        mime = body.get("mimetype") or mime
    if not isinstance(encoded, str) or len(encoded) > 28_000_000:
        raise ValueError("Missing or oversized media")
    raw = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    if len(raw) > 20_000_000 or not raw:
        raise ValueError("Invalid media size")
    # Never serve active HTML/SVG content supplied by an external sender.
    allowed = mime in {"application/pdf", "application/octet-stream"} or mime.startswith(("image/", "audio/", "video/"))
    if not allowed or mime == "image/svg+xml":
        mime = "application/octet-stream"
    extension = mimetypes.guess_extension(mime) if mime != "application/octet-stream" else ".bin"
    filename = f"whatsapp-{doc.name}{extension or '.bin'}"
    file_doc = save_file(filename, raw, doc.doctype, doc.name, is_private=1)
    frappe.db.set_value(doc.doctype, doc.name, "attach", file_doc.file_url)
