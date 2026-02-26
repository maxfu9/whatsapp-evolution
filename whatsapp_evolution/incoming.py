import frappe


def handle_incoming_message(msg):
    # Find matching Contact or Customer for the incoming number
    number = msg.get("from")
    reference_doctype = None
    reference_name = None

    if number:
        # 1. Search in Contact Phone (Dynamic Link)
        contact_name = frappe.db.get_value(
            "Contact Phone", 
            {"phone": ["like", f"%{number}%"]}, 
            "parent"
        )
        if contact_name:
            reference_doctype = "Contact"
            reference_name = contact_name
        else:
            # 2. Search in common mobile fields across Customer/Lead/Supplier
            for dt in ["Customer", "Lead", "Supplier"]:
                party_name = frappe.db.get_value(
                    dt, 
                    {"mobile_no": ["like", f"%{number}%"]}, 
                    "name"
                )
                if party_name:
                    reference_doctype = dt
                    reference_name = party_name
                    break

    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": msg.get("from"),
            "message": msg.get("body"),
            "message_id": msg.get("message_id"),
            "content_type": "text",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
