import frappe
from frappe.utils.password import set_encrypted_password, get_decrypted_password


def execute():
    return


def update_whatsapp_settings(account_name: str):
    """No-op in Evolution-only mode."""
    return


def get_old_settings_from_singles():
    """Read old WhatsApp Settings fields directly from the singles table.

    This bypasses the ORM since the field definitions may have been removed
    from the doctype schema, but the data might still exist in the database.
    Note: token is not included here as it's stored in __Auth table.
    """
    fields_to_migrate = [
        "phone_id",
        "business_id",
        "app_id",
        "url",
        "version",
        "webhook_verify_token",
        "enabled",
    ]

    result = frappe.db.sql(
        """
        SELECT field, value
        FROM `tabSingles`
        WHERE doctype = 'WhatsApp Settings'
        AND field IN %s
        """,
        (fields_to_migrate,),
        as_dict=True
    )

    if not result:
        return None

    return {row["field"]: row["value"] for row in result}


def update_whatsapp_templates(account_name: str):
    templates = frappe.get_all(
        "WhatsApp Templates",
        filters={"whatsapp_account": ""},
        fields=["name"]
    )
    for template in templates:
        frappe.db.set_value("WhatsApp Templates", template["name"], "whatsapp_account", account_name)
