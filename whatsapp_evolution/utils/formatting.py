import frappe


def format_amount_no_symbol(value):
    amount = frappe.utils.flt(value or 0)
    return f"{amount:,.2f}"
