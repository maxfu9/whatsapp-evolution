import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Sales Invoice"):
        return

    create_custom_fields(
        {
            "Sales Invoice": [
                {
                    "fieldname": "wa_balance_before_invoice",
                    "label": "WA Balance Before Invoice",
                    "fieldtype": "Currency",
                    "options": "currency",
                    "insert_after": "rounded_total",
                    "read_only": 1,
                    "no_copy": 1,
                    "print_hide": 1,
                },
                {
                    "fieldname": "wa_balance_after_invoice",
                    "label": "WA Balance After Invoice",
                    "fieldtype": "Currency",
                    "options": "currency",
                    "insert_after": "wa_balance_before_invoice",
                    "read_only": 1,
                    "no_copy": 1,
                    "print_hide": 1,
                },
            ]
        },
        update=True,
    )
