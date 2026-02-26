import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def setup_custom_fields():
    custom_fields = {
        "Contact Phone": [
            {
                "fieldname": "is_whatsapp_number",
                "label": "WhatsApp",
                "fieldtype": "Check",
                "insert_after": "is_primary_mobile_no",
                "default": 0,
                "in_list_view": 1,
            },
            {
                "fieldname": "is_notification_number",
                "label": "Notification",
                "fieldtype": "Check",
                "insert_after": "is_whatsapp_number",
                "default": 0,
                "in_list_view": 1,
            }
        ],
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
        ],
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
    }

    create_custom_fields(custom_fields, update=True)
