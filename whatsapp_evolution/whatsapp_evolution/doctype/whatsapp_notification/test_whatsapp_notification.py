# Copyright (c) 2022, Shridhar Patil and Contributors
# See license.txt

from unittest.mock import patch, MagicMock

import frappe
from whatsapp_evolution.testing import IntegrationTestCase


class TestWhatsAppNotification(IntegrationTestCase):
    """Tests for WhatsApp Notification doctype."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._ensure_test_account()
        cls._ensure_test_template()

    @classmethod
    def _ensure_test_account(cls):
        if not frappe.db.exists("WhatsApp Account", "Test WA Notif Account"):
            account = frappe.get_doc({
                "doctype": "WhatsApp Account",
                "account_name": "Test WA Notif Account",
                "status": "Active",
                "url": "https://graph.facebook.com",
                "version": "v17.0",
                "phone_id": "notif_test_phone_id",
                "business_id": "notif_test_business_id",
                "app_id": "notif_test_app_id",
                "webhook_verify_token": "notif_test_verify_token",
                "is_default_incoming": 1,
                "is_default_outgoing": 1,
            })
            account.insert(ignore_permissions=True)
            frappe.db.commit()

    @classmethod
    def _ensure_test_template(cls):
        template_name = "test_notif_template-en"
        if not frappe.db.exists("WhatsApp Templates", template_name):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Templates",
                "template_name": "test_notif_template",
                "actual_name": "test_notif_template",
                "template": "Hello {{1}}, your order {{2}} is ready",
                "sample_values": "John,ORD-001",
                "category": "TRANSACTIONAL",
                "language": frappe.db.get_value("Language", {"language_code": "en"}) or "en",
                "language_code": "en",
                "whatsapp_account": "Test WA Notif Account",
                "status": "APPROVED",
                "id": "test_notif_template_id",
                "header_type": "",
            })
            doc.db_insert()
            frappe.db.commit()

    def setUp(self):
        # Set password within each test's transaction scope
        from frappe.utils.password import set_encrypted_password
        set_encrypted_password("WhatsApp Account", "Test WA Notif Account", "test_notif_token", "token")
        # Clear ALL defaults then set ours (db.set_value bypasses on_update hooks)
        frappe.db.sql("UPDATE `tabWhatsApp Account` SET is_default_outgoing=0, is_default_incoming=0")
        frappe.db.set_value("WhatsApp Account", "Test WA Notif Account", {
            "is_default_outgoing": 1,
            "is_default_incoming": 1,
        })

    def tearDown(self):
        for name in frappe.get_all("WhatsApp Notification", filters={"notification_name": ["like", "Test Notif%"]}, pluck="name"):
            frappe.delete_doc("WhatsApp Notification", name, force=True)
        frappe.db.commit()

    def _make_notification(self, **kwargs):
        doc = frappe.get_doc({
            "doctype": "WhatsApp Notification",
            "notification_name": kwargs.get("notification_name", "Test Notif 1"),
            "notification_type": kwargs.get("notification_type", "DocType Event"),
            "reference_doctype": kwargs.get("reference_doctype", "User"),
            "field_name": kwargs.get("field_name", "mobile_no"),
            "doctype_event": kwargs.get("doctype_event", "After Save"),
            "delay_seconds": kwargs.get("delay_seconds", 0),
            "template": kwargs.get("template", "test_notif_template-en"),
            "disabled": kwargs.get("disabled", 0),
            "condition": kwargs.get("condition", ""),
        })
        if kwargs.get("fields"):
            for f in kwargs["fields"]:
                doc.append("fields", {"field_name": f})
        doc.insert(ignore_permissions=True)
        return doc

    def test_notification_creation(self):
        """Test basic notification creation."""
        doc = self._make_notification()
        self.assertTrue(frappe.db.exists("WhatsApp Notification", doc.name))

    def test_notification_autoname(self):
        """Test notification is named from notification_name field."""
        doc = self._make_notification(notification_name="Test Notif Autoname")
        self.assertEqual(doc.name, "Test Notif Autoname")

    def test_validate_invalid_field_name(self):
        """Test validation fails for non-existent field name."""
        with self.assertRaises(frappe.ValidationError):
            self._make_notification(
                notification_name="Test Notif BadField",
                field_name="nonexistent_field_xyz"
            )

    def test_validate_valid_field_name(self):
        """Test validation passes for existing field name."""
        doc = self._make_notification(
            notification_name="Test Notif GoodField",
            field_name="email"
        )
        self.assertIsNotNone(doc.name)

    def test_validate_custom_attachment_requires_attach(self):
        """Test that custom_attachment requires either attach or attach_from_field."""
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Notification",
                "notification_name": "Test Notif NoAttach",
                "notification_type": "DocType Event",
                "reference_doctype": "User",
                "field_name": "mobile_no",
                "doctype_event": "After Save",
                "template": "test_notif_template-en",
                "custom_attachment": 1,
                "attach": "",
                "attach_from_field": "",
            })
            doc.insert(ignore_permissions=True)

    def test_validate_set_property_after_alert_field_exists(self):
        """Test set_property_after_alert references existing field."""
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc({
                "doctype": "WhatsApp Notification",
                "notification_name": "Test Notif BadProp",
                "notification_type": "DocType Event",
                "reference_doctype": "User",
                "field_name": "mobile_no",
                "doctype_event": "After Save",
                "template": "test_notif_template-en",
                "set_property_after_alert": "nonexistent_field_abc",
            })
            doc.insert(ignore_permissions=True)

    def test_format_number(self):
        """Test format_number strips leading +."""
        doc = self._make_notification(notification_name="Test Notif Format")
        self.assertEqual(doc.format_number("+919900112233"), "919900112233")
        self.assertEqual(doc.format_number("919900112233"), "919900112233")

    def test_on_trash_clears_cache(self):
        """Test on_trash clears the notification map cache."""
        doc = self._make_notification(notification_name="Test Notif Cache")
        frappe.cache().set_value("whatsapp_notification_map", {"test": True})

        # Call on_trash directly to avoid side effects from doc.delete()
        # (delete triggers run_server_script_for_doc_event which rebuilds cache)
        doc.on_trash()

        cached = frappe.cache().get_value("whatsapp_notification_map")
        self.assertFalse(cached)

    def test_on_update_clears_cache(self):
        """Test on_update clears the notification map cache."""
        doc = self._make_notification(notification_name="Test Notif Cache Update")
        frappe.cache().set_value("whatsapp_notification_map", {"test": True})

        doc.disabled = 1
        doc.save(ignore_permissions=True)

        cached = frappe.cache().get_value("whatsapp_notification_map")
        self.assertFalse(cached)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._was_recently_sent", return_value=False)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._acquire_notification_dedup", return_value=True)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message(self, mock_send, _mock_dedup, _mock_recent):
        """Test send_template_message sends correct data."""
        mock_send.return_value = {"id": "wamid.notif_test_1"}

        doc = self._make_notification(
            notification_name="Test Notif Send",
            field_name="mobile_no",
            fields=["first_name", "name"],
        )

        # Create a mock source document
        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112233"

        doc.send_template_message(user)

        self.assertTrue(mock_send.called)
        self.assertEqual(mock_send.call_args.args[0], "919900112233")
        self.assertIn("Hello", mock_send.call_args.args[1])

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._was_recently_sent", return_value=False)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._acquire_notification_dedup", return_value=True)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_without_field_name_uses_auto_resolution(self, mock_send, _mock_dedup, _mock_recent):
        """Test auto-recipient resolution when field_name is left blank."""
        mock_send.return_value = {"id": "wamid.notif_auto_1"}

        doc = self._make_notification(
            notification_name="Test Notif Auto Recipient",
            field_name="",
        )

        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112234"
        user.save(ignore_permissions=True)
        doc.send_template_message(user)

        self.assertTrue(mock_send.called)
        self.assertEqual(mock_send.call_args.args[0], "919900112234")

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._was_recently_sent", return_value=False)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._acquire_notification_dedup", return_value=True)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_job_ignores_extra_kwargs(self, mock_send, _mock_dedup, _mock_recent):
        """Queued job should ignore unexpected kwargs like track_job."""
        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import (
            send_template_message_job,
        )

        mock_send.return_value = {"id": "wamid.notif_job_extra"}

        self._make_notification(
            notification_name="Test Notif Queue Kwargs",
            field_name="mobile_no",
        )

        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112235"
        user.save(ignore_permissions=True)

        send_template_message_job(
            notification_name="Test Notif Queue Kwargs",
            reference_doctype="User",
            reference_name="Administrator",
            track_job=True,
        )

        self.assertTrue(mock_send.called)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._get_employee_cell_numbers")
    def test_get_recipient_numbers_includes_employee_cell_number(self, mock_get_employee_cell_numbers):
        """Test employee cell_number is included in recipient resolution."""
        mock_get_employee_cell_numbers.return_value = ["923001112233"]

        notif = self._make_notification(
            notification_name="Test Notif Employee Number",
            field_name="",
        )
        user = frappe.get_doc("User", "Administrator")
        doc_data = user.as_dict()
        doc_data["employee"] = "EMP-0001"

        recipients = notif.get_recipient_numbers(user, doc_data)
        self.assertIn("923001112233", recipients)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._was_recently_sent", return_value=False)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._acquire_notification_dedup", return_value=True)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_with_condition(self, mock_send, _mock_dedup, _mock_recent):
        """Test that condition evaluation works."""
        mock_send.return_value = {"id": "wamid.notif_cond_1"}

        doc = self._make_notification(
            notification_name="Test Notif Condition",
            field_name="mobile_no",
            condition="doc.enabled == 1",
        )

        # User with enabled=1 should trigger
        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112299"
        user.enabled = 1
        doc.send_template_message(user)
        self.assertTrue(mock_send.called)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_condition_not_met(self, mock_send):
        """Test that message is not sent when condition is not met."""
        doc = self._make_notification(
            notification_name="Test Notif NoSend",
            field_name="mobile_no",
            condition="doc.enabled == 0",
        )

        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112299"
        user.enabled = 1
        doc.send_template_message(user)

        self.assertFalse(mock_send.called)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.frappe.enqueue")
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_with_delay_enqueues_job(self, mock_send, mock_enqueue):
        """Test delayed notifications are queued and not sent inline."""
        doc = self._make_notification(
            notification_name="Test Notif Delayed",
            field_name="mobile_no",
            delay_seconds=5,
        )

        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112299"
        doc.send_template_message(user)

        self.assertFalse(mock_send.called)
        self.assertTrue(mock_enqueue.called)
        enqueue_kwargs = mock_enqueue.call_args.kwargs
        self.assertEqual(
            enqueue_kwargs.get("queue"),
            "short",
        )
        self.assertEqual(enqueue_kwargs.get("notification_name"), doc.name)
        self.assertEqual(enqueue_kwargs.get("reference_doctype"), "User")
        self.assertEqual(enqueue_kwargs.get("reference_name"), "Administrator")
        self.assertEqual(enqueue_kwargs.get("delay_seconds"), 5)

    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.sleep")
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._was_recently_sent", return_value=False)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification._acquire_notification_dedup", return_value=True)
    @patch("whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.EvolutionProvider.send_message")
    def test_send_template_message_job(self, mock_send, _mock_dedup, _mock_recent, mock_sleep):
        """Test background delayed worker sends notification."""
        mock_send.return_value = {"id": "wamid.notif_delay_1"}

        doc = self._make_notification(
            notification_name="Test Notif Delayed Worker",
            field_name="mobile_no",
            delay_seconds=10,
        )
        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112235"
        user.save(ignore_permissions=True)

        from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import (
            send_template_message_job,
        )

        send_template_message_job(
            notification_name=doc.name,
            reference_doctype="User",
            reference_name="Administrator",
            delay_seconds=10,
        )

        self.assertTrue(mock_sleep.called)
        self.assertTrue(mock_send.called)

    def test_disabled_notification_does_not_send(self):
        """Test that disabled notification does not trigger."""
        doc = self._make_notification(
            notification_name="Test Notif Disabled",
            disabled=1,
        )
        user = frappe.get_doc("User", "Administrator")
        user.mobile_no = "919900112299"

        # Should return early without sending
        result = doc.send_template_message(user)
        self.assertIsNone(result)

    def test_scheduler_event_notification(self):
        """Test creating a scheduler event notification."""
        doc = frappe.get_doc({
            "doctype": "WhatsApp Notification",
            "notification_name": "Test Notif Scheduler",
            "notification_type": "Scheduler Event",
            "reference_doctype": "User",
            "event_frequency": "Daily",
            "template": "test_notif_template-en",
            "condition": "",
        })
        doc.insert(ignore_permissions=True)
        self.assertEqual(doc.notification_type, "Scheduler Event")
        self.assertEqual(doc.event_frequency, "Daily")
