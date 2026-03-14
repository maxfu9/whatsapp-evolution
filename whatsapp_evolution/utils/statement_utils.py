import frappe
from frappe import _
from frappe.utils import add_months, nowdate


def assert_statement_permission():
    if not frappe.has_permission("Process Statement Of Accounts", ptype="read"):
        frappe.throw(_("You are not permitted to send customer statements on WhatsApp."))


def get_default_company(customer):
    customer_meta = frappe.get_meta("Customer")
    if customer_meta and customer_meta.has_field("default_company"):
        company = frappe.db.get_value("Customer", customer, "default_company")
        if company:
            return company
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )


def get_default_outgoing_whatsapp_account():
    rows = frappe.get_all(
        "WhatsApp Account",
        filters={"is_default_outgoing": 1, "status": "Active"},
        fields=["name"],
        limit_page_length=1,
        ignore_permissions=True,
    )
    if rows:
        return rows[0].get("name")

    rows = frappe.get_all(
        "WhatsApp Account",
        filters={"is_default_outgoing": 1},
        fields=["name"],
        limit_page_length=1,
        ignore_permissions=True,
    )
    if rows:
        return rows[0].get("name")

    rows = frappe.get_all(
        "WhatsApp Account",
        filters={"status": "Active"},
        fields=["name"],
        limit_page_length=1,
        ignore_permissions=True,
    )
    if rows:
        return rows[0].get("name")

    rows = frappe.get_all(
        "WhatsApp Account",
        fields=["name"],
        limit_page_length=1,
        ignore_permissions=True,
    )
    return rows[0].get("name") if rows else None


def get_customer_mobile(customer):
    meta = frappe.get_meta("Customer")
    if meta and meta.has_field("mobile_no"):
        return frappe.db.get_value("Customer", customer, "mobile_no")
    return None


def build_statement_doc(customer, args):
    doc = frappe.new_doc("Process Statement Of Accounts")
    doc.company = args.get("company")
    doc.report = args.get("report") or "General Ledger"
    doc.set("customers", [])
    doc.append(
        "customers",
        {
            "customer": customer,
            "customer_name": frappe.db.get_value("Customer", customer, "customer_name") or customer,
        },
    )
    doc.currency = args.get("currency")
    doc.account = args.get("account")
    doc.letter_head = args.get("letter_head")
    doc.orientation = args.get("orientation") or "Portrait"
    doc.include_ageing = frappe.utils.cint(args.get("include_ageing"))
    doc.ageing_based_on = args.get("ageing_based_on") or "Due Date"
    doc.pdf_name = args.get("pdf_name") or f"{customer}-statement"
    doc.show_remarks = 1

    if doc.report == "Accounts Receivable":
        doc.posting_date = args.get("posting_date") or nowdate()
    else:
        doc.from_date = args.get("from_date") or add_months(nowdate(), -1)
        doc.to_date = args.get("to_date") or nowdate()

    return doc


def get_statement_pdf_bytes(customer, args):
    try:
        from erpnext.accounts.doctype.process_statement_of_accounts.process_statement_of_accounts import (
            get_report_pdf,
        )
    except Exception:
        frappe.throw(_("ERPNext is required to generate customer statements."))

    psoa = build_statement_doc(customer, args)
    report = get_report_pdf(psoa, consolidated=False)
    if isinstance(report, dict):
        pdf_bytes = report.get(customer)
        if pdf_bytes:
            return pdf_bytes

    report = get_report_pdf(psoa, consolidated=True)
    if isinstance(report, (bytes, bytearray)):
        return report

    frappe.throw(_("No statement data found for customer {0}.").format(customer))


def create_statement_file(customer, pdf_bytes, args):
    filename = (args.get("pdf_name") or f"{customer}-statement").strip() or f"{customer}-statement"
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"{filename}.pdf",
            "is_private": 1,
            "content": pdf_bytes,
            "attached_to_doctype": "Customer",
            "attached_to_name": customer,
        }
    )
    file_doc.insert(ignore_permissions=True)
    return file_doc.file_url


def validate_template_for_customer(template):
    if not template:
        return
    template_for_doctype = frappe.db.get_value("WhatsApp Templates", template, "for_doctype")
    if template_for_doctype and template_for_doctype != "Customer":
        frappe.throw(_("Selected template is not configured for Customer documents."))


def validate_manual_pdf(file_url):
    if not file_url:
        return
    if not file_url.lower().endswith(".pdf"):
        frappe.throw(_("Uploaded attachment must be a PDF file."))
