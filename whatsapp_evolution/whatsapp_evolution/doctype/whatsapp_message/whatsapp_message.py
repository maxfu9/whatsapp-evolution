# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import json
import re
from urllib.parse import urlparse, parse_qs
import frappe
from frappe import _, throw
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request
from frappe.desk.search import sanitize_searchfield

from whatsapp_evolution.utils import get_whatsapp_account, format_number
from whatsapp_evolution.whatsapp_evolution.providers import EvolutionProvider


def _get_template_text(template_doc):
    return (template_doc.get("template_message") or template_doc.get("template") or "").strip()


def _parse_body_param(body_param):
    if not body_param:
        return []
    try:
        parsed = json.loads(body_param) if isinstance(body_param, str) else body_param
    except Exception:
        return []
    if isinstance(parsed, dict):
        return [str(v or "") for _, v in sorted(parsed.items(), key=lambda x: int(str(x[0])) if str(x[0]).isdigit() else str(x[0]))]
    if isinstance(parsed, list):
        return [str(v or "") for v in parsed]
    return []


def _render_template_text(template_text, params):
    rendered = template_text or ""
    for idx, value in enumerate(params, start=1):
        rendered = re.sub(r"{{\s*" + str(idx) + r"\s*}}", str(value or ""), rendered)
    return rendered


def _is_evolution_enabled_global():
    settings = frappe.get_single("WhatsApp Settings")
    return bool(settings.get("evolution_api_base") and settings.get_password("evolution_api_token"))


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
    def _allow_attachment_link_fallback(self):
        mode = (frappe.db.get_single_value("WhatsApp Settings", "attachment_delivery_mode") or "").strip()
        if not mode:
            return False
        return mode.lower() == "fallback to link"

    def is_evolution_enabled(self):
        settings = frappe.get_single("WhatsApp Settings")
        return bool(settings.get("evolution_api_base") and settings.get_password("evolution_api_token"))

    def validate(self):
        self.set_whatsapp_account()

    def on_update(self):
        self.update_profile_name()

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
        if not frappe.db.exists("WhatsApp Profiles", {"number": number}):
            frappe.get_doc({
                "doctype": "WhatsApp Profiles",
                "profile_name": self.profile_name,
                "number": number,
                "whatsapp_account": self.whatsapp_account
            }).insert(ignore_permissions=True)

    def set_whatsapp_account(self):
        """Set whatsapp account to default if missing"""
        if self.is_evolution_enabled():
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

            elif self.content_type == "flow":
                # WhatsApp Flow message
                if not self.flow:
                    frappe.throw(_("WhatsApp Flow is required for flow content type"))

                flow_doc = frappe.get_doc("WhatsApp Flow", self.flow)

                if not flow_doc.flow_id:
                    frappe.throw(_("Flow must be created on WhatsApp before sending"))

                # Determine flow mode - draft flows can be tested with mode: "draft"
                flow_mode = None
                if flow_doc.status != "Published":
                    flow_mode = "draft"
                    frappe.msgprint(_("Sending flow in draft mode (for testing only)"), indicator="orange")

                # Get first screen if not specified
                flow_screen = self.flow_screen
                if not flow_screen and flow_doc.screens:
                    flow_screen = flow_doc.screens[0].screen_id

                data["type"] = "interactive"
                data["interactive"] = {
                    "type": "flow",
                    "body": {"text": self.message or "Please fill out the form"},
                    "action": {
                        "name": "flow",
                        "parameters": {
                            "flow_message_version": "3",
                            "flow_id": flow_doc.flow_id,
                            "flow_cta": self.flow_cta or flow_doc.flow_cta or "Open",
                            "flow_action": "navigate",
                            "flow_action_payload": {
                                "screen": flow_screen
                            }
                        }
                    }
                }

                # Add draft mode for testing unpublished flows
                if flow_mode:
                    data["interactive"]["action"]["parameters"]["mode"] = flow_mode

                # Add flow token - generate one if not provided (required by WhatsApp)
                flow_token = self.flow_token or frappe.generate_hash(length=16)
                data["interactive"]["action"]["parameters"]["flow_token"] = flow_token

            try:
                self.notify(data)
                self.status = "Success"
            except Exception as e:
                self.status = "Failed"
                frappe.throw(f"Failed to send message {str(e)}")
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
                params = _parse_body_param(self.body_param)
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
                    value = ref_doc.get_formatted(field_name.strip())
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
        if self.is_evolution_enabled():
            if self.message_type == "Template":
                frappe.throw(_("Template messages are not supported in Evolution mode."))
            if not self.to:
                frappe.throw(_("Mobile number is required."))

            settings_doc = frappe.get_single("WhatsApp Settings")
            settings = settings_doc.as_dict()
            settings["evolution_api_token"] = settings_doc.get_password("evolution_api_token")
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
                        print_data = frappe.attach_print(
                            self.reference_doctype,
                            self.reference_name,
                            print_format="Standard",
                        )
                        media_bytes = print_data.get("fcontent")
                        media_filename = print_data.get("fname")
                    except Exception:
                        media_bytes = None
                        media_filename = None

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
                    if not self._allow_attachment_link_fallback():
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
            self.message_id = response.get("id") or response.get("message_id") or ""
            return

        whatsapp_account = frappe.get_doc(
            "WhatsApp Account",
            self.whatsapp_account,
        )
        token = whatsapp_account.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{whatsapp_account.url}/{whatsapp_account.version}/{whatsapp_account.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            self.message_id = response["messages"][0]["id"]

        except Exception as e:
            res = frappe.flags.integration_request.json().get("error", {})
            error_message = res.get("Error", res.get("message"))
            frappe.get_doc(
                {
                    "doctype": "WhatsApp Notification Log",
                    "template": "Text Message",
                    "meta_data": frappe.flags.integration_request.json(),
                }
            ).insert(ignore_permissions=True)

            frappe.throw(msg=error_message, title=res.get("error_user_title", "Error"))

    def format_number(self, number):
        """Format number."""
        if number.startswith("+"):
            number = number[1 : len(number)]

        return number

    @frappe.whitelist()
    def send_read_receipt(self):
        if self.is_evolution_enabled():
            frappe.throw(_("Read receipts are not supported in Evolution mode."))

        data = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": self.message_id
        }

        settings = frappe.get_doc(
            "WhatsApp Account",
            self.whatsapp_account,
        )

        token = settings.get_password("token")

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{settings.url}/{settings.version}/{settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )

            if response.get("success"):
                self.status = "marked as read"
                self.save()
                return response.get("success")

        except Exception as e:
            res = frappe.flags.integration_request.json().get("error", {})
            error_message = res.get("Error", res.get("message"))
            frappe.log_error("WhatsApp API Error", f"{error_message}\n{res}")


def on_doctype_update():
    frappe.db.add_index("WhatsApp Message", ["reference_doctype", "reference_name"])


@frappe.whitelist()
def send_template(to, reference_doctype, reference_name, template, message=None, attach=None, attach_document_print=0, print_format=None, no_letterhead=0):
    queue_name = _create_queue_placeholder(
        to=to,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        message_type="Template",
        content_type="document" if (attach or frappe.utils.cint(attach_document_print)) else "text",
        template=template,
        message=message,
        attach=attach,
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
        "queued_message_name": queue_name,
    }
    frappe.enqueue(
        "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.send_template_now",
        queue="short",
        enqueue_after_commit=True,
        **kwargs,
    )
    return {"queued": True, "queue_message_name": queue_name}


def send_template_now(to, reference_doctype, reference_name, template, message=None, attach=None, attach_document_print=0, print_format=None, no_letterhead=0, queued_message_name=None):
    _update_queue_status(queued_message_name, "Started")
    try:
        sent_doc = None
        if _is_evolution_enabled_global():
            preview = get_template_preview(
                template=template,
                reference_doctype=reference_doctype,
                reference_name=reference_name,
            )
            rendered_text = (message or preview.get("rendered_text") or preview.get("template_text") or "").strip()

            send_attach = attach
            if not send_attach and frappe.utils.cint(attach_document_print):
                key = frappe.get_doc(reference_doctype, reference_name).get_document_share_key()
                fmt = print_format or "Standard"
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

            doc = frappe.get_doc(
                {
                    "doctype": "WhatsApp Message",
                    "to": to,
                    "type": "Outgoing",
                    "message_type": "Manual",
                    "reference_doctype": reference_doctype,
                    "reference_name": reference_name,
                    "content_type": "document" if send_attach else "text",
                    "message": rendered_text,
                    "attach": send_attach or "",
                }
            )
            doc.save()
            sent_doc = doc
        else:
            doc = frappe.get_doc({
                "doctype": "WhatsApp Message",
                "to": to,
                "type": "Outgoing",
                "message_type": "Template",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content_type": "text",
                "template": template
            })

            if _recent_duplicate_exists(
                reference_doctype=reference_doctype,
                reference_name=reference_name,
                to_number=to,
                content_type="text",
                message="",
                attach="",
                template=template,
                exclude_name=queued_message_name,
            ):
                _update_queue_status(queued_message_name, "Skipped", details="Duplicate prevented")
                return

            doc.save()
            sent_doc = doc
        _update_queue_status(
            queued_message_name,
            "Success",
            message_id=getattr(sent_doc, "message_id", None),
            details=getattr(sent_doc, "message", None),
        )
    except Exception as e:
        _update_queue_status(queued_message_name, "Failed", details=str(e))
        raise e


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
):
    queue_name = _create_queue_placeholder(
        to=to,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        message_type="Manual",
        content_type=content_type,
        message=message,
        attach=attach,
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
    queued_message_name=None,
):
    _update_queue_status(queued_message_name, "Started")
    try:
        if not attach and frappe.utils.cint(attach_document_print):
            key = frappe.get_doc(reference_doctype, reference_name).get_document_share_key()
            fmt = print_format or "Standard"
            attach = (
                f"{frappe.utils.get_url()}/api/method/frappe.utils.print_format.download_pdf"
                f"?doctype={reference_doctype}&name={reference_name}&format={fmt}&no_letterhead={frappe.utils.cint(no_letterhead)}&key={key}"
            )

        if _recent_duplicate_exists(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            to_number=to,
            content_type=content_type or "text",
            message=message or "",
            attach=attach or "",
            template=None,
            exclude_name=queued_message_name,
        ):
            _update_queue_status(queued_message_name, "Skipped", details="Duplicate prevented")
            return

        doc = frappe.get_doc(
            {
                "doctype": "WhatsApp Message",
                "to": to,
                "type": "Outgoing",
                "message_type": "Manual",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "content_type": content_type or "text",
                "message": message or "",
                "attach": attach or "",
            }
        )
        doc.save()
        _update_queue_status(
            queued_message_name,
            "Success",
            message_id=getattr(doc, "message_id", None),
            details=getattr(doc, "message", None),
        )
    except Exception as e:
        _update_queue_status(queued_message_name, "Failed", details=str(e))
        raise e


@frappe.whitelist()
def get_template_preview(template, reference_doctype=None, reference_name=None, body_param=None):
    template_doc = frappe.get_doc("WhatsApp Templates", template)
    template_text = _get_template_text(template_doc)
    params = []

    manual_params = _parse_body_param(body_param)
    if manual_params:
        params = manual_params
    elif reference_doctype and reference_name and template_doc.sample_values:
        field_names = template_doc.field_names.split(",") if template_doc.field_names else template_doc.sample_values.split(",")
        ref_doc = frappe.get_doc(reference_doctype, reference_name)
        params = [str(ref_doc.get_formatted(field.strip()) or "") for field in field_names if field and field.strip()]
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

    return {
        "template_text": template_text,
        "rendered_text": _render_template_text(template_text, params),
        "params": params,
    }


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_linked_contacts_query(doctype, txt, searchfield, start, page_len, filters):
    reference_doctype = (filters or {}).get("reference_doctype")
    reference_name = (filters or {}).get("reference_name")
    if not reference_doctype or not reference_name:
        return []

    links = {(reference_doctype, reference_name)}
    try:
        ref_doc = frappe.get_doc(reference_doctype, reference_name)
        for field in ("customer", "supplier", "lead", "prospect"):
            value = ref_doc.get(field)
            if value:
                links.add((field.title(), value))
    except Exception:
        pass

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

    searchfield = sanitize_searchfield(searchfield)
    return frappe.db.sql(
        f"""
        select distinct
            c.name,
            trim(concat(ifnull(c.first_name, ''), ' ', ifnull(c.last_name, '')))
        from `tabContact` c
        inner join `tabDynamic Link` dl
            on dl.parent = c.name and dl.parenttype = 'Contact'
        where ({' or '.join(conditions)})
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
