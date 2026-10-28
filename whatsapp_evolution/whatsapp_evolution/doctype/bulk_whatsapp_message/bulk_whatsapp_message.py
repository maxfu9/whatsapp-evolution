# Bulk WhatsApp Messaging for WhatsApp Evolution
# bulk_whatsapp_messaging.py

import frappe
from frappe import _
import json
import time
from frappe.utils import cint
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import (
    _is_evolution_enabled_global,
    _parse_body_param,
    _render_template_text,
    enqueue_existing_message,
)

# Add these files to your whatsapp_evolution app

# 1. First, create a new DocType for Bulk WhatsApp Messaging
# Save this as a Python file in your app's folder: 
# whatsapp_evolution/whatsapp_evolution/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py

class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")
    
    def validate(self):
        self.validate_message()
        self.validate_recipients()
    
    def validate_message(self):
        if not self.use_template and not (self.message_content or "").strip():
            frappe.throw(_("Message content is required"))
    
    def validate_recipients(self):
        if not self.recipients and not self.recipient_list:
            frappe.throw(_("At least one recipient or a recipient list is required"))
        
        # If recipient list is provided, count recipients
        if self.recipient_type == 'Recipient List' and self.recipient_list:
            recipient_count = frappe.db.count("WhatsApp Recipient", {"parent": self.recipient_list})
            if recipient_count == 0:
                frappe.throw(_("Selected recipient list has no recipients"))
            self.recipient_count = recipient_count
        # If individual recipients are provided
        elif self.recipients:
            self.recipient_count = len(self.recipients)
    
    def on_submit(self):
        self.db_set("status", "Queued")
        self.queue_messages()
    
    def queue_messages(self):
        """Queue one background worker job for sequential sending."""
        self.db_set("sent_count", 0)
        self.db_set("status", "Queued")
        frappe.enqueue_doc(
            self.doctype,
            self.name,
            "process_message_queue",
            queue="long",
            timeout=4000,
            enqueue_after_commit=True,
        )

    def _get_recipients(self):
        if self.recipient_type == "Recipient List" and self.recipient_list:
            return frappe.get_all(
                "WhatsApp Recipient",
                filters={"parent": self.recipient_list},
                fields=["mobile_number", "name", "recipient_name", "recipient_data"],
            )

        recipients = []
        for row in self.recipients:
            recipients.append(
                {
                    "mobile_number": row.mobile_number,
                    "name": row.name,
                    "recipient_name": row.recipient_name,
                    "recipient_data": row.recipient_data,
                }
            )
        return recipients

    def _parse_recipient_data(self, recipient):
        recipient_data = recipient.get("recipient_data") if recipient else None
        if not recipient_data:
            return {}

        if isinstance(recipient_data, dict):
            return recipient_data

        try:
            return json.loads(recipient_data)
        except Exception:
            frappe.log_error(
                title="WhatsApp Bulk Messaging",
                message=f"Invalid recipient_data for {recipient.get('mobile_number')}: {recipient_data}",
            )
            return {}

    def process_message_queue(self):
        """Send one-by-one with delay to reduce provider throttling/blocks."""
        recipients = self._get_recipients()
        total_recipients = len(recipients)
        delay_between_messages = max(cint(self.delay_between_messages) or 60, 0)

        if not total_recipients:
            self.db_set("status", "Partially Failed")
            return

        self.db_set("status", "In Progress")
        any_failure = False

        for index, recipient in enumerate(recipients, start=1):
            if not self.create_single_message(recipient):
                any_failure = True
            # This worker runs after the submitted bulk document committed.
            # Release each recipient's after-commit job before the rate delay.
            frappe.db.commit()

            if index < total_recipients and delay_between_messages:
                time.sleep(delay_between_messages)

        failed_count = frappe.db.count(
            "WhatsApp Message",
            {"bulk_message_reference": self.name, "status": "Failed"},
        )
        self.reload()

        self.update_progress()
    
    def create_single_message(self, recipient):
        """Create a single message in the queue"""
        recipient_data = self._parse_recipient_data(recipient)

        wa_message = frappe.new_doc("WhatsApp Message")
        wa_message.type = "Outgoing"
        wa_message.to = recipient.get("mobile_number")
        wa_message.flags.custom_ref_doc = recipient_data
        wa_message.bulk_message_reference = self.name
        if self.whatsapp_account:
            wa_message.whatsapp_account = self.whatsapp_account

        # Evolution path: render template to plain text and send as Manual.
        if self.use_template and _is_evolution_enabled_global():
            wa_message.message_type = "Manual"
            wa_message.content_type = "document" if self.attach else "text"
            wa_message.message = self._render_bulk_template_text(recipient_data)
            if self.attach:
                wa_message.attach = self.attach

        # Meta-compatible template path (legacy behavior).
        if self.use_template:
            wa_message.template = self.template
            wa_message.use_template = self.use_template
            if not _is_evolution_enabled_global():
                wa_message.message_type = "Template"

            if recipient_data and self.variable_type == "Unique":
                wa_message.body_param = json.dumps(recipient_data)
            elif self.template_variables and self.variable_type == "Common":
                wa_message.body_param = self.template_variables
            if self.attach and not _is_evolution_enabled_global():
                wa_message.attach = self.attach
        else:
            wa_message.message_type = "Manual"
            wa_message.content_type = "text"
            wa_message.message = self.message_content or ""

        wa_message.status = "Queued"
        try:
            wa_message.insert(ignore_permissions=True)
        except Exception:
            self.db_set("status", "Partially Failed")
            frappe.log_error(
                title="WhatsApp Bulk Messaging",
                message=frappe.get_traceback(),
            )
            return False
        if wa_message.status == "Queued":
            return True
        if wa_message.status not in {"Success", "Sent", "Delivered", "Read", "Played"}:
            # Suppressed duplicates and uncertain outcomes are not new sends.
            self.db_set("status", "Partially Failed")
            return False
        # Update message count
        self.reload()
        self.db_set("sent_count", cint(self.sent_count) + 1)
        self.reload()
        if cint(self.recipient_count) == cint(self.sent_count):
            self.db_set("status", "Completed")
        return True

    def _render_bulk_template_text(self, recipient_data):
        if not self.template:
            return ""

        template_doc = frappe.get_doc("WhatsApp Templates", self.template)
        template_text = (template_doc.get("template_message") or template_doc.get("template") or "").strip()
        if not template_text:
            return ""

        params = []
        if self.variable_type == "Unique" and recipient_data:
            if isinstance(recipient_data, dict):
                digit_keys = [k for k in recipient_data.keys() if str(k).isdigit()]
                if digit_keys:
                    params = [
                        str(recipient_data.get(k) or "")
                        for k in sorted(digit_keys, key=lambda x: int(str(x)))
                    ]
                else:
                    params = [str(v or "") for _, v in sorted(recipient_data.items())]
            elif isinstance(recipient_data, list):
                params = [str(v or "") for v in recipient_data]
        elif self.variable_type == "Common":
            params = _parse_body_param(self.template_variables)

        return _render_template_text(template_text, params)

    @frappe.whitelist()
    def retry_failed(self):
        """Retry failed messages"""
        self.check_permission("write")
        failed_messages = frappe.get_all(
            "WhatsApp Message",
            filters={
                "bulk_message_reference": self.name,
                "status": "Failed",
                "message_id": ["is", "not set"],
            },
            fields=["name"]
        )
        
        count = 0
        for msg in failed_messages:
            message_doc = frappe.get_doc("WhatsApp Message", msg.name)
            frappe.db.set_value("WhatsApp Message", msg.name, {"status": "Queued", "last_error": "", "message_id": ""})
            enqueue_existing_message(msg.name)
            count += 1
        
        frappe.msgprint(_("{0} messages have been requeued for sending").format(count))

    def update_progress(self):
        rows = frappe.get_all("WhatsApp Message", filters={"bulk_message_reference": self.name},
                              fields=["status"], limit_page_length=0)
        sent = sum(row.status in {"Success", "Sent", "Delivered", "Read", "Played"} for row in rows)
        pending = any(row.status in {"Queued", "Started"} for row in rows)
        status = "In Progress" if pending else ("Completed" if sent >= self.recipient_count else "Partially Failed")
        self.db_set({"sent_count": sent, "status": status})
        
    def get_progress(self):
        """Get sending progress for this bulk message"""
        total = self.recipient_count
        sent = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", ["Sent", "Delivered", "Success", "Read", "Played"]]
        })
        failed = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": "Failed"
        })
        queued = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": "Queued"
        })
        
        return {
            "total": total,
            "sent": sent,
            "failed": failed,
            "queued": queued,
            "percent": (sent / total * 100) if total else 0
        }
