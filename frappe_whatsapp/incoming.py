import frappe


def handle_incoming_message(msg):
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": msg.get("from"),
            "message": msg.get("body"),
            "message_id": msg.get("message_id"),
            "content_type": "text",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
