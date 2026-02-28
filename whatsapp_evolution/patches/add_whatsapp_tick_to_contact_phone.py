from whatsapp_evolution.setup import setup_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Contact Phone"):
        return

    setup_custom_fields()
