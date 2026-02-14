import frappe

from frappe_whatsapp.utils import get_whatsapp_account


def handle_incoming_message(msg):
    whatsapp_account = get_whatsapp_account(account_type="incoming")

    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": msg.get("from"),
            "message": msg.get("body"),
            "message_id": msg.get("message_id"),
            "content_type": "text",
            "whatsapp_account": whatsapp_account.name if whatsapp_account else None,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
