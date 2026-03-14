# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import re
from html import escape
from urllib.parse import urlparse, parse_qs
import requests
import frappe
from frappe import _, throw
from frappe.model.document import Document
from frappe.desk.search import sanitize_searchfield
from frappe.integrations.utils import make_post_request  # Backward-compat for legacy tests that patch this symbol.
from frappe.utils.file_manager import get_file

from whatsapp_evolution.utils import (
    get_whatsapp_account,
    format_number,
    get_evolution_settings,
    is_evolution_enabled,
)
from whatsapp_evolution.utils.template_rendering import (
    parse_body_param,
    render_numeric_placeholders,
    render_named_placeholders,
    resolve_template_value,
)
from whatsapp_evolution.whatsapp_evolution.providers import EvolutionProvider


ENTITY_LABEL_DOCTYPES = {"Customer", "Supplier", "User", "Employee", "Contact", "Lead", "Prospect"}


def _get_template_text(template_doc):
    return (template_doc.get("template_message") or template_doc.get("template") or "").strip()


def _get_entity_display_name(doctype, docname):
    if not doctype or not docname:
        return ""
    if not frappe.db.exists(doctype, docname):
        return str(docname)

    try:
        meta = frappe.get_meta(doctype)
    except Exception:
        return str(docname)

    title_field = (meta.title_field or "").strip()
    if title_field:
        value = frappe.db.get_value(doctype, docname, title_field)
        if value:
            return str(value)

    fallback_fields = {
        "Customer": ("customer_name",),
        "Supplier": ("supplier_name",),
        "User": ("full_name", "username"),
        "Employee": ("employee_name",),
        "Contact": ("full_name", "first_name"),
        "Lead": ("lead_name",),
        "Prospect": ("company_name", "prospect_name"),
    }
    for fieldname in fallback_fields.get(doctype, ()):
        value = frappe.db.get_value(doctype, docname, fieldname)
        if value:
            return str(value)

    if doctype == "Contact":
        first_name, last_name = frappe.db.get_value(doctype, docname, ["first_name", "last_name"]) or (None, None)
        full = " ".join([p for p in [first_name, last_name] if p]).strip()
        if full:
            return full

    return str(docname)


def _build_reference_label(reference_doctype, reference_name):
    if not reference_doctype or not reference_name:
        return ""

    if reference_doctype in ENTITY_LABEL_DOCTYPES:
        return f"{reference_doctype}: {_get_entity_display_name(reference_doctype, reference_name)}"

    try:
        meta = frappe.get_meta(reference_doctype)
    except Exception:
        return f"{reference_doctype}: {reference_name}"

    # Preferred link fields for common business documents.
    for fieldname in (
        "customer",
        "supplier",
        "employee",
        "user",
        "contact",
        "contact_person",
        "party",
        "lead",
        "prospect",
        "owner",
    ):
        df = meta.get_field(fieldname)
        if not df:
            continue
        value = frappe.db.get_value(reference_doctype, reference_name, fieldname)
        if not value:
            continue
        if df.fieldtype == "Link" and df.options in ENTITY_LABEL_DOCTYPES:
            display = _get_entity_display_name(df.options, value)
            return f"{df.options}: {display}"
        if fieldname == "party":
            party_type = frappe.db.get_value(reference_doctype, reference_name, "party_type")
            if party_type in ENTITY_LABEL_DOCTYPES:
                display = _get_entity_display_name(party_type, value)
                return f"{party_type}: {display}"
        if fieldname == "owner":
            return f"User: {_get_entity_display_name('User', value)}"
        return f"{fieldname.replace('_', ' ').title()}: {value}"

    return f"{reference_doctype}: {reference_name}"


def _contact_display_name(contact_name):
    if not contact_name or not frappe.db.exists("Contact", contact_name):
        return ""
    full_name = frappe.db.get_value("Contact", contact_name, "full_name")
    if full_name:
        return str(full_name)
    first_name, last_name = frappe.db.get_value("Contact", contact_name, ["first_name", "last_name"]) or (None, None)
    fallback = " ".join([part for part in [first_name, last_name] if part]).strip()
    return fallback or str(contact_name)


def _find_linked_contact_name(reference_doctype, reference_name, phone_number=None):
    links, direct_contacts = _collect_reference_links(reference_doctype, reference_name)

    contact_names = []
    if reference_doctype == "Contact" and frappe.db.exists("Contact", reference_name):
        contact_names.append(reference_name)

    for cname in direct_contacts:
        if cname and cname not in contact_names:
            contact_names.append(cname)

    for link_doctype, link_name in sorted(links):
        linked_contacts = frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": link_doctype,
                "link_name": link_name,
                "parenttype": "Contact",
            },
            pluck="parent",
        )
        for cname in linked_contacts:
            if cname not in contact_names:
                contact_names.append(cname)

    if not contact_names:
        return ""

    normalized_target = re.sub(r"\D", "", str(phone_number or ""))

    for contact_name in contact_names:
        if not normalized_target:
            return contact_name

        contact = frappe.get_doc("Contact", contact_name)
        candidates = []
        candidates.extend([contact.get("mobile_no"), contact.get("phone")])
        for row in contact.get("phone_nos") or []:
            candidates.append(row.get("phone"))

        for candidate in candidates:
            normalized_candidate = re.sub(r"\D", "", str(candidate or ""))
            if normalized_candidate and normalized_candidate == normalized_target:
                return contact_name

    # No exact phone match, still return first linked contact for context.
    return contact_names[0]


def _extract_response_message_id(response):
    if not isinstance(response, dict):
        return ""

    def _pick_id(payload):
        if not isinstance(payload, dict):
            return ""
        msg_id = payload.get("id") or payload.get("message_id")
        if msg_id:
            return str(msg_id)

        key = payload.get("key") or {}
        if isinstance(key, dict) and key.get("id"):
            return str(key.get("id"))

        status_data = payload.get("status") or {}
        if isinstance(status_data, dict):
            nested_key = status_data.get("key") or {}
            if isinstance(nested_key, dict) and nested_key.get("id"):
                return str(nested_key.get("id"))

        messages = payload.get("messages") or []
        if isinstance(messages, list) and messages:
            first = messages[0] or {}
            if isinstance(first, dict):
                mid = first.get("id") or ((first.get("key") or {}).get("id") if isinstance(first.get("key"), dict) else None)
                if mid:
                    return str(mid)

        return ""

    direct = _pick_id(response)
    if direct:
        return direct

    data = response.get("data")
    if isinstance(data, list):
        for item in data:
            nested = _pick_id(item)
            if nested:
                return nested
    elif isinstance(data, dict):
        nested = _pick_id(data)
        if nested:
            return nested

    return ""


def _is_evolution_enabled_global():
    return is_evolution_enabled()


def _resolve_outgoing_account_name(preferred_account=None):
    if preferred_account and frappe.db.exists("WhatsApp Account", preferred_account):
        return preferred_account
    outgoing = get_whatsapp_account(account_type="outgoing")
    if outgoing:
        return outgoing.name
    fallback = get_whatsapp_account()
    return fallback.name if fallback else None


def _resolve_evolution_account(preferred_account=None, template_account=None):
    candidates = []
    for candidate in (preferred_account, template_account):
        if candidate and candidate not in candidates and frappe.db.exists("WhatsApp Account", candidate):
            candidates.append(candidate)

    outgoing = get_whatsapp_account(account_type="outgoing")
    if outgoing and outgoing.name not in candidates:
        candidates.append(outgoing.name)

    fallback = get_whatsapp_account()
    if fallback and fallback.name not in candidates:
        candidates.append(fallback.name)

    for account_name in candidates:
        try:
            if is_evolution_enabled(whatsapp_account=account_name):
                return account_name
        except Exception:
            continue

    # Prefer accounts that have recently sent successfully.
    recent_success_accounts = frappe.get_all(
        "WhatsApp Message",
        filters={"type": "Outgoing", "status": "Success"},
        fields=["whatsapp_account"],
        order_by="modified desc",
        limit_page_length=50,
    )
    for row in recent_success_accounts:
        account_name = (row.get("whatsapp_account") or "").strip()
        if not account_name or account_name in candidates:
            continue
        if not frappe.db.exists("WhatsApp Account", account_name):
            continue
        try:
            if is_evolution_enabled(whatsapp_account=account_name):
                return account_name
        except Exception:
            continue

    # Last-resort fallback: pick any active account that is Evolution-enabled.
    active_accounts = frappe.get_all("WhatsApp Account", filters={"status": "Active"}, pluck="name")
    for account_name in active_accounts:
        if account_name in candidates:
            continue
        try:
            if is_evolution_enabled(whatsapp_account=account_name):
                return account_name
        except Exception:
            continue

    # Return first resolvable account for clearer error messages downstream.
    return candidates[0] if candidates else None


def _resolve_print_format(doctype_name, selected_print_format=None):
    selected = (selected_print_format or "").strip()
    if selected:
        return selected

    default_format = None
    if doctype_name:
        default_format = frappe.db.get_value("DocType", doctype_name, "default_print_format")
        if not default_format:
            default_format = frappe.db.get_value(
                "Property Setter",
                filters={
                    "doc_type": doctype_name,
                    "property": "default_print_format",
                },
                fieldname="value",
            )
    return default_format or "Standard"




def _normalized_attachment_identity(attach):
    if not attach:
        return ""
    attach = str(attach).strip()
    if "download_pdf" not in attach:
        return attach
    try:
        parsed = urlparse(attach)
        qs = parse_qs(parsed.query or "")
        doctype = (qs.get("doctype") or [""])[0]
        name = (qs.get("name") or [""])[0]
        fmt = (qs.get("format") or [""])[0]
        letterhead = (qs.get("no_letterhead") or [""])[0]
        return f"download_pdf:{doctype}:{name}:{fmt}:{letterhead}"
    except Exception:
        return attach


def _extract_print_format_from_attach(attach):
    if not attach or "download_pdf" not in str(attach):
        return None
    try:
        parsed = urlparse(str(attach))
        qs = parse_qs(parsed.query or "")
        return ((qs.get("format") or [None])[0] or "").strip() or None
    except Exception:
        return None


def _outgoing_dedup_key(doc):
    return "|".join(
        [
            str(doc.get("to") or ""),
            str(doc.get("content_type") or ""),
            str(doc.get("message") or ""),
            _normalized_attachment_identity(doc.get("attach")),
            str(doc.get("template") or ""),
            str(doc.get("reference_doctype") or ""),
            str(doc.get("reference_name") or ""),
        ]
    )


def _acquire_outgoing_dedup(doc, ttl=60):
    key = f"wa_msg_dedup:{_outgoing_dedup_key(doc)}"
    cache = frappe.cache()
    if cache.get_value(key):
        return False
    cache.set_value(key, 1, expires_in_sec=ttl)
    return True


def _create_queue_placeholder(
    to,
    reference_doctype,
    reference_name,
    message_type,
    content_type="text",
    template=None,
    message=None,
    attach=None,
    whatsapp_account=None,
):
    doc = frappe.get_doc(
        {
            "doctype": "WhatsApp Message",
            "to": to,
            "type": "Outgoing",
            "message_type": message_type,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content_type": content_type or "text",
            "template": template,
            "message": message or "",
            "attach": attach or "",
            "whatsapp_account": whatsapp_account or "",
            "status": "Queued",
            "message_id": f"queue-{frappe.generate_hash(length=8)}",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _update_queue_status(name, status, message_id=None, details=None):
    if not name or not frappe.db.exists("WhatsApp Message", name):
        return
    values = {"status": status}
    if message_id:
        values["message_id"] = message_id
    if details:
        values["message"] = details
    frappe.db.set_value("WhatsApp Message", name, values, update_modified=True)


def _recent_duplicate_exists(
    *,
    reference_doctype,
    reference_name,
    to_number,
    content_type,
    message,
    attach,
    template=None,
    exclude_name=None,
    seconds=120,
):
    rows = frappe.db.sql(
        """
        select name
        from `tabWhatsApp Message`
        where type='Outgoing'
          and ifnull(reference_doctype,'')=%s
          and ifnull(reference_name,'')=%s
          and ifnull(`to`,'')=%s
          and ifnull(content_type,'')=%s
          and ifnull(message,'')=%s
          and ifnull(attach,'')=%s
          and ifnull(template,'')=%s
          and ifnull(status,'') in ('Queued', 'Started', 'Success')
          and creation >= (now() - interval %s second)
          and (%s is null or name != %s)
        limit 1
        """,
        (
            reference_doctype or "",
            reference_name or "",
            to_number or "",
            content_type or "",
            message or "",
            attach or "",
            template or "",
            seconds,
            exclude_name,
            exclude_name,
        ),
    )
    return bool(rows)


class WhatsAppMessage(Document):
    def autoname(self):
        self.set_label()
        base = (self.label or self.reference_name or self.to or self.get("from") or "whatsapp-message").strip()
        slug = re.sub(r"[^a-z0-9]+", "-", frappe.scrub(base).lower()).strip("-")
        if not slug:
            slug = "whatsapp-message"
        slug = slug[:60]

        for _ in range(6):
            candidate = f"{slug}-{frappe.generate_hash(length=6)}"
            if not frappe.db.exists("WhatsApp Message", candidate):
                self.name = candidate
                return

        self.name = f"{slug}-{frappe.generate_hash(length=10)}"

    def _allow_attachment_link_fallback(self):
        mode = (frappe.db.get_single_value("WhatsApp Settings", "attachment_delivery_mode") or "").strip()
        if not mode:
            return False
        return mode.lower() == "fallback to link"

    def is_evolution_enabled(self):
        return is_evolution_enabled(self.whatsapp_account)

    def validate(self):
        self.set_whatsapp_account()
        self.set_label()

    def set_label(self):
        if (self.label or "").strip():
            return

        if self.reference_doctype and self.reference_name:
            number_for_match = self.to if self.type == "Outgoing" else self.get("from")
            contact_name = _find_linked_contact_name(
                self.reference_doctype,
                self.reference_name,
                number_for_match,
            )
            contact_display = _contact_display_name(contact_name)
            if contact_display:
                self.label = f"Contact: {contact_display}"
                return

        reference_label = _build_reference_label(self.reference_doctype, self.reference_name)
        if reference_label:
            self.label = reference_label
            return

        if self.type == "Incoming":
            from_number = format_number(self.get("from") or "")
            if self.profile_name and from_number:
                self.label = f"{self.profile_name} ({from_number})"
            elif self.profile_name:
                self.label = self.profile_name
            elif from_number:
                self.label = f"Phone: {from_number}"
            return

        to_number = format_number(self.to or "")
        if to_number:
            self.label = f"Phone: {to_number}"

    def after_insert(self):
        # Timeline entries are rendered directly from WhatsApp Message docs.
        return

    def on_update(self):
        self.update_profile_name()
        return

    def update_profile_name(self):
        number = self.get("from")
        if not number:
            return
        from_number = format_number(number)

        if (
            self.has_value_changed("profile_name")
            and self.profile_name
            and from_number
            and frappe.db.exists("WhatsApp Profiles", {"number": from_number})
        ):
            profile_id = frappe.get_value("WhatsApp Profiles", {"number": from_number}, "name")
            frappe.db.set_value("WhatsApp Profiles", profile_id, "profile_name", self.profile_name)

    def create_whatsapp_profile(self):
        number = format_number(self.get("from") or self.to)
        if not number:
            return
        if not frappe.db.exists("WhatsApp Profiles", {"number": number}):
            frappe.get_doc({
                "doctype": "WhatsApp Profiles",
                "profile_name": self.profile_name,
                "number": number,
                "whatsapp_account": self.whatsapp_account
            }).insert(ignore_permissions=True)

    def set_whatsapp_account(self):
        """Set whatsapp account to default if missing"""
        if self.is_evolution_enabled() and not self.whatsapp_account:
            account = get_whatsapp_account(account_type="outgoing")
            if account:
                self.whatsapp_account = account.name
            return

        if not self.whatsapp_account:
            account_type = 'outgoing' if self.type == 'Outgoing' else 'incoming'
            default_whatsapp_account = get_whatsapp_account(account_type=account_type)
            if not default_whatsapp_account:
                throw(_("Please set a default outgoing WhatsApp Account or Select available WhatsApp Account"))
            else:
                self.whatsapp_account = default_whatsapp_account.name

    """Send whats app messages."""
    def before_insert(self):
        """Send message."""
        if getattr(self.flags, "skip_send", False):
            if not self.status:
                self.status = "Success"
            return

        self.set_whatsapp_account()
        if self.type == "Outgoing" and self.message_type != "Template" and not self.message_id:
            if not _acquire_outgoing_dedup(self, ttl=60):
                self.status = "Skipped"
                self.message_id = "dedup-skip"
                self.create_whatsapp_profile()
                return

            if self.attach and not self.attach.startswith("http"):
                link = frappe.utils.get_url() + "/" + self.attach
            else:
                link = self.attach

            data = {
                "messaging_product": "whatsapp",
                "to": format_number(self.to),
                "type": self.content_type,
            }
            if self.is_reply and self.reply_to_message_id:
                data["context"] = {"message_id": self.reply_to_message_id}
            if self.content_type in ["document", "image", "video"]:
                data[self.content_type.lower()] = {
                    "link": link,
                    "caption": self.message,
                }
            elif self.content_type == "reaction":
                data["reaction"] = {
                    "message_id": self.reply_to_message_id,
                    "emoji": self.message,
                }
            elif self.content_type == "text":
                data["text"] = {"preview_url": True, "body": self.message}

            elif self.content_type == "audio":
                data["audio"] = {"link": link}

            elif self.content_type == "interactive":
                # Interactive message (buttons or list)
                data["type"] = "interactive"
                buttons_data = json.loads(self.buttons) if isinstance(self.buttons, str) else self.buttons

                if isinstance(buttons_data, list) and len(buttons_data) > 3:
                    # Use list message for more than 3 options (max 10)
                    data["interactive"] = {
                        "type": "list",
                        "body": {"text": self.message},
                        "action": {
                            "button": "Select Option",
                            "sections": [{
                                "title": "Options",
                                "rows": [
                                    {"id": btn["id"], "title": btn["title"], "description": btn.get("description", "")}
                                    for btn in buttons_data[:10]
                                ]
                            }]
                        }
                    }
                else:
                    # Use button message for 3 or fewer options
                    data["interactive"] = {
                        "type": "button",
                        "body": {"text": self.message},
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {"id": btn["id"], "title": btn["title"]}
                                }
                                for btn in buttons_data[:3]
                            ]
                        }
                    }

            try:
                self.notify(data)
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.log_error(frappe.get_traceback(), "WhatsApp Send Failed")
                frappe.throw(_("Failed to send message. Please check the error logs."))
        elif self.type == "Outgoing" and self.message_type == "Template" and not self.message_id:
            self.send_template()

        self.create_whatsapp_profile()

    def send_template(self):
        """Send template."""
        if not self.to:
            frappe.throw(_("Mobile number is required before sending template."))

        template = frappe.get_doc("WhatsApp Templates", self.template)
        data = {
            "messaging_product": "whatsapp",
            "to": format_number(self.to),
            "type": "template",
            "template": {
                "name": template.actual_name or template.template_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        template_text = _get_template_text(template)
        placeholder_matches = re.findall(r"{{\s*(\d+)\s*}}", template_text)
        if template.sample_values:
            field_names = template.field_names.split(",") if template.field_names else template.sample_values.split(",")
            parameters = []
            template_parameters = []

            if self.body_param is not None:
                params = parse_body_param(self.body_param)
                for param in params:
                    parameters.append({"type": "text", "text": param})
                    template_parameters.append(param)
            elif self.flags.custom_ref_doc:
                custom_values = self.flags.custom_ref_doc
                for field_name in field_names:
                    value = custom_values.get(field_name.strip())
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)                    

            else:
                ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                for field_name in field_names:
                    value = resolve_template_value(ref_doc, field_name)
                    parameters.append({"type": "text", "text": value})
                    template_parameters.append(value)

            self.template_parameters = json.dumps(template_parameters)
            data["template"]["components"].append(
                {
                    "type": "body",
                    "parameters": parameters,
                }
            )
        elif placeholder_matches:
            ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
            fallback_values = [
                ref_doc.get("customer_name") or ref_doc.get("contact_display") or "",
                ref_doc.get("name") or "",
            ]
            parameters = []
            template_parameters = []
            for index in sorted({int(match) for match in placeholder_matches}):
                value = fallback_values[index - 1] if index - 1 < len(fallback_values) else ""
                value = str(value or "")
                parameters.append({"type": "text", "text": value})
                template_parameters.append(value)

            if parameters:
                self.template_parameters = json.dumps(template_parameters)
                data["template"]["components"].append(
                    {
                        "type": "body",
                        "parameters": parameters,
                    }
                )

        if template.header_type:
            if self.attach:
                if template.header_type == 'IMAGE':

                    if self.attach.startswith("http"):
                        url = f'{self.attach}'
                    else:
                        url = f'{frappe.utils.get_url()}{self.attach}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

            elif template.sample:
                if template.header_type == 'IMAGE':
                    if template.sample.startswith("http"):
                        url = f'{template.sample}'
                    else:
                        url = f'{frappe.utils.get_url()}{template.sample}'
                    data['template']['components'].append({
                        "type": "header",
                        "parameters": [{
                            "type": "image",
                            "image": {
                                "link": url
                            }
                        }]
                    })

        if template.buttons:
            button_parameters = []
            for idx, btn in enumerate(template.buttons):
                if btn.button_type == "Quick Reply":
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": str(idx),
                        "parameters": [{"type": "payload", "payload": btn.button_label}]
                    })
                elif btn.button_type == "Call Phone":
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "phone_number",
                        "index": str(idx),
                        "parameters": [{"type": "text", "text": btn.phone_number}]
                    })
                elif btn.button_type == "Visit Website":
                    url = btn.website_url
                    if btn.url_type == "Dynamic":
                        ref_doc = frappe.get_doc(self.reference_doctype, self.reference_name)
                        url = ref_doc.get_formatted(btn.website_url)
                    button_parameters.append({
                        "type": "button",
                        "sub_type": "url",
                        "index": str(idx),
                        "parameters": [{"type": "text", "text": url}]
                    })

            if button_parameters:
                data['template']['components'].extend(button_parameters)

        self.notify(data)

    def notify(self, data):
        """Notify."""
        if not self.is_evolution_enabled():
            frappe.throw(
                _("Evolution API is required. Configure Evolution on WhatsApp Account / WhatsApp Settings.")
            )
        if self.message_type == "Template":
            frappe.throw(_("Template messages are not supported in Evolution mode."))
        if not self.to:
            frappe.throw(_("Mobile number is required."))

        settings = get_evolution_settings(self.whatsapp_account)
        provider = EvolutionProvider(settings)
        to_number = format_number(self.to)

        if self.content_type in ["document", "image", "video", "audio"] and self.attach:
            if self.attach.startswith("http"):
                file_url = self.attach
            else:
                file_url = f"{frappe.utils.get_url()}{self.attach}"
            media_bytes = None
            media_filename = None

            if (
                self.content_type == "document"
                and self.reference_doctype
                and self.reference_name
                and "download_pdf" in (self.attach or "")
            ):
                try:
                    resolved_print_format = (
                        _extract_print_format_from_attach(self.attach)
                        or _resolve_print_format(self.reference_doctype, None)
                    )
                    print_data = frappe.attach_print(
                        self.reference_doctype,
                        self.reference_name,
                        print_format=resolved_print_format,
                    )
                    media_bytes = print_data.get("fcontent")
                    media_filename = print_data.get("fname")
                except Exception:
                    media_bytes = None
                    media_filename = None
                    # If attach_print fails, try fetching the signed PDF internally
                    # so Evolution doesn't need to resolve local bench hostnames.
                    try:
                        parsed = urlparse(file_url or "")
                        urls_to_try = [file_url] if file_url else []
                        if parsed.hostname and parsed.hostname.endswith(".local"):
                            internal = parsed._replace(netloc="127.0.0.1:8000")
                            urls_to_try.append(internal.geturl())

                        for candidate in urls_to_try:
                            resp = requests.get(candidate, timeout=20)
                            resp.raise_for_status()
                            if resp.content:
                                media_bytes = resp.content
                                media_filename = f"{self.reference_name or 'document'}.pdf"
                                break
                    except Exception:
                        media_bytes = None
                        media_filename = None

            # For File attachments (/files or /private/files), upload bytes directly.
            if (
                not media_bytes
                and self.attach
                and isinstance(self.attach, str)
                and (self.attach.startswith("/files/") or self.attach.startswith("/private/files/"))
            ):
                try:
                    media_filename, content = get_file(self.attach)
                    media_bytes = content.encode() if isinstance(content, str) else content
                except Exception:
                    media_bytes = None
                    media_filename = None

            # Prefer byte upload when available to avoid Evolution DNS issues on site1.local.
            if media_bytes:
                file_url = ""

            try:
                response = provider.send_media(
                    to_number=to_number,
                    media_url=file_url,
                    media_type=self.content_type,
                    caption=self.message or "",
                    media_bytes=media_bytes,
                    filename=media_filename,
                )
            except Exception as e:
                is_dns_resolution_error = "enotfound" in str(e).lower()
                if not self._allow_attachment_link_fallback() and not is_dns_resolution_error:
                    frappe.throw(
                        _("Attachment send failed in File Only mode: {0}").format(str(e))
                    )
                fallback_text = self.message or ""
                if file_url:
                    fallback_text = (fallback_text + "\n\n" if fallback_text else "") + _("Attachment: {0}").format(file_url)
                self.message = fallback_text
                self.content_type = "text"
                response = provider.send_message(to_number, fallback_text)
        else:
            response = provider.send_message(to_number, self.message or "")
        self.message_id = _extract_response_message_id(response)
        return

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

    def create_communication(self):
        # Deprecated: we now render timeline directly from WhatsApp Message.
        return

    def update_communication(self):
        # Deprecated: we now render timeline directly from WhatsApp Message.
        return

    @frappe.whitelist()
    def send_read_receipt(self):
        frappe.throw(_("Read receipts are not supported in Evolution mode."))


def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])


@frappe.whitelist()
def send_template(
    to,
    reference_doctype,
    reference_name,
    template,
    message=None,
    attach=None,
    attach_document_print=0,
    print_format=None,
    no_letterhead=0,
    whatsapp_account=None,
):
    template_account = frappe.db.get_value("WhatsApp Templates", template, "whatsapp_account") if template else None
    selected_account = _resolve_evolution_account(
        preferred_account=template_account,
        template_account=whatsapp_account,
    )
    queue_name = _create_queue_placeholder(
        to=to,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        message_type="Template",
        content_type="document" if (attach or frappe.utils.cint(attach_document_print)) else "text",
        template=template,
        message=message,
        attach=attach,
        whatsapp_account=selected_account,
    )
    kwargs = {
        "to": to,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "template": template,
        "message": message,
        "attach": attach,
        "attach_document_print": attach_document_print,
        "print_format": print_format,
        "no_letterhead": no_letterhead,
        "whatsapp_account": selected_account,
        "queued_message_name": queue_name,
    }
    frappe.enqueue(
        "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.send_template_now",
        queue="short",
        enqueue_after_commit=True,
        **kwargs,
    )
    return {"queued": True, "queue_message_name": queue_name}


def send_template_now(
    to,
    reference_doctype,
    reference_name,
    template,
    message=None,
    attach=None,
    attach_document_print=0,
    print_format=None,
    no_letterhead=0,
    whatsapp_account=None,
    queued_message_name=None,
):
    _update_queue_status(queued_message_name, "Started")
    try:
        sent_doc = None
        template_account = frappe.db.get_value("WhatsApp Templates", template, "whatsapp_account") if template else None
        selected_account = _resolve_evolution_account(
            preferred_account=template_account,
            template_account=whatsapp_account,
        )
        if not is_evolution_enabled(whatsapp_account=selected_account):
            frappe.throw(
                _("Evolution API is required. Configure Evolution on WhatsApp Account / WhatsApp Settings.")
            )

        preview = get_template_preview(
            template=template,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
        rendered_text = (message or preview.get("rendered_text") or preview.get("template_text") or "").strip()
        if reference_doctype and reference_name and rendered_text:
            ref_doc = frappe.get_doc(reference_doctype, reference_name)
            rendered_text = render_named_placeholders(rendered_text, ref_doc)

        send_attach = attach
        if not send_attach and frappe.utils.cint(attach_document_print):
            key = frappe.get_doc(reference_doctype, reference_name).get_document_share_key()
            fmt = _resolve_print_format(reference_doctype, print_format)
            send_attach = (
                f"{frappe.utils.get_url()}/api/method/frappe.utils.print_format.download_pdf"
                f"?doctype={reference_doctype}&name={reference_name}&format={fmt}&no_letterhead={frappe.utils.cint(no_letterhead)}&key={key}"
            )

        if _recent_duplicate_exists(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            to_number=to,
            content_type="document" if send_attach else "text",
            message=rendered_text,
            attach=send_attach or "",
            template=template,
            exclude_name=queued_message_name,
        ):
            _update_queue_status(queued_message_name, "Skipped", details="Duplicate prevented")
            return

        if not queued_message_name:
            # Fallback for old enqueued tasks without a reference name
            doc = frappe.get_doc({
                "doctype": "WhatsApp Message",
                "to": to,
                "type": "Outgoing",
                "message_type": "Manual",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content_type": "document" if send_attach else "text",
                "message": rendered_text,
                "attach": send_attach or "",
                "whatsapp_account": selected_account or "",
            })
            doc.insert(ignore_permissions=True)
            sent_doc = doc
        else:
            # Update the existing queued document
            sent_doc = frappe.get_doc("WhatsApp Message", queued_message_name)
            sent_doc.update({
                # Evolution mode sends rendered text/media, not WA template API payloads.
                "message_type": "Manual",
                "content_type": "document" if send_attach else "text",
                "message": rendered_text,
                "attach": send_attach or "",
                "status": "Started",
                "whatsapp_account": selected_account or "",
            })
            # Queue placeholders are pre-created with a synthetic message_id.
            # Reset it and invoke send flow explicitly for existing docs.
            sent_doc.message_id = ""
            sent_doc.before_insert()
            sent_doc.db_update()
        
        _update_queue_status(
            queued_message_name,
            "Success",
            message_id=getattr(sent_doc, "message_id", None),
            details=getattr(sent_doc, "message", None),
        )
    except Exception as e:
        _update_queue_status(queued_message_name, "Failed", details=str(e))
        frappe.db.commit()
        if queued_message_name:
            frappe.log_error(frappe.get_traceback(), "WhatsApp Template Send Failed")
            return {"queued": False, "status": "Failed", "error": str(e)}
        raise


@frappe.whitelist()
def send_custom(
    to,
    reference_doctype,
    reference_name,
    message=None,
    attach=None,
    content_type="text",
    attach_document_print=0,
    print_format=None,
    no_letterhead=0,
    whatsapp_account=None,
):
    selected_account = _resolve_evolution_account(preferred_account=whatsapp_account)
    queue_name = _create_queue_placeholder(
        to=to,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        message_type="Manual",
        content_type=content_type,
        message=message,
        attach=attach,
        whatsapp_account=selected_account,
    )
    kwargs = {
        "to": to,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "message": message,
        "attach": attach,
        "content_type": content_type,
        "attach_document_print": attach_document_print,
        "print_format": print_format,
        "no_letterhead": no_letterhead,
        "whatsapp_account": selected_account,
        "queued_message_name": queue_name,
    }
    frappe.enqueue(
        "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.send_custom_now",
        queue="short",
        enqueue_after_commit=True,
        **kwargs,
    )
    return {"queued": True, "queue_message_name": queue_name}


def send_custom_now(
    to,
    reference_doctype,
    reference_name,
    message=None,
    attach=None,
    content_type="text",
    attach_document_print=0,
    print_format=None,
    no_letterhead=0,
    whatsapp_account=None,
    queued_message_name=None,
):
    _update_queue_status(queued_message_name, "Started")
    try:
        actual_content_type = content_type or "text"
        if reference_doctype and reference_name and (message or "").strip():
            ref_doc = frappe.get_doc(reference_doctype, reference_name)
            message = render_named_placeholders(message, ref_doc)
        if not attach and frappe.utils.cint(attach_document_print):
            key = frappe.get_doc(reference_doctype, reference_name).get_document_share_key()
            fmt = _resolve_print_format(reference_doctype, print_format)
            attach = (
                f"{frappe.utils.get_url()}/api/method/frappe.utils.print_format.download_pdf"
                f"?doctype={reference_doctype}&name={reference_name}&format={fmt}&no_letterhead={frappe.utils.cint(no_letterhead)}&key={key}"
            )
            actual_content_type = "document"

        if _recent_duplicate_exists(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            to_number=to,
            content_type=actual_content_type,
            message=message or "",
            attach=attach or "",
            template=None,
            exclude_name=queued_message_name,
        ):
            _update_queue_status(queued_message_name, "Skipped", details="Duplicate prevented")
            return

        selected_account = _resolve_evolution_account(preferred_account=whatsapp_account)

        if not queued_message_name:
            # Fallback for old enqueued tasks
            doc = frappe.get_doc({
                "doctype": "WhatsApp Message",
                "to": to,
                "type": "Outgoing",
                "message_type": "Manual",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content_type": actual_content_type,
                "message": message or "",
                "attach": attach or "",
                "whatsapp_account": selected_account or "",
            })
            doc.insert(ignore_permissions=True)
            sent_doc = doc
        else:
            # Update the existing queued document
            sent_doc = frappe.get_doc("WhatsApp Message", queued_message_name)
            sent_doc.update({
                "content_type": actual_content_type,
                "message": message or "",
                "attach": attach or "",
                "status": "Started",
                "whatsapp_account": selected_account or "",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name
            })
            # Queue placeholders are pre-created with a synthetic message_id.
            # Reset it and invoke send flow explicitly for existing docs.
            sent_doc.message_id = ""
            sent_doc.before_insert()
            sent_doc.db_update()

        _update_queue_status(
            queued_message_name,
            "Success",
            message_id=getattr(sent_doc, "message_id", None),
            details=getattr(sent_doc, "message", None),
        )
    except Exception as e:
        _update_queue_status(queued_message_name, "Failed", details=str(e))
        frappe.db.commit()
        if queued_message_name:
            frappe.log_error(frappe.get_traceback(), "WhatsApp Custom Send Failed")
            return {"queued": False, "status": "Failed", "error": str(e)}
        raise


@frappe.whitelist()
def get_template_preview(template, reference_doctype=None, reference_name=None, body_param=None):
    template_doc = frappe.get_doc("WhatsApp Templates", template)
    template_text = _get_template_text(template_doc)
    params = []

    manual_params = parse_body_param(body_param)
    if manual_params:
        params = manual_params
    elif reference_doctype and reference_name and template_doc.sample_values:
        field_names = template_doc.field_names.split(",") if template_doc.field_names else template_doc.sample_values.split(",")
        ref_doc = frappe.get_doc(reference_doctype, reference_name)
        params = [resolve_template_value(ref_doc, field) for field in field_names if field and field.strip()]
    elif reference_doctype and reference_name:
        ref_doc = frappe.get_doc(reference_doctype, reference_name)
        placeholder_matches = re.findall(r"{{\s*(\d+)\s*}}", template_text)
        fallback_values = [
            ref_doc.get("customer_name") or ref_doc.get("contact_display") or "",
            ref_doc.get("name") or "",
        ]
        for index in sorted({int(match) for match in placeholder_matches}):
            value = fallback_values[index - 1] if index - 1 < len(fallback_values) else ""
            params.append(str(value or ""))

    rendered_text = render_numeric_placeholders(template_text, params)
    if reference_doctype and reference_name:
        ref_doc = frappe.get_doc(reference_doctype, reference_name)
        rendered_text = render_named_placeholders(rendered_text, ref_doc)

    return {
        "template_text": template_text,
        "rendered_text": rendered_text,
        "params": params,
    }


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_linked_contacts_query(doctype, txt, searchfield, start, page_len, filters):
    reference_doctype = (filters or {}).get("reference_doctype")
    reference_name = (filters or {}).get("reference_name")
    if not reference_doctype or not reference_name:
        return []

    links, _ = _collect_reference_links(reference_doctype, reference_name)

    conditions = []
    values = {
        "txt": f"%{txt or ''}%",
        "start": start,
        "page_len": page_len,
    }
    for idx, (link_doctype, link_name) in enumerate(sorted(links)):
        values[f"ld_{idx}"] = link_doctype
        values[f"ln_{idx}"] = link_name
        conditions.append(f"(dl.link_doctype = %(ld_{idx})s and dl.link_name = %(ln_{idx})s)")

    if not conditions:
        return []

    # Get WhatsApp tick fields dynamically
    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import _get_tick_fields
    tick_fields = _get_tick_fields(purpose="whatsapp")
    if not tick_fields:
        return []
    tick_condition = "and (" + " or ".join([f"ifnull(cp.{tf}, 0) = 1" for tf in tick_fields]) + ")"

    searchfield = sanitize_searchfield(searchfield)
    return frappe.db.sql(
        f"""
        select distinct
            c.name,
            trim(concat(ifnull(c.first_name, ''), ' ', ifnull(c.last_name, '')))
        from `tabContact` c
        inner join `tabDynamic Link` dl
            on dl.parent = c.name and dl.parenttype = 'Contact'
        inner join `tabContact Phone` cp
            on cp.parent = c.name
        where ({' or '.join(conditions)})
            {tick_condition}
            and (
                c.name like %(txt)s
                or ifnull(c.first_name, '') like %(txt)s
                or ifnull(c.last_name, '') like %(txt)s
                or ifnull(c.mobile_no, '') like %(txt)s
                or ifnull(c.phone, '') like %(txt)s
            )
        order by
            case when c.name = %(txt)s then 0 else 1 end,
            c.modified desc
        limit %(start)s, %(page_len)s
        """,
        values,
    )
@frappe.whitelist()
def get_authorized_whatsapp_numbers(reference_doctype, reference_name, primary_only=0):
    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import (
        _get_dynamic_link_contact_numbers,
        _get_contact_numbers,
    )
    primary_only = frappe.utils.cint(primary_only)
    
    # 1. Direct Dynamic Links (strict)
    numbers = _get_dynamic_link_contact_numbers(
        reference_doctype,
        reference_name,
        purpose="whatsapp",
        primary_only=primary_only,
    )
    
    # 2. Check document fields if it's a Contact
    if reference_doctype == "Contact":
        numbers.extend(
            _get_contact_numbers(
                reference_name,
                purpose="whatsapp",
                primary_only=primary_only,
            )
        )

    # Return deduped list
    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import _dedupe_numbers
    return _dedupe_numbers(numbers)


def _collect_reference_links(reference_doctype, reference_name):
    """Collect link tuples and direct contact names from a reference document."""
    links = {(reference_doctype, reference_name)}
    direct_contacts = []
    try:
        ref_doc = frappe.get_doc(reference_doctype, reference_name)

        for contact_field in ("contact_person", "contact", "supplier_contact", "customer_contact"):
            contact_name = ref_doc.get(contact_field)
            if contact_name:
                links.add(("Contact", contact_name))
                direct_contacts.append(contact_name)

        party_type = ref_doc.get("party_type")
        party = ref_doc.get("party")
        if party_type and party:
            links.add((party_type, party))

        for field in ("customer", "supplier", "lead", "prospect", "employee"):
            value = ref_doc.get(field)
            if value:
                links.add((field.title(), value))
    except Exception:
        pass
    return links, direct_contacts


def _get_employee_mobile(employee_name):
    if not employee_name or not frappe.db.exists("Employee", employee_name):
        return ""
    try:
        meta = frappe.get_meta("Employee")
    except Exception:
        return ""
    candidates = []
    for fieldname in ("cell_number", "personal_mobile_no", "personal_mobile", "mobile_no"):
        if meta.get_field(fieldname):
            candidates.append(frappe.db.get_value("Employee", employee_name, fieldname))
    for raw in candidates:
        digits = re.sub(r"\D", "", str(raw or ""))
        if digits:
            return str(raw)
    return ""


@frappe.whitelist()
def get_default_contact_and_whatsapp_number(reference_doctype, reference_name):
    """Return first linked contact and strict primary WhatsApp number."""
    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import (
        _get_contact_numbers,
    )

    links, direct_contacts = _collect_reference_links(reference_doctype, reference_name)

    for contact_name in direct_contacts:
        if not frappe.db.exists("Contact", contact_name):
            continue
        numbers = _get_contact_numbers(
            contact_name,
            purpose="whatsapp",
            primary_only=1,
        )
        if numbers:
            return {
                "contact": contact_name,
                "mobile_no": numbers[0],
            }

    contact_names = []
    if reference_doctype == "Contact" and frappe.db.exists("Contact", reference_name):
        contact_names.append(reference_name)

    for link_doctype, link_name in sorted(links):
        linked_contacts = frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": link_doctype,
                "link_name": link_name,
                "parenttype": "Contact",
            },
            pluck="parent",
        )
        for contact_name in linked_contacts:
            if contact_name not in contact_names:
                contact_names.append(contact_name)

    for contact_name in contact_names:
        numbers = _get_contact_numbers(
            contact_name,
            purpose="whatsapp",
            primary_only=1,
        )
        if numbers:
            return {
                "contact": contact_name,
                "mobile_no": numbers[0],
            }

    # Employee fallback for docs with party_type=Employee or employee link.
    if reference_doctype == "Employee":
        emp_mobile = _get_employee_mobile(reference_name)
        if emp_mobile:
            return {"mobile_no": emp_mobile}

    for link_doctype, link_name in sorted(links):
        if link_doctype == "Employee":
            emp_mobile = _get_employee_mobile(link_name)
            if emp_mobile:
                return {"mobile_no": emp_mobile}

    return {}


def get_whatsapp_timeline_content(doctype, docname):
    if not doctype or not docname or not frappe.db.table_exists("WhatsApp Message"):
        return []

    rows = frappe.get_all(
        "WhatsApp Message",
        filters={
            "reference_doctype": doctype,
            "reference_name": docname,
            "status": ["not in", ["Queued", "Started"]],
        },
        fields=[
            "name",
            "creation",
            "type",
            "to",
            "from",
            "message",
            "status",
            "template",
            "attach",
        ],
        order_by="creation desc",
        limit=50,
    )

    out = []
    tick_map = {
        "Sent": "✓",
        "Success": "✓",
        "Delivered": "✓✓",
        "Read": "✓✓",
        "Played": "✓✓",
    }
    for row in rows:
        row_type = row.get("type") or ""
        phone = row.get("to") if row_type == "Outgoing" else row.get("from")
        direction = _("Sent") if row_type == "Outgoing" else _("Received")
        status = row.get("status") or ""
        message = (row.get("message") or "").strip()

        if not message and row.get("template"):
            message = _("Template: {0}").format(row.get("template"))
        if not message and row.get("attach"):
            message = _("Attachment sent")
        if not message:
            message = _("WhatsApp message")
        if len(message) > 320:
            message = f"{message[:317]}..."

        doc_url = frappe.utils.get_url_to_form("WhatsApp Message", row.get("name"))
        status_html = (
            f"<span class='wa-timeline-status wa-status-{escape(status.lower())}'>{escape(status)} {escape(tick_map.get(status, ''))}</span>"
            if status
            else ""
        )
        content = (
            "<div class='wa-timeline-item'>"
            f"<div class='wa-timeline-head'><a href='{doc_url}'>{escape(row.get('name') or '')}</a>"
            f" · {escape(direction)} · {escape(format_number(phone or ''))} {status_html}</div>"
            f"<div class='wa-timeline-message'>{escape(message)}</div>"
            "</div>"
        )
        out.append(
            {
                "doctype": "WhatsApp Message",
                "name": row.get("name"),
                "creation": row.get("creation"),
                "icon": "message-circle",
                "icon_size": "sm",
                "content": content,
            }
        )
    return out
