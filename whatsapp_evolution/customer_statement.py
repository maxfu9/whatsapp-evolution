import frappe
from frappe import _
from frappe.utils import add_months, nowdate


def _assert_statement_permission():
    # Reuse ERPNext permission model: whoever can read Process Statement doc can use this action.
    if not frappe.has_permission("Process Statement Of Accounts", ptype="read"):
        frappe.throw(_("You are not permitted to send customer statements on WhatsApp."))


def _get_default_company(customer):
    customer_meta = frappe.get_meta("Customer")
    if customer_meta and customer_meta.has_field("default_company"):
        company = frappe.db.get_value("Customer", customer, "default_company")
        if company:
            return company
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )


def _build_statement_doc(customer, args):
    doc = frappe.new_doc("Process Statement Of Accounts")
    doc.company = args.get("company")
    doc.report = args.get("report") or "General Ledger"
    doc.customers = [{"customer": customer}]
    doc.currency = args.get("currency")
    doc.account = args.get("account")
    doc.letter_head = args.get("letter_head")
    doc.orientation = args.get("orientation") or "Portrait"
    doc.include_ageing = frappe.utils.cint(args.get("include_ageing"))
    doc.ageing_based_on = args.get("ageing_based_on") or "Due Date"
    doc.pdf_name = args.get("pdf_name") or f"{customer}-statement"

    if doc.report == "Accounts Receivable":
        doc.posting_date = args.get("posting_date") or nowdate()
    else:
        doc.from_date = args.get("from_date") or add_months(nowdate(), -1)
        doc.to_date = args.get("to_date") or nowdate()

    return doc


def _get_statement_pdf_bytes(customer, args):
    try:
        from erpnext.accounts.doctype.process_statement_of_accounts.process_statement_of_accounts import (
            get_report_pdf,
        )
    except Exception:
        frappe.throw(_("ERPNext is required to generate customer statements."))

    psoa = _build_statement_doc(customer, args)
    report = get_report_pdf(psoa, consolidated=False)
    if isinstance(report, dict):
        pdf_bytes = report.get(customer)
        if pdf_bytes:
            return pdf_bytes

    # Fallback for unexpected return format
    report = get_report_pdf(psoa, consolidated=True)
    if isinstance(report, (bytes, bytearray)):
        return report

    frappe.throw(_("No statement data found for customer {0}.").format(customer))


def _create_statement_file(customer, pdf_bytes, args):
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


@frappe.whitelist()
def get_customer_statement_defaults(customer):
    _assert_statement_permission()
    if not customer:
        frappe.throw(_("Customer is required"))

    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import (
        get_default_contact_and_whatsapp_number,
    )

    company = _get_default_company(customer)
    contact_defaults = get_default_contact_and_whatsapp_number("Customer", customer) or {}

    return {
        "company": company,
        "report": "General Ledger",
        "from_date": add_months(nowdate(), -1),
        "to_date": nowdate(),
        "posting_date": nowdate(),
        "include_ageing": 0,
        "ageing_based_on": "Due Date",
        "orientation": "Portrait",
        "contact": contact_defaults.get("contact"),
        "mobile_no": contact_defaults.get("mobile_no"),
    }


@frappe.whitelist()
def can_send_customer_statement():
    return bool(frappe.has_permission("Process Statement Of Accounts", ptype="read"))


@frappe.whitelist()
def send_customer_statement_whatsapp(
    customer,
    to,
    send_mode="Custom",
    template=None,
    message=None,
    custom_message=None,
    company=None,
    report="General Ledger",
    from_date=None,
    to_date=None,
    posting_date=None,
    include_ageing=0,
    ageing_based_on="Due Date",
    orientation="Portrait",
    currency=None,
    account=None,
    letter_head=None,
    pdf_name=None,
    whatsapp_account=None,
):
    _assert_statement_permission()
    if not customer:
        frappe.throw(_("Customer is required"))
    if not to:
        frappe.throw(_("Mobile number is required"))

    args = {
        "company": company or _get_default_company(customer),
        "report": report,
        "from_date": from_date,
        "to_date": to_date,
        "posting_date": posting_date,
        "include_ageing": include_ageing,
        "ageing_based_on": ageing_based_on,
        "orientation": orientation,
        "currency": currency,
        "account": account,
        "letter_head": letter_head,
        "pdf_name": pdf_name,
    }

    if not args["company"]:
        frappe.throw(_("Company is required to generate statement"))

    pdf_bytes = _get_statement_pdf_bytes(customer, args)
    file_url = _create_statement_file(customer, pdf_bytes, args)

    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import (
        send_custom,
        send_template,
    )

    if (send_mode or "").strip().lower() == "template":
        if not template:
            frappe.throw(_("Template is required for Template mode"))
        return send_template(
            to=to,
            reference_doctype="Customer",
            reference_name=customer,
            template=template,
            message=message or "",
            attach=file_url,
            whatsapp_account=whatsapp_account,
        )

    return send_custom(
        to=to,
        reference_doctype="Customer",
        reference_name=customer,
        message=custom_message or message or "",
        attach=file_url,
        content_type="document",
        whatsapp_account=whatsapp_account,
    )
