# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

import json
from unittest.mock import patch, MagicMock

import frappe
from whatsapp_evolution.testing import IntegrationTestCase


class TestWhatsAppTemplates(IntegrationTestCase):
    """Tests for WhatsApp Templates doctype."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_test_account()

    @classmethod
    def _ensure_test_account(cls):
        if not frappe.db.exists("WhatsApp Account", "Test WA Tmpl Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test WA Tmpl Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "tmpl_test_phone_id",
                "business_id": "tmpl_test_business_id",
                "app_id": "tmpl_test_app_id",
                "webhook_verify_token": "tmpl_test_verify_token",
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            frappe.db.commit()

    def setUp(self):
        # Set password within each test's transaction scope
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("WhatsApp Account", "Test WA Tmpl Account", "test_tmpl_token", "token")
        # Clear ALL defaults then set ours (db.set_value bypasses on_update hooks)
        frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_outgoing=0, is_default_incoming=0")
        frappe.db.set_value("WhatsApp Account", "Test WA Tmpl Account", {
            "is_default_outgoing": 1,
            "is_default_incoming": 1,
        })

    def tearDown(self):
        # Use SQL-level delete to avoid triggering on_trash (which calls get_settings)
        frappe.db.delete("WhatsApp Templates", {"template_name": ["like", "test_tmpl_%"]})
        frappe.db.delete("WhatsApp Templates", {"template_name": ["like", "test_msg_template%"]})
        frappe.db.commit()

    def _make_template_without_hooks(self, **kwargs):
        """Create a template directly in DB to avoid Meta API calls."""
        template_name = kwargs.get("template_name", "test_tmpl_basic")
        language_code = kwargs.get("language_code", "en")
        doc = frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "template_name": template_name,
            "actual_name": template_name.lower().replace(" ", "_"),
            "template": kwargs.get("template", "Hello {{1}}"),
            "category": kwargs.get("category", "TRANSACTIONAL"),
            "language": kwargs.get("language", frappe.db.get_value("Language", {"language_code": "en"}) or "en"),
            "language_code": language_code,
            "whatsapp_account": kwargs.get("whatsapp_account", "Test WA Tmpl Account"),
            "status": kwargs.get("status", "APPROVED"),
            "id": kwargs.get("id", f"tmpl_id_{template_name}"),
            "header_type": kwargs.get("header_type", ""),
            "header": kwargs.get("header", ""),
            "footer": kwargs.get("footer", ""),
            "sample_values": kwargs.get("sample_values", ""),
        })
        doc.db_insert()
        frappe.db.commit()
        return frappe.get_doc("WhatsApp Templates", doc.name)

    def test_template_autoname(self):
        """Test template autoname format: template_name-language_code."""
        doc = self._make_template_without_hooks(template_name="test_tmpl_autoname")
        self.assertEqual(doc.name, "test_tmpl_autoname-en")

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_templates.whatsapp_templates.make_post_request")
    def test_language_code_set_on_validate(self, mock_post):
        """Test language_code is derived from language field on validate."""
        mock_post.return_value = {}
        doc = self._make_template_without_hooks(template_name="test_tmpl_langcode")
        doc.language_code = ""
        doc.language = frappe.db.get_value("Language", {"language_code": "en"}) or "en"
        doc.validate()
        self.assertTrue(len(doc.language_code) > 0)

    def test_set_whatsapp_account_default(self):
        """Test whatsapp_account is set to default if missing."""
        doc = self._make_template_without_hooks(
            template_name="test_tmpl_default_acct",
            whatsapp_account=""
        )
        doc.whatsapp_account = ""
        doc.set_whatsapp_account()
        self.assertTrue(len(doc.whatsapp_account) > 0)

    def test_get_absolute_path_public_files(self):
        """Test get_absolute_path for public files."""
        doc = self._make_template_without_hooks(template_name="test_tmpl_path")
        path = doc.get_absolute_path("/files/test_image.png")
        self.assertIn("/public/files/test_image.png", path)

    def test_get_absolute_path_private_files(self):
        """Test get_absolute_path for private files."""
        doc = self._make_template_without_hooks(template_name="test_tmpl_priv_path")
        path = doc.get_absolute_path("/private/files/test_doc.pdf")
        self.assertIn("/private/files/test_doc.pdf", path)

    def test_get_header_text(self):
        """Test get_header for TEXT header type."""
        doc = self._make_template_without_hooks(
            template_name="test_tmpl_hdr_text",
            header_type="TEXT",
            header="Order Update"
        )
        header = doc.get_header()
        self.assertEqual(header["type"], "header")
        self.assertEqual(header["format"], "TEXT")
        self.assertEqual(header["text"], "Order Update")

    def test_get_header_text_with_sample(self):
        """Test get_header for TEXT header with sample values."""
        doc = self._make_template_without_hooks(
            template_name="test_tmpl_hdr_sample",
            header_type="TEXT",
            header="Hello {{1}}",
            sample_values="John"
        )
        doc.sample = "John"
        header = doc.get_header()
        self.assertEqual(header["format"], "TEXT")
        self.assertIn("example", header)
        self.assertEqual(header["example"]["header_text"], ["John"])

    def test_get_settings(self):
        """Test get_settings loads WhatsApp Account credentials."""
        doc = self._make_template_without_hooks(template_name="test_tmpl_settings")
        doc.get_settings()
        self.assertEqual(doc._url, "https://graph.facebook.com")
        self.assertEqual(doc._version, "v17.0")
        self.assertEqual(doc._business_id, "tmpl_test_business_id")

    def test_after_insert_sets_actual_name(self):
        """Template insert should set normalized actual_name."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "template_name": "test_tmpl_insert",
            "template": "Test body {{1}}",
            "sample_values": "World",
            "category": "TRANSACTIONAL",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": "Test WA Tmpl Account",
        })
        doc.insert(ignore_permissions=True)

        self.assertEqual(doc.actual_name, "test_tmpl_insert")
        self.assertEqual(doc.language_code, "en")

    def test_after_insert_with_footer(self):
        """Template footer should be saved locally."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "template_name": "test_tmpl_footer",
            "template": "Body text",
            "footer": "Reply STOP to opt out",
            "category": "MARKETING",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": "Test WA Tmpl Account",
        })
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.footer, "Reply STOP to opt out")

    def test_after_insert_with_buttons(self):
        """Template buttons should be saved locally."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "template_name": "test_tmpl_buttons",
            "template": "Click below",
            "category": "TRANSACTIONAL",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": "Test WA Tmpl Account",
        })
        doc.append("buttons", {
            "button_type": "Quick Reply",
            "button_label": "Yes",
        })
        doc.append("buttons", {
            "button_type": "Visit Website",
            "button_label": "Visit",
            "website_url": "https://example.com",
            "url_type": "Static",
        })
        doc.insert(ignore_permissions=True)

        self.assertEqual(len(doc.buttons), 2)
        self.assertEqual(doc.buttons[0].button_type, "Quick Reply")
        self.assertEqual(doc.buttons[1].button_type, "Visit Website")

    def test_on_trash_deletes_locally(self):
        """Template delete should work without external API dependency."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Templates",
            "template_name": "test_tmpl_trash",
            "template": "Delete me",
            "category": "TRANSACTIONAL",
            "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
            "language_code": "en",
            "whatsapp_account": "Test WA Tmpl Account",
        })
        doc.insert(ignore_permissions=True)
        name = doc.name
        doc.delete()
        self.assertFalse(frappe.db.exists("WhatsApp Templates", name))

    def test_fetch_templates_from_meta(self):
        """Meta fetch is intentionally disabled in Evolution-only mode."""
        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_templates.whatsapp_templates import fetch
        self.assertRaisesRegex(
            frappe.ValidationError,
            "Meta sync is removed",
            fetch,
        )

    def test_upsert_doc_without_hooks(self):
        """Test upsert_doc_without_hooks inserts and updates correctly."""
        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_templates.whatsapp_templates import upsert_doc_without_hooks

        doc = self._make_template_without_hooks(template_name="test_tmpl_upsert")

        # Update template text
        doc.template = "Updated body text"
        upsert_doc_without_hooks(doc, "WhatsApp Button", "buttons")

        doc.reload()
        self.assertEqual(doc.template, "Updated body text")
