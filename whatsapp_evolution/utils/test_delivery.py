"""Offline delivery regression tests. No site, live API or message sends."""

import json
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
import requests

from whatsapp_evolution.utils import delivery
from whatsapp_evolution.whatsapp_evolution.providers import evolution
from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider, EvolutionSendError
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message import whatsapp_message as messages
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification import whatsapp_notification as notifications
from whatsapp_evolution.whatsapp_evolution.doctype.bulk_whatsapp_message.bulk_whatsapp_message import BulkWhatsAppMessage


class TestReceiptWebhook(TestCase):
    def run_receipt(self, status="DELIVERY_ACK", current="Success", found=True, account="786", from_me=True, batch=None):
        payload = {
            "event": "messages.update", "instance": "786",
            "data": {"keyId": "provider-id", "id": "database-row-id",
                     "status": status, "fromMe": from_me,
                     "remoteJid": "923001234567@s.whatsapp.net"},
        }
        if batch is not None:
            payload["data"] = batch
        with patch.object(evolution, "frappe") as fake, \
             patch("whatsapp_evolution.utils.receipts.authenticate_webhook", return_value=True), \
             patch("whatsapp_evolution.utils.receipts.apply_receipt") as apply, \
             patch.object(evolution, "_account_name_from_instance", return_value=account), \
             patch.object(evolution, "_log_webhook_debug"), \
             patch.object(evolution, "_find_recent_outgoing_by_number") as fallback:
            fake.local.request.get_json.return_value = payload
            fake.get_single.return_value.as_dict.return_value = {}
            fake.get_all.return_value = [{"name": "target-message", "status": current}] if found else []
            def apply_to_fake(account, message_id, status):
                if found and evolution._status_rank(status) > evolution._status_rank(current):
                    fake.db.set_value("WhatsApp Message", "target-message", "status", status)
            apply.side_effect = apply_to_fake
            handler = evolution.handle_webhook
            while hasattr(handler, "__wrapped__"):
                handler = handler.__wrapped__
            handler()
            fake.log_error.assert_not_called()
            fallback.assert_not_called()
            return fake

    def test_v2_receipt_updates_exact_account_message(self):
        fake = self.run_receipt()
        fake.db.set_value.assert_called_once_with("WhatsApp Message", "target-message", "status", "Delivered")
        filters = fake.get_all.call_args.kwargs["filters"]
        self.assertIn("provider-id", filters["message_id"][1])
        self.assertNotIn("database-row-id", filters["message_id"][1])
        self.assertEqual(filters["whatsapp_account"], "786")
        self.assertEqual(filters["type"], "Outgoing")

    def test_read_receipt_advances_delivered_message(self):
        fake = self.run_receipt(status="READ", current="Delivered")
        fake.db.set_value.assert_called_once_with("WhatsApp Message", "target-message", "status", "Read")

    def test_read_receipt_after_ignored_entry_in_batch_is_processed(self):
        fake = self.run_receipt(current="Delivered", batch=[
            {"keyId": "incoming-id", "status": "READ", "fromMe": False},
            {"keyId": "provider-id", "status": "READ", "fromMe": True},
        ])
        fake.db.set_value.assert_called_once_with("WhatsApp Message", "target-message", "status", "Read")

    def test_read_receipt_after_malformed_entry_is_processed(self):
        fake = self.run_receipt(current="Delivered", batch=[None,
            {"keyId": "provider-id", "status": 4, "fromMe": True},
        ])
        fake.db.set_value.assert_called_once_with("WhatsApp Message", "target-message", "status", "Read")

    def test_empty_receipt_batch_does_not_change_status(self):
        self.run_receipt(batch=[]).db.set_value.assert_not_called()

    def test_delayed_delivery_receipt_cannot_downgrade_read(self):
        self.run_receipt(current="Read").db.set_value.assert_not_called()

    def test_unmatched_receipt_cannot_update_another_send(self):
        self.run_receipt(found=False).db.set_value.assert_not_called()

    def test_unknown_account_and_incoming_receipts_are_ignored(self):
        for kwargs in ({"account": None}, {"from_me": False}):
            with self.subTest(kwargs=kwargs):
                fake = self.run_receipt(**kwargs)
                fake.get_all.assert_not_called()
                fake.db.set_value.assert_not_called()


class FakeCache:
    def __init__(self):
        self.values = {}

    def make_key(self, key):
        return f"test-site|{key}"

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, script, count, key, owner, *args):
        if self.values.get(key) == owner:
            if "'del'" in script:
                del self.values[key]
            return 1
        return 0


def response(code=201, body=None):
    result = requests.Response()
    result.status_code = code
    result._content = json.dumps(body if body is not None else {"key": {"id": "sent-1"}}).encode()
    return result


class TestProviderDelivery(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.cache = FakeCache()
        self.stack.enter_context(patch.object(frappe, "cache", return_value=self.cache))
        self.post = self.stack.enter_context(patch(
            "whatsapp_evolution.whatsapp_evolution.providers.evolution.requests.post",
            return_value=response(),
        ))
        self.stack.enter_context(patch.object(EvolutionProvider, "_log_send_outcome"))
        self.provider = EvolutionProvider({
            "evolution_api_base": "https://evolution.example",
            "evolution_api_token": "test-token",
            "evolution_instance": "786",
            "evolution_api_version": "v2",
        })

    def send(self, media=False):
        if media:
            return self.provider.send_media("923001234567", "", media_bytes=b"pdf", filename="invoice.pdf")
        return self.provider.send_message("923001234567", "Hello")

    def test_success_then_duplicate_is_explicitly_skipped(self):
        self.assertEqual(self.send()["key"]["id"], "sent-1")
        skipped = self.send()
        self.assertTrue(skipped["skipped"])
        self.assertNotIn("id", skipped)
        self.assertEqual(delivery.send_status(skipped), "Skipped")
        self.assertEqual(self.post.call_count, 1)

    def test_media_duplicate_is_skipped(self):
        self.send(media=True)
        self.assertTrue(self.send(media=True)["skipped"])
        self.assertEqual(self.post.call_count, 1)

    def test_auth_failure_stops_and_allows_corrected_retry(self):
        self.post.return_value = response(401, {"error": "Unauthorized"})
        with self.assertRaisesRegex(EvolutionSendError, "401"):
            self.send()
        self.assertEqual(self.post.call_count, 1)
        self.assertFalse(self.cache.values)
        self.post.return_value = response()
        self.assertIn("key", self.send())

    def test_terminal_media_errors_do_not_try_other_payloads(self):
        for code, body in [(401, {"error": "Unauthorized"}), (403, {}),
                           (400, {"response": {"message": [{"exists": False}]}}),
                           (429, {"error": "rate limit"})]:
            with self.subTest(code=code):
                self.post.reset_mock()
                self.post.return_value = response(code, body)
                with self.assertRaises(EvolutionSendError) as caught:
                    self.send(media=True)
                self.assertFalse(caught.exception.delivery_unknown)
                self.assertFalse(caught.exception.allow_link_fallback)
                self.assertEqual(self.post.call_count, 1)
                self.assertFalse(self.cache.values)

    def test_timeout_is_uncertain_and_retains_duplicate_protection(self):
        self.post.side_effect = requests.ReadTimeout("response lost")
        with self.assertRaises(EvolutionSendError) as caught:
            self.send()
        self.assertEqual(delivery.send_status(error=caught.exception), "Needs Review")
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(self.send()["skipped"])

    def test_server_error_is_uncertain_without_resend(self):
        self.post.return_value = response(500, {"error": "upstream failure"})
        with self.assertRaises(EvolutionSendError) as caught:
            self.send(media=True)
        self.assertTrue(caught.exception.delivery_unknown)
        self.assertEqual(self.post.call_count, 1)
        self.assertTrue(self.cache.values)

    def test_schema_mismatch_can_use_compatible_payload(self):
        self.post.side_effect = [response(400, {"message": 'instance requires property "textMessage"'}), response()]
        self.assertIn("key", self.send())
        self.assertEqual(self.post.call_count, 2)

    def test_missing_route_moves_to_next_url_once(self):
        self.post.side_effect = [response(404, {"message": "Cannot POST /old-route"}), response()]
        self.assertIn("key", self.send())
        calls = self.post.call_args_list
        self.assertNotEqual(calls[0].args[0], calls[1].args[0])

    def test_missing_instance_does_not_probe_other_routes(self):
        self.post.return_value = response(404, {"message": "Instance does not exist"})
        with self.assertRaises(EvolutionSendError):
            self.send()
        self.assertEqual(self.post.call_count, 1)

    def test_redirect_not_followed(self):
        self.post.return_value = response(302, {})
        with self.assertRaises(EvolutionSendError):
            self.send()
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])
        self.assertEqual(self.post.call_count, 1)

    def test_reservations_are_account_scoped(self):
        self.send()
        self.provider.instance = "7823"
        self.assertIn("key", self.send())
        self.assertEqual(self.post.call_count, 2)

    def test_reservation_is_atomic_and_release_checks_owner(self):
        self.assertTrue(self.provider._acquire_dedup("text", "123", "hello"))
        reservation = self.provider._dedup_reservation
        self.assertFalse(self.provider._acquire_dedup("text", "123", "hello"))
        self.provider._release_dedup()
        self.assertEqual(self.cache.values[reservation[0]], reservation[1])

    def test_empty_text_does_not_reserve_or_send(self):
        with self.assertRaisesRegex(EvolutionSendError, "empty"):
            self.provider.send_message("123", "  ")
        self.post.assert_not_called()
        self.assertFalse(self.cache.values)

    def test_post_send_cache_outage_does_not_report_failure(self):
        with patch.object(self.cache, "eval", side_effect=ConnectionError("cache unavailable")):
            self.assertIn("key", self.send())
        self.assertEqual(self.post.call_count, 1)


class TestDeliveryRecords(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = self.stack.enter_context(patch.object(frappe, "db", MagicMock()))

    def test_queue_error_preserves_original_message(self):
        messages._update_queue_status("message-1", "Failed", details="Unauthorized")
        values = self.db.set_value.call_args.args[2]
        self.assertEqual(values["last_error"], "Unauthorized")
        self.assertNotIn("message", values)

    def test_queue_claim_is_locked_and_committed(self):
        self.db.get_value.return_value = "Queued"
        self.assertTrue(delivery.start_queued_message("message-1"))
        self.assertTrue(self.db.get_value.call_args.kwargs["for_update"])
        self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Started")
        self.db.commit.assert_called_once()

    def test_completed_or_uncertain_job_is_not_replayed(self):
        for status in ["Success", "Skipped", "Failed", "Started", "Needs Review", None]:
            self.db.get_value.return_value = status
            self.assertFalse(delivery.start_queued_message("message-1"))
        self.db.set_value.assert_not_called()

    def test_stale_attempts_need_review_and_active_jobs_are_untouched(self):
        now = datetime(2026, 10, 27, 12)
        rows = [frappe._dict(name="active"), frappe._dict(name="abandoned")]
        job = MagicMock()
        job.get_status.return_value = "started"
        self.db.get_value.return_value = frappe._dict(status="Started", modified=now - timedelta(days=2))
        with patch.object(delivery, "now_datetime", return_value=now), \
                patch.object(frappe, "get_all", return_value=rows), \
                patch.object(delivery, "get_job", side_effect=[job, None]):
            delivery.reconcile_stale_messages()
        self.db.set_value.assert_called_once()
        self.assertEqual(self.db.set_value.call_args.args[1], "abandoned")
        self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Needs Review")

    def test_reconciliation_rechecks_status_under_lock(self):
        self.db.get_value.return_value = frappe._dict(status="Success", modified=datetime(2026, 1, 1))
        with patch.object(frappe, "get_all", return_value=[frappe._dict(name="finished")]), \
                patch.object(delivery, "now_datetime", return_value=datetime(2026, 10, 27)), \
                patch.object(delivery, "get_job", return_value=None):
            delivery.reconcile_stale_messages()
        self.db.set_value.assert_not_called()

    def test_both_queue_workers_preserve_skipped_outcome(self):
        for worker in [messages.send_custom_now, messages.send_template_now]:
            with self.subTest(worker=worker.__name__):
                doc = MagicMock(status="Skipped", message_id="", last_error="Duplicate")
                with ExitStack() as stack:
                    stack.enter_context(patch.object(messages, "start_queued_message", return_value=True))
                    stack.enter_context(patch.object(messages, "_recent_duplicate_exists", return_value=False))
                    stack.enter_context(patch.object(messages, "_resolve_evolution_account", return_value="786"))
                    stack.enter_context(patch.object(messages, "is_evolution_enabled", return_value=True))
                    stack.enter_context(patch.object(messages, "get_template_preview", return_value={}))
                    stack.enter_context(patch.object(frappe, "get_doc", return_value=doc))
                    kwargs = {"template": "test"} if worker == messages.send_template_now else {}
                    worker(to="123", reference_doctype=None, reference_name=None, message="Hello",
                           queued_message_name="queued", **kwargs)
                self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Skipped")

    def test_manual_send_records_duplicate_as_skipped(self):
        doc = SimpleNamespace(
            is_evolution_enabled=lambda: True, message_type="Manual", to="123",
            whatsapp_account="786", content_type="text", message="Hello", attach="",
        )
        provider = MagicMock()
        provider.send_message.return_value = {"skipped": True, "reason": "Duplicate"}
        with patch.object(messages, "get_evolution_settings", return_value={}), \
                patch.object(messages, "EvolutionProvider", return_value=provider):
            messages.WhatsAppMessage.notify(doc, {})
        self.assertEqual(doc.status, "Skipped")
        self.assertEqual(doc.message_id, "")

    def test_media_timeout_does_not_fall_back_to_text(self):
        doc = SimpleNamespace(
            is_evolution_enabled=lambda: True, message_type="Manual", to="123",
            whatsapp_account="786", content_type="document", message="Hello",
            attach="https://files.example/invoice.pdf", reference_doctype=None,
        )
        provider = MagicMock()
        provider.send_media.side_effect = EvolutionSendError("Timeout", delivery_unknown=True)
        with patch.object(messages, "get_evolution_settings", return_value={}), \
                patch.object(messages, "EvolutionProvider", return_value=provider):
            with self.assertRaises(EvolutionSendError):
                messages.WhatsAppMessage.notify(doc, {})
        provider.send_message.assert_not_called()

    def test_bulk_does_not_count_a_suppressed_duplicate(self):
        bulk = MagicMock()
        bulk.use_template = False
        doc = MagicMock()
        doc.insert.side_effect = lambda **kwargs: setattr(doc, "status", "Skipped")
        with patch.object(frappe, "new_doc", return_value=doc):
            result = BulkWhatsAppMessage.create_single_message(bulk, {"mobile_number": "123"})
        self.assertFalse(result)
        bulk.db_set.assert_called_once_with("status", "Partially Failed")


class TestNotificationDelivery(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db = self.stack.enter_context(patch.object(frappe, "db", MagicMock()))
        self.stack.enter_context(patch.object(notifications, "get_whatsapp_account", return_value=SimpleNamespace(name="786")))
        self.stack.enter_context(patch.object(notifications, "_is_evolution_enabled", return_value=True))
        self.stack.enter_context(patch.object(notifications, "get_evolution_settings", return_value={}))
        self.provider = MagicMock()
        self.stack.enter_context(patch.object(notifications, "EvolutionProvider", return_value=self.provider))
        self.documents = []

        def get_doc(value, *args):
            if value == "WhatsApp Templates":
                return {"template": "Hello {{1}}"}
            doc = MagicMock()
            doc.name = f"attempt-{len(self.documents)}"
            doc.values = value
            doc.update.side_effect = doc.values.update
            original_message = value.get("message")

            def save(**kwargs):
                if doc.save.call_count > 1 and doc.values.get("message") != original_message:
                    raise frappe.ValidationError("Value cannot be changed for Message")

            doc.save.side_effect = save
            self.documents.append(doc)
            return doc

        self.stack.enter_context(patch.object(frappe, "get_doc", side_effect=get_doc))
        self.notification = SimpleNamespace(
            template="test-template", attach_document_print=False,
            set_property_after_alert=None, property_value=None,
        )
        self.data = {"to": "923001234567", "template": {"components": [
            {"type": "body", "parameters": [{"type": "text", "text": "Customer"}]},
        ]}}

    def notify(self):
        notifications.WhatsAppNotification.notify(
            self.notification, self.data,
            doc_data={"doctype": "Sales Invoice", "name": "INV-TEST"}, template_account="786",
        )

    def test_failure_is_visible_and_keeps_message_text(self):
        self.provider.send_message.side_effect = EvolutionSendError("HTTP 401 Unauthorized")
        self.notify()
        attempt = self.documents[0]
        self.assertEqual(attempt.values["message"], "Hello Customer")
        self.assertEqual(attempt.values["reference_name"], "INV-TEST")
        self.assertTrue(attempt.flags.skip_send)
        self.assertEqual(attempt.save.call_count, 1)
        attempt.save.assert_called_with(ignore_permissions=True)
        values = self.db.set_value.call_args.args[2]
        self.assertEqual(values["status"], "Failed")
        self.assertEqual(values["last_error"], "HTTP 401 Unauthorized")
        self.assertNotIn("message", values)
        self.assertEqual(self.documents[-1].values["meta_data"]["messages"], [attempt.name])

    def test_skipped_notification_not_logged_as_success(self):
        self.provider.send_message.return_value = {"skipped": True, "reason": "Duplicate"}
        self.notify()
        values = self.db.set_value.call_args.args[2]
        self.assertEqual(values["status"], "Skipped")
        self.assertEqual(values["message_id"], "")

    def test_uncertain_notification_needs_review(self):
        self.provider.send_message.side_effect = EvolutionSendError("Timeout", delivery_unknown=True)
        self.notify()
        self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Needs Review")

    def test_success_has_real_provider_id(self):
        self.provider.send_message.return_value = {"key": {"id": "sent-1"}}
        self.notify()
        self.assertEqual(self.db.set_value.call_args.args[2]["message_id"], "sent-1")
        self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Success")

    def test_rendered_content_is_persisted_without_resaving_immutable_message(self):
        self.provider.send_message.return_value = {"key": {"id": "sent-1"}}
        self.notify()
        self.documents[0].save.assert_called_once_with(ignore_permissions=True)
        rendered = self.db.set_value.call_args_list[0].args[2]
        self.assertEqual(rendered["message"], "Hello Customer")
        self.assertEqual(rendered["content_type"], "text")
        self.assertNotIn("to", rendered)
        self.assertNotIn("whatsapp_account", rendered)
        self.provider.send_message.assert_called_once_with("923001234567", "Hello Customer")

    def test_configuration_failure_is_visible_before_provider_call(self):
        with patch.object(notifications, "_is_evolution_enabled", side_effect=frappe.ValidationError("Missing settings")):
            self.notify()
        self.assertEqual(self.db.set_value.call_args.args[2]["status"], "Failed")
        self.assertEqual(self.db.set_value.call_args.args[2]["last_error"], "Missing settings")
        self.provider.send_message.assert_not_called()

    def test_uncertain_session_error_does_not_switch_accounts(self):
        self.provider.send_message.side_effect = EvolutionSendError("SessionError: No sessions", delivery_unknown=True)
        notifications.WhatsAppNotification.notify(self.notification, self.data, template_account="7823")
        self.provider.send_message.assert_called_once()
