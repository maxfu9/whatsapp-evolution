import frappe


def execute():
    if not frappe.db.exists("DocType", "WhatsApp Account"):
        return

    try:
        old_instance = frappe.db.get_single_value("WhatsApp Settings", "evolution_instance")
    except Exception:
        old_instance = None

    active_accounts = frappe.get_all(
        "WhatsApp Account",
        filters={"status": "Active"},
        fields=["name", "is_default", "evolution_instance"],
        order_by="modified asc",
    )

    if not active_accounts:
        return

    has_default = any(int(row.get("is_default") or 0) == 1 for row in active_accounts)

    for idx, row in enumerate(active_accounts):
        updates = {}
        if not row.get("evolution_instance") and old_instance:
            updates["evolution_instance"] = old_instance
        if not has_default and idx == 0:
            updates["is_default"] = 1
        if updates:
            frappe.db.set_value("WhatsApp Account", row.name, updates, update_modified=False)
