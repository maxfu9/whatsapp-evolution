from whatsapp_evolution.setup import setup_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Sales Invoice"):
        return

    setup_custom_fields()
