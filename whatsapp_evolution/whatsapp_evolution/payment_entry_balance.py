import frappe
from frappe.utils import flt


def _get_party_ledger_after(doc):
    if doc.doctype != "Payment Entry":
        return 0.0
    if not doc.get("party_type") or not doc.get("party") or not doc.get("company"):
        return 0.0

    try:
        from erpnext.accounts.utils import get_balance_on
    except Exception:
        return 0.0

    posting_date = doc.get("posting_date") or frappe.utils.nowdate()

    account = None
    if doc.get("party_type") == "Customer":
        account = doc.get("paid_from")
    elif doc.get("party_type") == "Supplier":
        account = doc.get("paid_to")

    if not account:
        account = doc.get("party_account")

    if not account:
        return 0.0

    try:
        return flt(
            get_balance_on(
                account=account,
                date=posting_date,
                party_type=doc.get("party_type"),
                party=doc.get("party"),
                company=doc.get("company"),
            )
        )
    except TypeError:
        return flt(get_balance_on(account=account, date=posting_date))
    except Exception:
        return 0.0


def _get_payment_effect_amount(doc):
    effect = flt(doc.get("total_allocated_amount"))
    if not effect:
        effect = flt(doc.get("paid_amount")) or flt(doc.get("received_amount"))
    return effect


def _get_outstanding_delta(doc):
    party_type = doc.get("party_type")
    payment_type = doc.get("payment_type")
    effect = _get_payment_effect_amount(doc)

    if not effect or party_type not in ("Customer", "Supplier"):
        return 0.0

    # Delta means: after_balance = before_balance + delta
    if party_type == "Customer":
        if payment_type == "Receive":
            return -effect
        if payment_type == "Pay":
            return effect
    elif party_type == "Supplier":
        if payment_type == "Pay":
            return effect
        if payment_type == "Receive":
            return -effect

    return 0.0


def update_payment_entry_whatsapp_balances(doc, event=None):
    """Persist party balances on Payment Entry for WhatsApp templates."""
    if doc.doctype != "Payment Entry":
        return
    if doc.get("docstatus") == 2:
        return

    delta = _get_outstanding_delta(doc)
    current_balance = _get_party_ledger_after(doc)

    if doc.get("docstatus") == 1:
        after_balance = current_balance
        before_balance = after_balance - delta
    else:
        before_balance = current_balance
        after_balance = before_balance + delta

    doc.wa_balance_before_payment = before_balance
    doc.wa_balance_after_payment = after_balance

    if doc.get("name") and doc.get("docstatus") == 1:
        frappe.db.set_value(
            "Payment Entry",
            doc.name,
            {
                "wa_balance_before_payment": before_balance,
                "wa_balance_after_payment": after_balance,
            },
            update_modified=False,
        )
