import json

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


APP_NAME = "whatsapp_evolution"
MODULE_NAME = "WhatsApp Evolution"
MANAGER_ROLE = "WhatsApp Manager"


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
        ],
        "Communication": [
            {
                "fieldname": "link_doctype",
                "label": "Link DocType",
                "fieldtype": "Link",
                "options": "DocType",
                "insert_after": "reference_name",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "link_name",
                "label": "Link Name",
                "fieldtype": "Dynamic Link",
                "options": "link_doctype",
                "insert_after": "link_doctype",
                "read_only": 1,
                "print_hide": 1,
            }
        ]
    }

    # In CI or frappe-only installs, ERPNext doctypes (e.g. Sales Invoice,
    # Payment Entry) may not exist yet. Skip missing doctypes safely.
    existing_custom_fields = {}
    for doctype, fields in custom_fields.items():
        if frappe.db.exists("DocType", doctype):
            existing_custom_fields[doctype] = fields

    if existing_custom_fields:
        create_custom_fields(existing_custom_fields, update=True)
    add_whatsapp_communication_medium()
    setup_v16_desk()


def _save_standard_doc(doctype, name, values):
    values = dict(values)
    values.update({"doctype": doctype, "name": name})
    if frappe.db.exists(doctype, name):
        doc = frappe.get_doc(doctype, name)
        doc.update(values)
    else:
        doc = frappe.get_doc(values)

    # These are application-owned standard Desk records installed during migrate,
    # so there is no interactive user permission context to honor here.
    doc.save(ignore_permissions=True)
    return doc


def _ensure_role(role_name):
    if frappe.db.exists("Role", role_name):
        return
    _save_standard_doc(
        "Role",
        role_name,
        {
            "role_name": role_name,
            "desk_access": 1,
            "disabled": 0,
        },
    )


def _ensure_workspace():
    content = [
        {"id": "wa_hdr", "type": "header", "data": {"text": '<span class="h4"><b>WhatsApp Evolution</b></span>', "col": 12}},
        {"id": "wa_msg", "type": "card", "data": {"card_name": "Messaging", "col": 4}},
        {"id": "wa_cfg", "type": "card", "data": {"card_name": "Configuration", "col": 4}},
        {"id": "wa_rpt", "type": "card", "data": {"card_name": "Reports", "col": 4}},
    ]
    links = [
        {"type": "Card Break", "label": "Messaging", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 0},
        {"type": "Link", "label": "WhatsApp Message", "link_to": "WhatsApp Message", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Link", "label": "Bulk WhatsApp Message", "link_to": "Bulk WhatsApp Message", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Card Break", "label": "Configuration", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 0},
        {"type": "Link", "label": "WhatsApp Account", "link_to": "WhatsApp Account", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Link", "label": "WhatsApp Settings", "link_to": "WhatsApp Settings", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Link", "label": "WhatsApp Templates", "link_to": "WhatsApp Templates", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Link", "label": "WhatsApp Recipient List", "link_to": "WhatsApp Recipient List", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 1},
        {"type": "Card Break", "label": "Reports", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 0},
        {"type": "Link", "label": "Bulk WhatsApp Status", "link_to": "Bulk WhatsApp Status", "link_type": "Report", "hidden": 0, "is_query_report": 1, "link_count": 0, "onboard": 0},
        {"type": "Link", "label": "WhatsApp Notification Log", "link_to": "WhatsApp Notification Log", "link_type": "DocType", "hidden": 0, "is_query_report": 0, "link_count": 0, "onboard": 0},
    ]
    _save_standard_doc(
        "Workspace",
        MODULE_NAME,
        {
            "label": MODULE_NAME,
            "title": MODULE_NAME,
            "module": MODULE_NAME,
            "app": APP_NAME,
            "type": "Workspace",
            "icon": "message-circle",
            "public": 1,
            "is_hidden": 0,
            "content": json.dumps(content),
            "links": links,
            "roles": [{"role": "System Manager"}, {"role": MANAGER_ROLE}],
        },
    )


def _ensure_workspace_sidebar():
    items = [
        {"type": "Link", "label": MODULE_NAME, "link_to": MODULE_NAME, "link_type": "Workspace", "icon": "message-circle", "indent": 0, "child": 0, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Message", "link_to": "WhatsApp Message", "link_type": "DocType", "icon": "message-square", "indent": 0, "child": 0, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "Bulk WhatsApp Message", "link_to": "Bulk WhatsApp Message", "link_type": "DocType", "icon": "send", "indent": 0, "child": 0, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Section Break", "label": "Configuration", "link_type": "DocType", "icon": "settings", "indent": 1, "child": 0, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Account", "link_to": "WhatsApp Account", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Settings", "link_to": "WhatsApp Settings", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Templates", "link_to": "WhatsApp Templates", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Recipient List", "link_to": "WhatsApp Recipient List", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Profiles", "link_to": "WhatsApp Profiles", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Section Break", "label": "Logs & Reports", "link_type": "DocType", "icon": "file-text", "indent": 1, "child": 0, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Notification", "link_to": "WhatsApp Notification", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "WhatsApp Notification Log", "link_to": "WhatsApp Notification Log", "link_type": "DocType", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
        {"type": "Link", "label": "Bulk WhatsApp Status", "link_to": "Bulk WhatsApp Status", "link_type": "Report", "indent": 0, "child": 1, "collapsible": 1, "keep_closed": 0, "show_arrow": 0},
    ]
    _save_standard_doc(
        "Workspace Sidebar",
        MODULE_NAME,
        {
            "title": MODULE_NAME,
            "module": MODULE_NAME,
            "app": APP_NAME,
            "standard": 1,
            "header_icon": "message-circle",
            "items": items,
        },
    )


def _ensure_desktop_icon():
    _save_standard_doc(
        "Desktop Icon",
        MODULE_NAME,
        {
            "label": MODULE_NAME,
            "standard": 1,
            "app": APP_NAME,
            "icon_type": "App",
            "link_type": "External",
            "link": "/desk/whatsapp-evolution",
            "hidden": 0,
            "bg_color": "blue",
            "roles": [{"role": "System Manager"}, {"role": MANAGER_ROLE}],
        },
    )


def setup_v16_desk():
    """Install v16 Desktop Icon, Workspace, and Sidebar records."""
    for doctype in ("Role", "Workspace", "Workspace Sidebar", "Desktop Icon"):
        if not frappe.db.table_exists(doctype):
            return

    _ensure_role(MANAGER_ROLE)
    _ensure_workspace()
    _ensure_workspace_sidebar()
    _ensure_desktop_icon()

def add_whatsapp_communication_medium():
    # Communication Medium
    options = frappe.get_meta('Communication').get_field('communication_medium').options
    if "WhatsApp" not in options:
        make_property_setter(
            'Communication', 
            'communication_medium', 
            'options', 
            options + '\nWhatsApp', 
            'Select'
        )
    
    # Delivery Status
    ds_options = frappe.get_meta('Communication').get_field('delivery_status').options
    if "Delivered" not in ds_options:
        make_property_setter(
            'Communication', 
            'delivery_status', 
            'options', 
            ds_options + '\nDelivered', 
            'Select'
        )
