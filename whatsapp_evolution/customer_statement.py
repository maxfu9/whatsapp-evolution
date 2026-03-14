import frappe
from frappe import _
from whatsapp_evolution.utils.statement_utils import (
    assert_statement_permission,
    get_default_company,
    get_default_outgoing_whatsapp_account,
    get_customer_mobile,
    get_statement_pdf_bytes,
    create_statement_file,
    validate_template_for_customer,
    validate_manual_pdf,
)


@frappe.whitelist()
def get_customer_statement_defaults(customer):
    assert_statement_permission()
    if not customer:
        frappe.throw(_("Customer is required"))

    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import (
        get_default_contact_and_whatsapp_number,
    )

    company = get_default_company(customer)
    contact_defaults = get_default_contact_and_whatsapp_number("Customer", customer) or {}
    mobile_no = contact_defaults.get("mobile_no")
    if not mobile_no:
        mobile_no = get_customer_mobile(customer)

    return {
        "company": company,
        "report": "General Ledger",
        "from_date": frappe.utils.add_months(frappe.utils.nowdate(), -1),
        "to_date": frappe.utils.nowdate(),
        "posting_date": frappe.utils.nowdate(),
        "include_ageing": 0,
        "ageing_based_on": "Due Date",
        "orientation": "Portrait",
        "contact": contact_defaults.get("contact"),
        "mobile_no": mobile_no,
        "whatsapp_account": get_default_outgoing_whatsapp_account(),
        "attach_pdf": 0,
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
    attach_pdf=1,
    manual_attach=None,
    whatsapp_account=None,
):
    assert_statement_permission()
    if not customer:
        frappe.throw(_("Customer is required"))
    if not to:
        frappe.throw(_("Mobile number is required"))

    args = {
        "company": company or get_default_company(customer),
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

    file_url = ""
    if manual_attach:
        validate_manual_pdf(manual_attach)
        file_url = manual_attach
    elif frappe.utils.cint(attach_pdf):
        pdf_bytes = get_statement_pdf_bytes(customer, args)
        file_url = create_statement_file(customer, pdf_bytes, args)

    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import (
        send_custom,
        send_template,
    )

    if (send_mode or "").strip().lower() == "template":
        if not template:
            frappe.throw(_("Template is required for Template mode"))
        validate_template_for_customer(template)
        return send_template(
            to=to,
            reference_doctype="Customer",
            reference_name=customer,
            template=template,
            message=message or "",
            attach=file_url or "",
            whatsapp_account=whatsapp_account,
        )

    return send_custom(
        to=to,
        reference_doctype="Customer",
        reference_name=customer,
        message=custom_message or message or "",
        attach=file_url or "",
        content_type="document" if file_url else "text",
        whatsapp_account=whatsapp_account,
    )
