"""Create whatsapp template."""

# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import os
import json
import frappe
import magic
from frappe import _, throw
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request, make_request
from frappe.desk.form.utils import get_pdf_link

from whatsapp_evolution.utils import get_whatsapp_account

class WhatsAppTemplates(Document):
    """Create whatsapp template."""
    def is_evolution_enabled(self):
        return True

    def validate(self):
        self.set_whatsapp_account()
        if not self.language_code or self.has_value_changed("language"):
            lang_code = frappe.db.get_value("Language", self.language) or "en"
            self.language_code = lang_code.replace("-", "_")

    def set_whatsapp_account(self):
        """Set whatsapp account to default if missing"""
        if not self.whatsapp_account:
            default_whatsapp_account = get_whatsapp_account()
            if not default_whatsapp_account:
                throw(_("Please set a default outgoing WhatsApp Account or Select available WhatsApp Account"))
            else:
                self.whatsapp_account = default_whatsapp_account.name

    def get_session_id(self):
        """Upload media."""
        self.get_settings()
        file_path = self.get_absolute_path(self.sample)
        mime = magic.Magic(mime=True)
        file_type = mime.from_file(file_path)

        payload = {
            'file_length': os.path.getsize(file_path),
            'file_type': file_type,
            'messaging_product': 'whatsapp'
        }

        response = make_post_request(
            f"{self._url}/{self._version}/{self._app_id}/uploads",
            headers=self._headers,
            data=json.loads(json.dumps(payload))
        )
        self._session_id = response['id']

    def get_media_id(self):
        self.get_settings()

        headers = {
                "authorization": f"OAuth {self._token}"
            }
        file_name = self.get_absolute_path(self.sample)
        with open(file_name, mode='rb') as file: # b is important -> binary
            file_content = file.read()

        payload = file_content
        response = make_post_request(
            f"{self._url}/{self._version}/{self._session_id}",
            headers=headers,
            data=payload
        )

        self._media_id = response['h']

    def get_absolute_path(self, file_name):
        if(file_name.startswith('/files/')):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}/public{file_name}'
        if(file_name.startswith('/private/')):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}{file_name}'
        return file_path


    def after_insert(self):
        if self.template_name:
            self.actual_name = self.template_name.lower().replace(" ", "_")
        self.db_update()

    def update_template(self):
        """Update template to meta."""
        self.get_settings()
        data = {"components": []}

        body = {
            "type": "BODY",
            "text": self.template,
        }
        if self.sample_values:
            body.update({"example": {"body_text": [self.sample_values.split(",")]}})
        data["components"].append(body)
        if self.header_type:
            data["components"].append(self.get_header())
        if self.footer:
            data["components"].append({"type": "FOOTER", "text": self.footer})
        if self.buttons:
            button_block = {"type": "BUTTONS", "buttons": []}
            for btn in self.buttons:
                b = {"type": btn.button_type, "text": btn.button_label}

                if btn.button_type == "Visit Website":
                    b["type"] = "URL"
                    b["url"] = btn.website_url
                    if btn.url_type == "Dynamic" and btn.example_url:
                        b["example"] = btn.example_url.split(",")
                elif btn.button_type == "Call Phone":
                    b["type"] = "PHONE_NUMBER"
                    b["phone_number"] = btn.phone_number
                elif btn.button_type == "Quick Reply":
                    b["type"] = "QUICK_REPLY"

                button_block["buttons"].append(b)

            data["components"].append(button_block)

        try:
            # post template to meta for update
            make_post_request(
                f"{self._url}/{self._version}/{self.id}",
                headers=self._headers,
                data=json.dumps(data),
            )
        except Exception as e:
            raise e
            # res = frappe.flags.integration_request.json()['error']
            # frappe.throw(
            #     msg=res.get('error_user_msg', res.get("message")),
            #     title=res.get("error_user_title", "Error"),
            # )

    def get_settings(self):
        """Get whatsapp settings."""
        settings = frappe.get_doc("WhatsApp Account", self.whatsapp_account)
        self._token = settings.get_password("token")
        self._url = settings.url
        self._version = settings.version
        self._business_id = settings.business_id
        self._app_id = settings.app_id

        self._headers = {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
        }

    def on_trash(self):
        return

    def get_header(self):
        """Get header format."""
        header = {"type": "header", "format": self.header_type}
        if self.header_type == "TEXT":
            header["text"] = self.header
            if self.sample:
                samples = self.sample.split(", ")
                header.update({"example": {"header_text": samples}})
        else:
            pdf_link = ''
            if not self.sample:
                key = frappe.get_doc(self.doctype, self.name).get_document_share_key()
                link = get_pdf_link(self.doctype, self.name)
                pdf_link = f"{frappe.utils.get_url()}{link}&key={key}"
            header.update({"example": {"header_handle": [self._media_id]}})

        return header

@frappe.whitelist()
def fetch():
    frappe.throw(_("Meta sync is removed. Create and manage templates locally for Evolution API."))

def upsert_doc_without_hooks(doc, child_dt, child_field):
    """Insert or update a parent document and its children without hooks."""
    if frappe.db.exists(doc.doctype, doc.name):
        doc.db_update()
        frappe.db.delete(child_dt, {"parent": doc.name, "parenttype": doc.doctype})
    else:
        doc.db_insert()
    for d in doc.get(child_field):
        d.parent = doc.name
        d.parenttype = doc.doctype
        d.parentfield = child_field
        d.db_insert()
    frappe.db.commit()
