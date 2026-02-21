import frappe
from frappe.utils import flt


def _get_customer_ledger_after(doc):
    if doc.doctype != "Sales Invoice":
        return 0.0
    if not doc.get("customer") or not doc.get("company") or not doc.get("debit_to"):
        return 0.0

    try:
        from erpnext.accounts.utils import get_balance_on
    except Exception:
        return 0.0

    posting_date = doc.get("posting_date") or frappe.utils.nowdate()
    try:
        return flt(
            get_balance_on(
                account=doc.get("debit_to"),
                date=posting_date,
                party_type="Customer",
                party=doc.get("customer"),
                company=doc.get("company"),
            )
        )
    except TypeError:
        return flt(get_balance_on(account=doc.get("debit_to"), date=posting_date))
    except Exception:
        return 0.0


def _get_invoice_effect_amount(doc):
    amount = flt(doc.get("rounded_total")) if doc.get("rounded_total") is not None else 0.0
    if not amount:
        amount = flt(doc.get("grand_total"))
    return amount


def update_sales_invoice_whatsapp_balances(doc, event=None):
    """Persist customer ledger balances on Sales Invoice for WhatsApp templates."""
    if doc.doctype != "Sales Invoice":
        return
    if doc.get("docstatus") == 2:
        return

    after_balance = _get_customer_ledger_after(doc)
    invoice_effect = _get_invoice_effect_amount(doc)

    if doc.get("docstatus") == 1:
        before_balance = after_balance - invoice_effect
    else:
        before_balance = after_balance
        after_balance = before_balance + invoice_effect

    doc.wa_balance_before_invoice = before_balance
    doc.wa_balance_after_invoice = after_balance

    if doc.get("name") and doc.get("docstatus") == 1:
        frappe.db.set_value(
            "Sales Invoice",
            doc.name,
            {
                "wa_balance_before_invoice": before_balance,
                "wa_balance_after_invoice": after_balance,
            },
            update_modified=False,
        )
