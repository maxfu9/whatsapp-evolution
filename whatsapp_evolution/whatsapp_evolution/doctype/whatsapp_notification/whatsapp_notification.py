"""Notification."""

import json
import re
import frappe
from time import sleep

from frappe import _dict, _
from frappe.model.document import Document
from frappe.utils.safe_exec import get_safe_globals, safe_exec
from frappe.integrations.utils import make_post_request  # Backward-compat for legacy tests that patch this symbol.
from frappe.desk.form.utils import get_pdf_link
from frappe.utils import add_to_date, nowdate, datetime
from frappe.utils.synchronization import filelock
from frappe.utils.file_lock import LockTimeoutError

from whatsapp_evolution.utils import (
    get_whatsapp_account,
    format_number,
    get_evolution_settings,
    is_evolution_enabled,
)
from whatsapp_evolution.whatsapp_evolution.providers import EvolutionProvider


LEDGER_BALANCE_ALIASES = {"ledger_balance", "_ledger_balance", "ledger balance"}
ITEMS_TEXT_ALIASES = {"custom_wa_items", "wa_items", "items_list", "invoice_items_list"}


def _is_evolution_enabled(whatsapp_account=None):
    return is_evolution_enabled(whatsapp_account=whatsapp_account)


def _extract_body_params(template_data):
    params = []
    components = (template_data or {}).get("components") or []
    for component in components:
        if component.get("type") == "body":
            for p in component.get("parameters") or []:
                if p.get("type") == "text":
                    params.append(str(p.get("text") or ""))
    return params


def _render_template_text(template_text, params):
    text = template_text or ""
    for idx, value in enumerate(params, start=1):
        text = text.replace(f"{{{{{idx}}}}}", str(value or ""))
        text = text.replace(f"{{{{ {idx} }}}}", str(value or ""))
    return text


def _extract_response_message_id(response):
    if not isinstance(response, dict):
        return ""
    msg_id = response.get("id") or response.get("message_id")
    if msg_id:
        return str(msg_id)
    key = response.get("key") or {}
    if isinstance(key, dict) and key.get("id"):
        return str(key.get("id"))
    messages = response.get("messages") or []
    if isinstance(messages, list) and messages:
        first = messages[0] or {}
        if isinstance(first, dict) and first.get("id"):
            return str(first.get("id"))
    return ""


def _doc_value(doc_data, key):
    if isinstance(doc_data, dict):
        return doc_data.get(key)
    return getattr(doc_data, key, None)


def _should_retry_on_default_account(error_message):
    text = (error_message or "").lower()
    return (
        "does not exist" in text
        or "sessionerror" in text
        or "no sessions" in text
    )


def _resolve_print_format(doctype, explicit_print_format=None):
    fmt = (explicit_print_format or "").strip()
    if fmt:
        return fmt
    try:
        meta = frappe.get_meta(doctype)
        if meta and meta.default_print_format:
            return meta.default_print_format
    except Exception:
        pass
    return "Standard"


def _looks_like_phone(value):
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    normalized = text.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if normalized.startswith("+"):
        normalized = normalized[1:]
    return normalized.isdigit() and len(normalized) >= 8


def _normalize_phone(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[^\d+]", "", text)
    if text.startswith("+"):
        text = text[1:]
    return text


def _split_candidate_numbers(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = re.split(r"[,;\n|]+", str(value))

    numbers = []
    for raw in raw_values:
        candidate = _normalize_phone(raw)
        if _looks_like_phone(candidate):
            numbers.append(candidate)
    return numbers


def _dedupe_numbers(numbers):
    seen = set()
    out = []
    for number in numbers or []:
        normalized = _normalize_phone(number)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _contact_has_whatsapp_tick():
    meta = frappe.get_meta("Contact Phone")
    for field in ("is_whatsapp_number", "is_whatsapp", "whatsapp"):
        if meta.get_field(field):
            return field
    return None


def _get_contact_numbers(contact_name):
    if not contact_name or not frappe.db.exists("Contact", contact_name):
        return []

    contact = frappe.get_doc("Contact", contact_name)
    phone_rows = contact.get("phone_nos") or []
    tick_field = _contact_has_whatsapp_tick()

    numbers = []
    if tick_field:
        for row in phone_rows:
            if frappe.utils.cint(row.get(tick_field)):
                numbers.extend(_split_candidate_numbers(row.get("phone")))
        return _dedupe_numbers(numbers)

    for row in phone_rows:
        numbers.extend(_split_candidate_numbers(row.get("phone")))

    if not numbers:
        numbers.extend(_split_candidate_numbers(contact.get("mobile_no")))
        numbers.extend(_split_candidate_numbers(contact.get("phone")))

    return _dedupe_numbers(numbers)


def _get_dynamic_link_contact_numbers(link_doctype, link_name):
    if not link_doctype or not link_name:
        return []

    contact_names = frappe.get_all(
        "Dynamic Link",
        filters={
            "link_doctype": link_doctype,
            "link_name": link_name,
            "parenttype": "Contact",
        },
        pluck="parent",
    )
    numbers = []
    for contact_name in contact_names:
        numbers.extend(_get_contact_numbers(contact_name))
    return _dedupe_numbers(numbers)


def _get_employee_cell_numbers(employee_name):
    if not employee_name:
        return []
    if not frappe.db.exists("DocType", "Employee"):
        return []
    if not frappe.db.exists("Employee", employee_name):
        return []
    cell_number = frappe.db.get_value("Employee", employee_name, "cell_number")
    return _dedupe_numbers(_split_candidate_numbers(cell_number))


def _insert_notification_log(template, error=None, response=None):
    meta = {"error": error} if error else {"response": response or {}}
    frappe.get_doc(
        {
            "doctype": "WhatsApp Notification Log",
            "template": template,
            "meta_data": meta,
        }
    ).insert(ignore_permissions=True)


def _get_ledger_balance_value(doc):
    try:
        from erpnext.accounts.utils import get_balance_on
    except Exception:
        return None

    account = None
    party_type = doc.get("party_type")
    party = doc.get("party")
    company = doc.get("company")
    posting_date = doc.get("posting_date") or doc.get("transaction_date") or nowdate()
    payment_type = doc.get("payment_type")

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

    # Generic fallback for party-ledger doctypes (Customer/Supplier/etc)
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

    currency = doc.get("party_account_currency") or doc.get("paid_from_account_currency") or doc.get("paid_to_account_currency") or doc.get("currency")
    try:
        return frappe.utils.fmt_money(balance, currency=currency) if currency else frappe.utils.fmt_money(balance)
    except Exception:
        return str(balance)


def _resolve_template_param_value(doc, fieldname):
    fieldname = (fieldname or "").strip()
    if not fieldname:
        return ""

    if fieldname.lower() in LEDGER_BALANCE_ALIASES:
        value = _get_ledger_balance_value(doc)
        if value is not None:
            return value

    if fieldname.lower() in ITEMS_TEXT_ALIASES:
        value = _get_items_text_value(doc)
        if value is not None:
            return value

    try:
        return doc.get_formatted(fieldname)
    except Exception:
        value = doc.get(fieldname)
        if value is None:
            return ""
        if isinstance(value, (datetime.date, datetime.datetime)):
            return str(value)
        return str(value)


def _get_items_text_value(doc):
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
            f"   *تعداد:* {qty:g} {uom} × *قیمت:* {frappe.utils.fmt_money(rate)} = *کل:* {frappe.utils.fmt_money(amount)}"
        )
    chunks.append("---------------------------")
    return "\n".join(chunks)


def _was_recently_sent(reference_doctype, reference_name, to_number, template_name, seconds=90):
    if not (reference_doctype and reference_name and to_number):
        return False
    rows = frappe.db.sql(
        """
        select name
        from `tabWhatsApp Message`
        where reference_doctype=%s
          and reference_name=%s
          and `to`=%s
          and ifnull(template, '')=%s
          and creation >= (now() - interval %s second)
          and ifnull(status, '') in ('Queued', 'Started', 'Success')
        limit 1
        """,
        (reference_doctype, reference_name, to_number, template_name or "", seconds),
        as_dict=True,
    )
    return bool(rows)


def _notification_dedup_key(notification_name, reference_doctype, reference_name, to_number, template_name):
    return (
        f"wa_notif_dedup:{notification_name}:{reference_doctype}:{reference_name}:"
        f"{to_number}:{template_name}"
    )


def _acquire_notification_dedup(notification_name, reference_doctype, reference_name, to_number, template_name, ttl=180):
    key = _notification_dedup_key(notification_name, reference_doctype, reference_name, to_number, template_name)
    cache = frappe.cache()
    if cache.get_value(key):
        return False
    cache.set_value(key, 1, expires_in_sec=ttl)
    return True


class WhatsAppNotification(Document):
    """Notification."""

    def validate(self):
        """Validate."""
        if self.notification_type == "DocType Event" and self.field_name:
            fields = frappe.get_doc("DocType", self.reference_doctype).fields
            fields += frappe.get_all(
                "Custom Field",
                filters={"dt": self.reference_doctype},
                fields=["fieldname"]
            )
            if not any(field.fieldname == self.field_name for field in fields): # noqa
                frappe.throw(_("Field name {0} does not exists").format(self.field_name))
        if self.custom_attachment:
            if not self.attach and not self.attach_from_field:
                frappe.throw(_("Either {0} a file or add a {1} to send attachemt").format(
                    frappe.bold(_("Attach")),
                    frappe.bold(_("Attach from field")),
                ))

        if self.set_property_after_alert:
            meta = frappe.get_meta(self.reference_doctype)
            if not meta.get_field(self.set_property_after_alert):
                frappe.throw(_("Field {0} not found on DocType {1}").format(
                    self.set_property_after_alert,
                    self.reference_doctype,
                ))

    def after_insert(self):
        """Refresh cached notification map after create."""
        frappe.cache().delete_value("whatsapp_notification_map")

    def on_update(self):
        """Refresh cached notification map after update."""
        frappe.cache().delete_value("whatsapp_notification_map")


    def send_scheduled_message(self) -> dict:
        """Specific to API endpoint Server Scripts."""
        safe_exec(
            self.condition, get_safe_globals(), dict(doc=self)
        )

        template = frappe.db.get_value(
            "WhatsApp Templates", self.template,
            fieldname='*'
        )

        if template and template.language_code:
            if self.get("_contact_list"):
                # send simple template without a doc to get field data.
                self.send_simple_template(template)
            elif self.get("_data_list"):
                # allow send a dynamic template using schedule event config
                # _doc_list shoud be [{"name": "xxx", "phone_no": "123"}]
                for data in self._data_list:
                    doc = frappe.get_doc(self.reference_doctype, data.get("name"))

                    self.send_template_message(doc, data.get("phone_no"), template, True)
        # return _globals.frappe.flags


    def send_simple_template(self, template):
        """ send simple template without a doc to get field data """
        for contact in self._contact_list:
            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(contact),
                "type": "template",
                "template": {
                    "name": template.actual_name,
                    "language": {
                        "code": template.language_code
                    },
                    "components": []
                }
            }
            self.content_type = template.get("header_type", "text").lower()
            self.notify(data, template_account=template.get("whatsapp_account"))


    def send_template_message(
        self,
        doc: Document,
        phone_no=None,
        default_template=None,
        ignore_condition=False,
        from_queue=False,
    ):
        """Specific to Document Event triggered Server Scripts."""
        if self.disabled:
            return

        doc_data = doc.as_dict()
        if self.condition and not ignore_condition:
            # check if condition satisfies
            if not frappe.safe_eval(
                self.condition, get_safe_globals(), dict(doc=doc_data)
            ):
                return

        delay_seconds = frappe.utils.cint(self.get("delay_seconds") or 0)
        if delay_seconds > 0 and not from_queue:
            frappe.enqueue(
                "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.send_template_message_job",
                queue="short",
                enqueue_after_commit=True,
                notification_name=self.name,
                reference_doctype=doc_data.get("doctype"),
                reference_name=doc_data.get("name"),
                phone_no=phone_no,
                default_template_name=getattr(default_template, "name", None),
                ignore_condition=ignore_condition,
                delay_seconds=delay_seconds,
            )
            return

        template = default_template or frappe.get_doc("WhatsApp Templates", self.template)

        if template:
            recipient_numbers = self.get_recipient_numbers(doc, doc_data, phone_no)
            if not recipient_numbers:
                _insert_notification_log(
                    self.template,
                    error=(
                        f"No recipient number resolved for {doc_data.get('doctype')} {doc_data.get('name')}. "
                        f"Field: {self.field_name or 'N/A'}. "
                        "If Contact Phone has WhatsApp tick field, ensure at least one number is checked."
                    ),
                )
                return
            parameters = []
            if self.fields:
                for field in self.fields:
                    value = _resolve_template_param_value(doc, field.field_name)
                    parameters.append({
                        "type": "text",
                        "text": value
                    })

            attachment_url = ""
            attachment_filename = ""
            if self.attach_document_print:
                    key = doc.get_document_share_key()  # noqa
                    frappe.db.commit()
                    print_format = _resolve_print_format(doc_data["doctype"])
                    link = get_pdf_link(
                        doc_data["doctype"],
                        doc_data["name"],
                        print_format=print_format,
                    )

                    attachment_filename = f'{doc_data["name"]}.pdf'
                    attachment_url = f"{frappe.utils.get_url()}{link}&key={key}"

            elif self.custom_attachment:
                    attachment_filename = self.file_name

                    if self.attach_from_field:
                        file_url = doc_data[self.attach_from_field]
                        if not file_url.startswith("http"):
                            # get share key so that private files can be sent
                            key = doc.get_document_share_key()
                            file_url = f"{frappe.utils.get_url()}{file_url}&key={key}"
                    else:
                        file_url = self.attach

                    if file_url.startswith("http"):
                        attachment_url = f"{file_url}"
                    else:
                        attachment_url = f"{frappe.utils.get_url()}{file_url}"

            for phone_number in recipient_numbers:
                formatted_to = self.format_number(phone_number)
                lock_key = (
                    f"wa_notif:{self.name}:{doc_data.get('doctype')}:{doc_data.get('name')}:"
                    f"{formatted_to}:{template.name}"
                )
                try:
                    with filelock(lock_key, timeout=10):
                        if not _acquire_notification_dedup(
                            notification_name=self.name,
                            reference_doctype=doc_data.get("doctype"),
                            reference_name=doc_data.get("name"),
                            to_number=formatted_to,
                            template_name=template.name,
                            ttl=180,
                        ):
                            continue

                        if _was_recently_sent(
                            reference_doctype=doc_data.get("doctype"),
                            reference_name=doc_data.get("name"),
                            to_number=formatted_to,
                            template_name=template.name,
                            seconds=120,
                        ):
                            continue
                except LockTimeoutError:
                    continue

                data = {
                    "messaging_product": "whatsapp",
                    "to": formatted_to,
                    "type": "template",
                    "template": {
                        "name": template.actual_name,
                        "language": {
                            "code": template.language_code
                        },
                        "components": []
                    }
                }

                if parameters:
                    data["template"]["components"].append(
                        {
                            "type": "body",
                            "parameters": parameters
                        }
                    )

                if template.header_type == "DOCUMENT" and attachment_url:
                    data["template"]["components"].append(
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "document",
                                    "document": {
                                        "link": attachment_url,
                                        "filename": attachment_filename,
                                    },
                                }
                            ],
                        }
                    )
                elif template.header_type == "IMAGE" and attachment_url:
                    data["template"]["components"].append(
                        {
                            "type": "header",
                            "parameters": [
                                {
                                    "type": "image",
                                    "image": {
                                        "link": attachment_url,
                                    },
                                }
                            ],
                        }
                    )
                self.content_type = template.header_type.lower() if template.header_type else None

                if template.buttons:
                    button_fields = self.button_fields.split(",") if self.button_fields else []
                    for idx, btn in enumerate(template.buttons):
                        if btn.button_type == "Visit Website" and btn.url_type == "Dynamic":
                            if button_fields:
                                data["template"]["components"].append(
                                    {
                                        "type": "button",
                                        "sub_type": "url",
                                        "index": str(idx),
                                        "parameters": [
                                            {"type": "text", "text": doc.get(button_fields.pop(0))}
                                        ],
                                    }
                                )

                self.notify(data, doc_data, template_account=template.whatsapp_account)

    def get_recipient_numbers(self, doc, doc_data, phone_no=None):
        numbers = []
        if phone_no:
            numbers.extend(_split_candidate_numbers(phone_no))

        if self.field_name:
            value = doc_data.get(self.field_name)
            numbers.extend(_split_candidate_numbers(value))
            numbers.extend(_get_contact_numbers(value))
            numbers.extend(_get_employee_cell_numbers(value))

        for field in ("contact_mobile", "mobile_no", "mobile", "phone", "contact_phone"):
            numbers.extend(_split_candidate_numbers(doc_data.get(field)))

        party_type = doc_data.get("party_type")
        party = doc_data.get("party")
        if party_type and party:
            numbers.extend(_get_dynamic_link_contact_numbers(party_type, party))
            if party_type == "Employee":
                numbers.extend(_get_employee_cell_numbers(party))

        for linked_dt_field in ("customer", "supplier", "lead", "prospect"):
            linked_name = doc_data.get(linked_dt_field)
            if linked_name:
                linked_dt = linked_dt_field.title()
                numbers.extend(_get_dynamic_link_contact_numbers(linked_dt, linked_name))

        for employee_field in ("employee", "employee_name"):
            employee_name = doc_data.get(employee_field)
            if employee_name:
                numbers.extend(_get_employee_cell_numbers(employee_name))

        return _dedupe_numbers(numbers)

    def notify(self, data, doc_data=None, template_account=None):
        """Notify."""
        default_account = get_whatsapp_account(account_type="outgoing")
        default_account_name = default_account.name if default_account else None
        effective_account = template_account or default_account_name

        if effective_account and default_account_name and effective_account != default_account_name:
            instance = frappe.db.get_value("WhatsApp Account", effective_account, "evolution_instance")
            if not instance or str(instance).strip().lower() == "erpnext":
                effective_account = default_account_name

        def _send_with_account(account_name):
            if not _is_evolution_enabled(account_name):
                frappe.throw(
                    _("Evolution API is required. Configure Evolution on WhatsApp Account / WhatsApp Settings.")
                )

            settings = get_evolution_settings(account_name)
            provider = EvolutionProvider(settings)

            to_number = format_number(data.get("to"))
            template_doc = frappe.get_doc("WhatsApp Templates", self.template)
            template_text = (template_doc.get("template") or template_doc.get("template_message") or "").strip()
            params = _extract_body_params(data.get("template"))
            rendered_text = _render_template_text(template_text, params)

            media_url = ""
            media_type = "document"
            media_bytes = None
            media_name = None
            components = (data.get("template") or {}).get("components") or []
            for component in components:
                if component.get("type") != "header":
                    continue
                header_params = component.get("parameters") or []
                if not header_params:
                    continue
                hp = header_params[0]
                if hp.get("type") == "document":
                    media_type = "document"
                    media_url = ((hp.get("document") or {}).get("link") or "").strip()
                    media_name = ((hp.get("document") or {}).get("filename") or "").strip()
                elif hp.get("type") == "image":
                    media_type = "image"
                    media_url = ((hp.get("image") or {}).get("link") or "").strip()

            if self.attach_document_print and doc_data:
                try:
                    ref_doctype = _doc_value(doc_data, "doctype")
                    ref_name = _doc_value(doc_data, "name")
                    key = frappe.get_doc(ref_doctype, ref_name).get_document_share_key()
                    default_print_format = _resolve_print_format(ref_doctype)
                    link = get_pdf_link(ref_doctype, ref_name, print_format=default_print_format)
                    media_url = f"{frappe.utils.get_url()}{link}&key={key}"
                    pdf = frappe.attach_print(ref_doctype, ref_name, print_format=default_print_format)
                    media_bytes = pdf.get("fcontent")
                    media_name = pdf.get("fname")
                    media_type = "document"
                except Exception:
                    media_bytes = None
                    media_name = media_name or None

            if media_url or media_bytes:
                response = provider.send_media(
                    to_number=to_number,
                    media_url=media_url,
                    media_type=media_type,
                    caption=rendered_text,
                    media_bytes=media_bytes,
                    filename=media_name,
                )
                content_type = media_type
            else:
                response = provider.send_message(to_number, rendered_text)
                content_type = "text"

            params_json = frappe.json.dumps(params, default=str) if params else None
            new_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "message": rendered_text,
                "to": data.get("to"),
                "message_type": "Manual",
                "message_id": _extract_response_message_id(response) or f"evo-log-{frappe.generate_hash(length=8)}",
                "content_type": content_type,
                "use_template": 1,
                "template": self.template,
                "template_parameters": params_json,
                "attach": media_url if (media_url or media_bytes) else "",
            }
            if doc_data:
                new_doc.update(
                    {
                        "reference_doctype": _doc_value(doc_data, "doctype"),
                        "reference_name": _doc_value(doc_data, "name"),
                    }
                )

            msg_doc = frappe.get_doc(new_doc)
            msg_doc.flags.skip_send = True
            msg_doc.save(ignore_permissions=True)
            return response

        success = False
        error_message = None
        response = {}
        try:
            response = _send_with_account(effective_account)
            if doc_data and self.set_property_after_alert and self.property_value:
                if _doc_value(doc_data, "doctype") and _doc_value(doc_data, "name"):
                    fieldname = self.set_property_after_alert
                    value = self.property_value
                    meta = frappe.get_meta(_doc_value(doc_data, "doctype"))
                    df = meta.get_field(fieldname)
                    if df and df.fieldtype in frappe.model.numeric_fieldtypes:
                        value = frappe.utils.cint(value)
                    if df:
                        frappe.db.set_value(_doc_value(doc_data, "doctype"), _doc_value(doc_data, "name"), fieldname, value)
            success = True
        except Exception as e:
            error_message = str(e)
            if (
                default_account_name
                and effective_account != default_account_name
                and _should_retry_on_default_account(error_message)
            ):
                try:
                    response = _send_with_account(default_account_name)
                    success = True
                    error_message = None
                except Exception as e2:
                    error_message = str(e2)
        finally:
            meta = {"error": error_message} if not success else {"response": response}
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": self.template,
                    "meta_data": meta,
                }
            ).insert(ignore_permissions=True)


    def on_trash(self):
        """On delete remove from schedule."""
        frappe.cache().delete_value("whatsapp_notification_map")


    def format_number(self, number):
        """Format number."""
        if not number:
            return number
        if (number.startswith("+")):
            number = number[1:len(number)]

        return number

    def get_documents_for_today(self):
        """get list of documents that will be triggered today"""
        docs = []

        diff_days = self.days_in_advance
        if self.doctype_event == "Days After":
            diff_days = -diff_days

        reference_date = add_to_date(nowdate(), days=diff_days)
        reference_date_start = reference_date + " 00:00:00.000000"
        reference_date_end = reference_date + " 23:59:59.000000"

        doc_list = frappe.get_all(
            self.reference_doctype,
            fields="name",
            filters=[
                {self.date_changed: (">=", reference_date_start)},
                {self.date_changed: ("<=", reference_date_end)},
            ],
        )

        for d in doc_list:
            doc = frappe.get_doc(self.reference_doctype, d.name)
            self.send_template_message(doc)
            # print(doc.name)


@frappe.whitelist()
def call_trigger_notifications():
    """Trigger notifications."""
    try:
        # Directly call the trigger_notifications function
        trigger_notifications()  
    except Exception as e:
        # Log the error but do not show any popup or alert
        frappe.log_error(frappe.get_traceback(), "Error in call_trigger_notifications")
        # Optionally, you could raise the exception to be handled elsewhere if needed
        raise e

def trigger_notifications(method="daily"):
    if frappe.flags.in_import or frappe.flags.in_patch:
        # don't send notifications while syncing or patching
        return

    if method == "daily":
        doc_list = frappe.get_all(
            "WhatsApp Notification", filters={"doctype_event": ("in", ("Days Before", "Days After")), "disabled": 0}
        )
        for d in doc_list:
            alert = frappe.get_doc("WhatsApp Notification", d.name)
            alert.get_documents_for_today()


def send_template_message_job(
    notification_name: str,
    reference_doctype: str,
    reference_name: str,
    phone_no: str | None = None,
    default_template_name: str | None = None,
    ignore_condition: bool = False,
    delay_seconds: int = 0,
    **kwargs,
):
    """Background worker for delayed WhatsApp notification sends."""
    # Ignore extra enqueue metadata keys (e.g. track_job) for compatibility.
    _ = kwargs

    if delay_seconds and delay_seconds > 0:
        sleep(delay_seconds)

    notification = frappe.get_doc("WhatsApp Notification", notification_name)
    reference_doc = frappe.get_doc(reference_doctype, reference_name)
    default_template = (
        frappe.get_doc("WhatsApp Templates", default_template_name)
        if default_template_name else None
    )
    notification.send_template_message(
        reference_doc,
        phone_no=phone_no,
        default_template=default_template,
        ignore_condition=ignore_condition,
        from_queue=True,
    )
           
