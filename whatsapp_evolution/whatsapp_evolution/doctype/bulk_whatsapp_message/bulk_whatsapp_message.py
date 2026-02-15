# Bulk WhatsApp Messaging for WhatsApp Evolution
# bulk_whatsapp_messaging.py

import frappe
from frappe import _
import json
import time
from frappe.utils import cint
from frappe.model.document import Document
from frappe.model.naming import make_autoname

# Add these files to your whatsapp_evolution app

# 1. First, create a new DocType for Bulk WhatsApp Messaging
# Save this as a Python file in your app's folder: 
# whatsapp_evolution/whatsapp_evolution/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py

class BulkWhatsAppMessage(Document):
    def autoname(self):
        self.name = make_autoname("BULK-WA-.YYYY.-.#####")
    
    def validate(self):
        # self.validate_message()
        self.validate_recipients()
    
    def validate_message(self):
        if not self.message_content:
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

            if index < total_recipients and delay_between_messages:
                time.sleep(delay_between_messages)

        failed_count = frappe.db.count(
            "WhatsApp Message",
            {"bulk_message_reference": self.name, "status": "Failed"},
        )
        self.reload()

        if any_failure or failed_count:
            self.db_set("status", "Partially Failed")
        elif cint(self.sent_count) >= cint(self.recipient_count):
            self.db_set("status", "Completed")
        else:
            self.db_set("status", "Partially Failed")
    
    def create_single_message(self, recipient):
        """Create a single message in the queue"""
        # message_content = self.message_content
        
        # Replace variables in the message if any
        recipient_data = self._parse_recipient_data(recipient)
        
        # Create WhatsApp message
        wa_message = frappe.new_doc("WhatsApp Message")
        # wa_message.from_number = self.from_number
        wa_message.to = recipient.get("mobile_number")
        wa_message.message_type = "Text"
        # wa_message.message = message_content
        wa_message.flags.custom_ref_doc = recipient_data
        wa_message.bulk_message_reference = self.name
        if self.whatsapp_account:
            wa_message.whatsapp_account = self.whatsapp_account
        
        # If template is being used
        if self.use_template:
            wa_message.template = self.template
            wa_message.message_type = 'Template'
            wa_message.use_template = self.use_template
            # Handle template variables if needed

            if recipient_data and self.variable_type=='Unique':
                wa_message.body_param = json.dumps(recipient_data)
            elif self.template_variables and self.variable_type=='Common':
                wa_message.body_param = self.template_variables
            if self.attach:
                wa_message.attach = self.attach
        
        # Set status to queued
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
        # Update message count
        self.reload()
        self.db_set("sent_count", cint(self.sent_count) + 1)
        self.reload()
        if cint(self.recipient_count) == cint(self.sent_count):
            self.db_set("status", "Completed")
        return True

    def retry_failed(self):
        """Retry failed messages"""
        failed_messages = frappe.get_all(
            "WhatsApp Message",
            filters={
                "bulk_message_reference": self.name,
                "status": "Failed"
            },
            fields=["name"]
        )
        
        count = 0
        for msg in failed_messages:
            message_doc = frappe.get_doc("WhatsApp Message", msg.name)
            message_doc.status = "Queued"
            message_doc.save(ignore_permissions=True)
            count += 1
        
        frappe.msgprint(_("{0} messages have been requeued for sending").format(count))
        
    def get_progress(self):
        """Get sending progress for this bulk message"""
        total = self.recipient_count
        sent = frappe.db.count("WhatsApp Message", {
            "bulk_message_reference": self.name,
            "status": ["in", ["sent","delivered", "Success", "read"]]
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
