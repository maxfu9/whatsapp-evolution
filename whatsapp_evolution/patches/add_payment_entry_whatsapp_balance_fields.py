import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Payment Entry"):
        return

    create_custom_fields(
        {
            "Payment Entry": [
                {
                    "fieldname": "wa_balance_before_payment",
                    "label": "WA Balance Before Payment",
                    "fieldtype": "Currency",
                    "options": "party_account_currency",
                    "insert_after": "difference_amount",
                    "read_only": 1,
                    "no_copy": 1,
                    "print_hide": 1,
                },
                {
                    "fieldname": "wa_balance_after_payment",
                    "label": "WA Balance After Payment",
                    "fieldtype": "Currency",
                    "options": "party_account_currency",
                    "insert_after": "wa_balance_before_payment",
                    "read_only": 1,
                    "no_copy": 1,
                    "print_hide": 1,
                },
            ]
        },
        update=True,
    )
