import frappe


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _numbers_match(a, b):
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    return da.endswith(db[-10:]) or db.endswith(da[-10:])


def _find_reference_by_number(number):
    rows = frappe.get_all(
        "Contact Phone",
        filters={"is_whatsapp_number": 1},
        fields=["parent", "phone", "is_primary_mobile_no"],
        limit_page_length=2000,
    )
    for row in rows:
        if row.get("is_primary_mobile_no") and _numbers_match(row.get("phone"), number):
            return "Contact", row.get("parent")

    for dt in ("Customer", "Lead", "Supplier", "Employee"):
        party_rows = frappe.get_all(
            dt,
            fields=["name", "mobile_no"],
            filters={"mobile_no": ["is", "set"]},
            limit_page_length=2000,
        )
        for row in party_rows:
            if _numbers_match(row.get("mobile_no"), number):
                return dt, row.get("name")

    return None, None


def handle_incoming_message(msg):
    number = (msg.get("from") or "").strip()
    message_id = (msg.get("message_id") or "").strip()

    if message_id:
        existing = frappe.db.get_value("WhatsApp Message", {"message_id": message_id}, "name")
        if existing:
            return existing

    if not number:
        return None

    reference_doctype, reference_name = _find_reference_by_number(number)

    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "type": "Incoming",
            "from": number,
            "message": msg.get("body"),
            "message_id": message_id,
            "content_type": "text",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name
