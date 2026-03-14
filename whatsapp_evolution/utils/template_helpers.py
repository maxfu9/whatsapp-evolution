import frappe

from whatsapp_evolution.utils.formatting import format_amount_no_symbol


def get_ledger_balance_value(doc):
    try:
        from erpnext.accounts.utils import get_balance_on
    except Exception:
        return None

    account = None
    party_type = doc.get("party_type")
    party = doc.get("party")
    company = (
        doc.get("company")
        or doc.get("default_company")
        or frappe.defaults.get_user_default("Company")
        or frappe.db.get_single_value("Global Defaults", "default_company")
    )
    posting_date = doc.get("posting_date") or doc.get("transaction_date") or frappe.utils.nowdate()
    payment_type = doc.get("payment_type")

    if doc.doctype in ("Customer", "Supplier", "Employee"):
        party_type = doc.doctype
        party = doc.get("name")
        if company and party:
            try:
                from erpnext.accounts.party import get_party_account
                account = get_party_account(party_type, party, company)
            except Exception:
                account = None

    if doc.doctype == "Payment Entry":
        if payment_type == "Receive":
            account = doc.get("paid_from")
        elif payment_type == "Pay":
            account = doc.get("paid_to")
        else:
            account = doc.get("paid_from") or doc.get("paid_to")

    if not account:
        for fieldname in ("account", "debit_to", "credit_to", "paid_from", "paid_to"):
            account = doc.get(fieldname)
            if account:
                break

    if not account and party_type and party:
        try:
            from erpnext.accounts.party import get_party_account
            account = get_party_account(party_type, party, company)
        except Exception:
            account = None

    if not account:
        return None

    try:
        balance = get_balance_on(
            account=account,
            date=posting_date,
            party_type=party_type,
            party=party,
            company=company,
        )
    except TypeError:
        balance = get_balance_on(account=account, date=posting_date)
    except Exception:
        return None

    try:
        return format_amount_no_symbol(balance)
    except Exception:
        return str(balance)


def get_items_text_value(doc):
    if doc.doctype not in ("Sales Invoice", "Purchase Invoice"):
        return None

    rows = doc.get("items") or []
    if not rows:
        return ""

    chunks = []
    for row in rows:
        item_name = row.get("item_name") or row.get("item_code") or "-"
        qty = frappe.utils.flt(row.get("qty") or 0)
        uom = row.get("uom") or ""
        rate = frappe.utils.flt(row.get("rate") or 0)
        amount = frappe.utils.flt(row.get("amount") or 0)
        chunks.append(
            "---------------------------\n"
            f"🔹 *نام:* {item_name}\n"
            f"   *تعداد:* {qty:g} {uom} × *قیمت:* {format_amount_no_symbol(rate)} = *کل:* {format_amount_no_symbol(amount)}"
        )
    chunks.append("---------------------------")
    return "\n".join(chunks)
