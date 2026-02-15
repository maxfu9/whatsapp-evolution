import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Contact Phone"):
        return

    create_custom_fields(
        {
            "Contact Phone": [
                {
                    "fieldname": "is_whatsapp_number",
                    "label": "WhatsApp",
                    "fieldtype": "Check",
                    "insert_after": "is_primary_mobile_no",
                    "default": 0,
                    "in_list_view": 1,
                }
            ]
        },
        update=True,
    )
