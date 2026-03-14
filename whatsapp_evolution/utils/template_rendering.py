import json
import re
import frappe

from whatsapp_evolution.utils.formatting import format_amount_no_symbol
from whatsapp_evolution.utils.template_helpers import get_items_text_value, get_ledger_balance_value


LEDGER_BALANCE_ALIASES = {"ledger_balance", "_ledger_balance", "ledger balance"}
ITEMS_TEXT_ALIASES = {"custom_wa_items", "wa_items", "items_list", "invoice_items_list"}


def parse_body_param(body_param):
    if not body_param:
        return []
    try:
        parsed = json.loads(body_param) if isinstance(body_param, str) else body_param
    except Exception:
        return []
    if isinstance(parsed, dict):
        return [
            str(v or "")
            for _, v in sorted(
                parsed.items(),
                key=lambda x: int(str(x[0])) if str(x[0]).isdigit() else str(x[0]),
            )
        ]
    if isinstance(parsed, list):
        return [str(v or "") for v in parsed]
    return []


def extract_body_params(template_data):
    params = []
    components = (template_data or {}).get("components") or []
    for component in components:
        if component.get("type") == "body":
            for p in component.get("parameters") or []:
                if p.get("type") == "text":
                    params.append(str(p.get("text") or ""))
    return params


def render_numeric_placeholders(template_text, params):
    rendered = template_text or ""
    for idx, value in enumerate(params, start=1):
        rendered = re.sub(r"{{\s*" + str(idx) + r"\s*}}", str(value or ""), rendered)
    return rendered


def render_named_placeholders(text, ref_doc):
    if not text:
        return ""

    def _replace(match):
        key = (match.group(1) or "").strip()
        if not key or key.isdigit():
            return match.group(0)
        return resolve_template_value(ref_doc, key)

    return re.sub(r"{{\s*([^{}]+?)\s*}}", _replace, text)


def resolve_template_value(ref_doc, field_name):
    key = (field_name or "").strip()
    if not key:
        return ""
    if key.lower() in LEDGER_BALANCE_ALIASES:
        value = get_ledger_balance_value(ref_doc)
        if value is not None:
            return str(value)
    if key.lower() in ITEMS_TEXT_ALIASES:
        value = get_items_text_value(ref_doc)
        if value is not None:
            return str(value)
    try:
        meta = frappe.get_meta(ref_doc.doctype)
        df = meta.get_field(key) if meta else None
        if df and df.fieldtype == "Currency":
            raw_value = ref_doc.get(key)
            if raw_value in (None, ""):
                return ""
            return format_amount_no_symbol(raw_value)
    except Exception:
        pass
    try:
        return str(ref_doc.get_formatted(key) or "")
    except Exception:
        value = ref_doc.get(key)
        return str(value or "")
