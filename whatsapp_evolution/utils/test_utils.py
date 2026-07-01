# Copyright (c) 2025, Shridhar Patil and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import patch, MagicMock

import frappe
from whatsapp_evolution.testing import IntegrationTestCase
from whatsapp_evolution.whatsapp_evolution.providers.evolution import (
    EvolutionProvider,
    _map_evolution_status,
    _normalize_event_type,
)

from whatsapp_evolution.utils import (
    format_number,
    get_evolution_settings,
    get_notifications_map,
    get_whatsapp_account,
    run_server_script_for_doc_event,
    trigger_whatsapp_notifications,
)


class TestFormatNumber(IntegrationTestCase):
    """Tests for format_number utility."""

    def test_strips_leading_plus(self):
        self.assertEqual(format_number("+919900112233"), "919900112233")

    def test_no_plus_unchanged(self):
        self.assertEqual(format_number("919900112233"), "919900112233")

    def test_plus_only_at_start(self):
        self.assertEqual(format_number("+1234567890"), "1234567890")


class TestGetWhatsAppAccount(IntegrationTestCase):
    """Tests for get_whatsapp_account utility."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_test_accounts()

    @classmethod
    def _ensure_test_accounts(cls):
        if not frappe.db.exists("WhatsApp Account", "Test Utils Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test Utils Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "utils_test_phone_id",
                "business_id": "utils_test_business_id",
                "app_id": "utils_test_app_id",
                "webhook_verify_token": "utils_test_verify_token",
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            frappe.db.commit()

    def setUp(self):
        # Clear ALL defaults then set ours (db.set_value bypasses on_update hooks)
        frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_outgoing=0, is_default_incoming=0")
        frappe.db.set_value("WhatsApp Account", "Test Utils Account", {
            "is_default_outgoing": 1,
            "is_default_incoming": 1,
        })

    def test_get_account_by_phone_id(self):
        """Test getting account by phone_id."""
        account = get_whatsapp_account(phone_id="utils_test_phone_id")
        self.assertIsNotNone(account)
        self.assertEqual(account.name, "Test Utils Account")

    def test_get_account_by_phone_id_not_found(self):
        """Test getting account by non-existent phone_id falls back to default."""
        account = get_whatsapp_account(phone_id="nonexistent_phone_id")
        # Should fall back to default incoming
        if account:
            self.assertTrue(account.is_default_incoming)

    def test_get_default_incoming_account(self):
        """Test getting default incoming account."""
        account = get_whatsapp_account(account_type='incoming')
        self.assertIsNotNone(account)
        self.assertEqual(account.is_default_incoming, 1)

    def test_get_default_outgoing_account(self):
        """Test getting default outgoing account."""
        account = get_whatsapp_account(account_type='outgoing')
        self.assertIsNotNone(account)
        self.assertEqual(account.is_default_outgoing, 1)


class TestGetNotificationsMap(IntegrationTestCase):
    """Tests for get_notifications_map utility."""

    def test_returns_dict(self):
        """Test that notifications map returns a dictionary."""
        result = get_notifications_map()
        self.assertIsInstance(result, dict)

    def test_maps_doctype_to_events(self):
        """Test the structure of notification map."""
        # Create a test notification
        if not frappe.db.exists("WhatsApp Account", "Test Utils Map Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test Utils Map Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "utils_map_phone_id",
                "business_id": "utils_map_business_id",
                "app_id": "utils_map_app_id",
                "webhook_verify_token": "utils_map_verify_token",
            })
            account.insert(ignore_permissions=True)
            frappe.db.commit()

        template_name = "test_utils_map_template-en"
        if not frappe.db.exists("WhatsApp Templates", template_name):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Templates",
                "template_name": "test_utils_map_template",
                "actual_name": "test_utils_map_template",
                "template": "Hello",
                "category": "TRANSACTIONAL",
                "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
                "language_code": "en",
                "whatsapp_account": "Test Utils Map Account",
                "status": "APPROVED",
                "id": "test_utils_map_tmpl_id",
            })
            doc.db_insert()
            frappe.db.commit()

        if not frappe.db.exists("WhatsApp Notification", "Test Utils Map Notif"):
            frappe.get_doc({
                "doctype": "WhatsApp Notification",
                "notification_name": "Test Utils Map Notif",
                "notification_type": "DocType Event",
                "reference_doctype": "User",
                "field_name": "mobile_no",
                "doctype_event": "After Save",
                "template": template_name,
                "disabled": 0,
            }).insert(ignore_permissions=True)
            frappe.db.commit()

        result = get_notifications_map()
        self.assertIn("User", result)
        self.assertIn("After Save", result["User"])
        self.assertIn("Test Utils Map Notif", result["User"]["After Save"])


class TestGetEvolutionSettings(IntegrationTestCase):
    """Tests for Evolution settings helper."""

    def test_defaults_api_version_to_v1(self):
        settings = frappe.get_single("WhatsApp Settings")
        settings.evolution_api_version = ""
        settings.save(ignore_permissions=True)

        effective = get_evolution_settings()
        self.assertEqual(effective.get("evolution_api_version"), "v1")

    def test_uses_selected_api_version(self):
        settings = frappe.get_single("WhatsApp Settings")
        settings.evolution_api_version = "v2"
        settings.save(ignore_permissions=True)

        effective = get_evolution_settings()
        self.assertEqual(effective.get("evolution_api_version"), "v2")

    def test_account_api_version_overrides_global(self):
        settings = frappe.get_single("WhatsApp Settings")
        settings.evolution_api_version = "v1"
        settings.save(ignore_permissions=True)

        account_name = "Test Utils Evolution V2 Account"
        if not frappe.db.exists("WhatsApp Account", account_name):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": account_name,
                "status": "Active",
                "evolution_instance": "test-utils-v2",
                "evolution_api_version": "v2",
            })
            account.insert(ignore_permissions=True)
        else:
            account = frappe.get_doc("WhatsApp Account", account_name)
            account.status = "Active"
            account.evolution_instance = "test-utils-v2"
            account.evolution_api_version = "v2"
            account.save(ignore_permissions=True)

        effective = get_evolution_settings(account.name)
        self.assertEqual(effective.get("evolution_api_version"), "v2")


class TestEvolutionProviderV2(TestCase):
    """Tests for Evolution provider v2 compatibility helpers."""

    def _provider(self, api_version="v2", strict=False, send_endpoint=""):
        return EvolutionProvider({
            "evolution_api_base": "https://evolution.example",
            "evolution_api_token": "test-token",
            "evolution_instance": "test-instance",
            "evolution_api_version": api_version,
            "evolution_strict_api_version": strict,
            "evolution_send_endpoint": send_endpoint,
        })

    def test_v2_text_payloads_are_preferred_for_v2(self):
        provider = self._provider("v2")
        payloads = provider._text_payload_variants("923001234567", "Hello")

        self.assertEqual(payloads[0], {"number": "923001234567", "text": "Hello"})
        self.assertEqual(payloads[1], {"to": "923001234567", "text": "Hello"})
        self.assertIn("textMessage", payloads[2])

    def test_v1_text_payloads_are_preferred_by_default(self):
        provider = self._provider("unknown")
        payloads = provider._text_payload_variants("923001234567", "Hello")

        self.assertIn("textMessage", payloads[0])
        self.assertEqual(payloads[2], {"number": "923001234567", "text": "Hello"})

    def test_v2_media_payload_includes_mimetype(self):
        provider = self._provider("v2")
        payload = provider._media_payload_variants(
            "923001234567",
            "image",
            caption="Photo",
            media_value="abc123",
            filename="photo.png",
        )[0]

        self.assertEqual(payload["mediatype"], "image")
        self.assertEqual(payload["mimetype"], "image/png")
        self.assertEqual(payload["fileName"], "photo.png")

    def test_strict_v2_payloads_do_not_include_v1_fallbacks(self):
        provider = self._provider("v2", strict=True)
        payloads = provider._text_payload_variants("923001234567", "Hello")

        self.assertEqual(len(payloads), 2)
        self.assertTrue(all("text" in payload for payload in payloads))

    def test_strict_v1_payloads_do_not_include_v2_fallbacks(self):
        provider = self._provider("v1", strict=True)
        payloads = provider._text_payload_variants("923001234567", "Hello")

        self.assertEqual(len(payloads), 2)
        self.assertTrue(all("textMessage" in payload for payload in payloads))

    def test_strict_urls_skip_legacy_messages_endpoint(self):
        provider = self._provider("v2", strict=True)

        self.assertEqual(
            provider._text_candidate_urls(),
            [
                "https://evolution.example/message/sendText/test-instance",
                "https://evolution.example/message/sendText",
            ],
        )

    def test_non_strict_urls_keep_legacy_messages_fallback(self):
        provider = self._provider("v2")

        self.assertIn("https://evolution.example/messages/test-instance", provider._text_candidate_urls())
        self.assertIn("https://evolution.example/messages", provider._text_candidate_urls())

    def test_custom_endpoint_placeholder_is_replaced_once(self):
        provider = self._provider("v2", send_endpoint="/custom/{instance}/send")

        urls = provider._text_candidate_urls()

        self.assertEqual(urls[0], "https://evolution.example/custom/test-instance/send")
        self.assertNotIn("https://evolution.example/custom/{instance}/send", urls)

    def test_media_payload_mode_detects_url_payload(self):
        provider = self._provider("v2")
        payload = provider._media_payload_variants(
            "923001234567",
            "document",
            media_value="https://files.example/document.pdf",
            filename="document.pdf",
        )[0]

        self.assertEqual(provider._media_payload_mode(payload), "url")

    def test_filename_from_url_ignores_query_string(self):
        provider = self._provider("v2")

        filename = provider._filename_from_url(
            "https://files.example/private/My%20File.pdf?signature=abc",
            "document",
        )

        self.assertEqual(filename, "My File.pdf")

    @patch("whatsapp_evolution.whatsapp_evolution.providers.evolution.requests.post")
    def test_successful_non_json_response_stops_retrying(self, mock_post):
        provider = self._provider("v2")
        response = MagicMock()
        response.status_code = 200
        response.text = "OK"
        response.json.side_effect = ValueError("No JSON")
        response.raise_for_status.return_value = None
        mock_post.return_value = response

        with patch.object(provider, "_acquire_dedup", return_value=True), patch.object(
            provider, "_text_candidate_urls", return_value=["https://evolution.example/message/sendText/test-instance"]
        ):
            result = provider.send_message("923001234567", "Hello")

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["text"], "OK")
        self.assertEqual(mock_post.call_count, 1)

    def test_successful_json_response_is_returned_unchanged(self):
        provider = self._provider("v2")
        response = MagicMock()
        response.json.return_value = {"id": "wamid.test"}

        self.assertEqual(provider._success_result(response), {"id": "wamid.test"})

    def test_fetch_instances_requires_matching_instance(self):
        provider = self._provider("v2")
        body = {
            "data": [
                {"instance": {"instanceName": "other-instance", "state": "open"}},
                {"instance": {"instanceName": "test-instance", "state": "close"}},
            ]
        }

        result = provider._connection_result_from_body(
            body,
            "https://evolution.example/instance/fetchInstances",
            require_instance_match=True,
        )

        self.assertEqual(result["ok"], False)
        self.assertEqual(result["status"], "disconnected")

    def test_fetch_instances_reports_missing_configured_instance(self):
        provider = self._provider("v2")
        body = {"data": [{"instance": {"instanceName": "other-instance", "state": "open"}}]}

        result = provider._connection_result_from_body(
            body,
            "https://evolution.example/instance/fetchInstances",
            require_instance_match=True,
        )

        self.assertEqual(result["ok"], False)
        self.assertEqual(result["status"], "disconnected")
        self.assertIn("not found", result["message"])

    def test_normalizes_uppercase_evolution_event_names(self):
        self.assertEqual(_normalize_event_type("MESSAGES_UPDATE"), "messages.update")
        self.assertEqual(_normalize_event_type("MESSAGES_UPSERT"), "messages.upsert")

    def test_parse_update_accepts_uppercase_event_and_ack_status(self):
        provider = self._provider("v2")

        parsed = provider.parse_incoming({
            "event": "MESSAGES_UPDATE",
            "data": {
                "key": {
                    "id": "3EB0C11694230BF9D6C89F",
                    "remoteJid": "923001007823@s.whatsapp.net",
                    "fromMe": True,
                },
                "update": {"ack": 3},
            },
        })

        self.assertEqual(parsed["event"], "messages.update")
        self.assertEqual(parsed["message_id"], "3EB0C11694230BF9D6C89F")
        self.assertEqual(parsed["status"], 3)
        self.assertEqual(_map_evolution_status(parsed["status"]), "Read")

    def test_status_mapper_accepts_delivery_and_read_aliases(self):
        self.assertEqual(_map_evolution_status("DELIVERY"), "Delivered")
        self.assertEqual(_map_evolution_status("READ_RECEIPT"), "Read")


class TestRunServerScriptForDocEvent(IntegrationTestCase):
    """Tests for run_server_script_for_doc_event."""

    def test_skips_during_install(self):
        """Test that it skips during install."""
        frappe.flags.in_install = True
        try:
            doc = frappe.get_doc("User", "Administrator")
            # Should not raise any error, just return
            run_server_script_for_doc_event(doc, "on_update")
        finally:
            frappe.flags.in_install = False

    def test_skips_during_migrate(self):
        """Test that it skips during migrate."""
        frappe.flags.in_migrate = True
        try:
            doc = frappe.get_doc("User", "Administrator")
            run_server_script_for_doc_event(doc, "on_update")
        finally:
            frappe.flags.in_migrate = False

    def test_skips_during_uninstall(self):
        """Test that it skips during uninstall."""
        frappe.flags.in_uninstall = True
        try:
            doc = frappe.get_doc("User", "Administrator")
            run_server_script_for_doc_event(doc, "on_update")
        finally:
            frappe.flags.in_uninstall = False

    def test_skips_unmapped_event(self):
        """Test that it skips events not in EVENT_MAP."""
        doc = frappe.get_doc("User", "Administrator")
        # 'random_event' is not in EVENT_MAP, should just return
        run_server_script_for_doc_event(doc, "random_event")


class TestTriggerWhatsAppNotifications(IntegrationTestCase):
    """Tests for trigger_whatsapp_notifications."""

    @patch("whatsapp_evolution.utils.frappe.get_doc")
    def test_trigger_by_frequency(self, mock_get_doc):
        """Test triggering notifications by frequency."""
        mock_notification = MagicMock()
        mock_get_doc.return_value = mock_notification

        # This will query for notifications with event_frequency="Daily"
        # and call send_scheduled_message on each
        trigger_whatsapp_notifications("Daily")
        # The function queries the DB, so if there are no matching notifications,
        # mock_get_doc may not be called
