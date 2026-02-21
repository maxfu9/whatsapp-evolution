import frappe


OBSOLETE_DOCTYPES = (
    "WhatsApp Flow",
    "WhatsApp Flow Field",
    "WhatsApp Flow Screen",
)


def execute():
    for doctype in OBSOLETE_DOCTYPES:
        if frappe.db.exists("DocType", doctype):
            try:
                frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
            except Exception:
                frappe.db.sql("delete from `tabDocType` where name=%s", doctype)

        frappe.db.sql("drop table if exists `tab{}`".format(doctype))
        frappe.clear_document_cache("DocType", doctype)

