"""Offline security, receipt recovery, incoming and committed-queue tests."""
from unittest import TestCase
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from whatsapp_evolution.utils import receipts
from whatsapp_evolution import incoming
from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message import whatsapp_message as messages
from whatsapp_evolution.whatsapp_evolution.doctype.bulk_whatsapp_message.bulk_whatsapp_message import BulkWhatsAppMessage


class TestWebhookSecurity(TestCase):
    def test_rejected_callback_is_not_parsed(self):
        from whatsapp_evolution.whatsapp_evolution.providers import evolution
        with patch.object(evolution,'frappe') as fake, patch.object(receipts,'authenticate_webhook',return_value=False):
            handler=evolution.handle_webhook
            while hasattr(handler,'__wrapped__'):handler=handler.__wrapped__
            self.assertEqual(handler(),'Unauthorized webhook')
            fake.local.request.get_json.assert_not_called()
    def test_secret_required_and_compared(self):
        for configured, supplied, expected in [(None, '', False), ('secret', '', False),
                ('secret', 'wrong', False), ('secret', 'secret', True)]:
            with self.subTest(configured=bool(configured), supplied=bool(supplied)), patch.object(receipts, 'frappe') as fake:
                fake.conf.get.return_value = configured
                self.assertEqual(receipts.authenticate_webhook(SimpleNamespace(headers={'X-Webhook-Secret': supplied})), expected)
                if not expected:
                    self.assertEqual(fake.local.response.http_status_code, 401)


class TestReceiptStorage(TestCase):
    def setUp(self):
        clock=patch.object(receipts,'now_datetime',return_value='2026-10-28')
        clock.start()
        self.addCleanup(clock.stop)
    def test_receipt_before_message_is_retained(self):
        with patch.object(receipts, 'frappe') as fake:
            fake.db.exists.return_value = False
            fake.db.get_value.return_value = None
            self.assertFalse(receipts.apply_receipt('786', 'id', 'Read'))
            fake.get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True, ignore_if_duplicate=True)
            fake.db.set_value.assert_not_called()

    def test_status_lock_and_no_downgrade(self):
        with patch.object(receipts, 'frappe') as fake:
            fake.db.exists.return_value = True
            fake.db.get_value.side_effect = ['2026-10-28', Row(name='msg', status='Read', delivered_at='2026-10-28')]
            self.assertTrue(receipts.apply_receipt('786', 'id', 'Delivered'))
            fake.db.set_value.assert_called_once()
            self.assertEqual(fake.db.set_value.call_args.args[0], 'WhatsApp Delivery Receipt')
            self.assertTrue(fake.db.get_value.call_args.kwargs['for_update'])

    def test_read_advances_and_publishes_after_commit(self):
        with patch.object(receipts, 'frappe') as fake:
            fake.db.exists.return_value = True
            fake.db.get_value.side_effect = ['2026-10-28', Row(name='msg', status='Delivered')]
            self.assertTrue(receipts.apply_receipt('786', 'id', 'Read'))
            values = fake.db.set_value.call_args_list[0].args[2]
            self.assertEqual(values['status'], 'Read')
            self.assertIn('read_at', values)
            self.assertTrue(fake.publish_realtime.call_args.kwargs['after_commit'])

    def test_manual_refresh_checks_permissions_before_lookup(self):
        with patch.object(receipts, 'frappe') as fake, patch.object(receipts, 'refresh_receipts') as refresh:
            fake.get_doc.return_value.check_permission.side_effect = PermissionError
            handler=receipts.refresh_message_status
            while hasattr(handler, '__wrapped__'): handler=handler.__wrapped__
            with self.assertRaises(PermissionError):handler('private-msg')
            refresh.assert_not_called()

    def test_provider_lookup_cannot_apply_another_message_receipt(self):
        from whatsapp_evolution import utils
        with patch.object(receipts,'frappe') as fake, patch.object(utils,'get_evolution_settings',return_value={
            'whatsapp_account':'786','evolution_api_base':'https://provider.example','evolution_instance':'786'}), \
             patch.object(receipts.requests,'post') as post, patch.object(receipts,'apply_receipt') as apply:
            fake.get_doc.return_value=Row(type='Outgoing',message_id='ours',whatsapp_account='786')
            fake.db.get_value.return_value='Active'
            post.return_value.json.return_value=[{'keyId':'another','fromMe':True,'status':'READ'},
                                                {'keyId':'ours','fromMe':False,'status':'READ'}]
            receipts.refresh_receipts('msg')
            apply.assert_not_called()
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_hourly_recovery_only_checks_bounded_recent_outgoing(self):
        with patch.object(receipts,'frappe') as fake, patch.object(receipts,'refresh_receipts') as refresh, \
             patch.object(receipts,'add_to_date',return_value='cutoff'):
            fake.get_all.side_effect=[[],[Row(name='msg')]]
            receipts.reconcile_receipts()
            self.assertEqual(fake.get_all.call_args.kwargs['limit_page_length'],30)
            self.assertEqual(fake.get_all.call_args.kwargs['filters']['type'],'Outgoing')
            refresh.assert_called_once_with('msg')


class Row(dict):
    __getattr__ = dict.get


class TestIncomingV16(TestCase):
    def test_missing_employee_mobile_uses_cell_number(self):
        with patch.object(incoming, 'frappe') as fake:
            fake.db.exists.side_effect=lambda dt, name: name == 'Employee'
            fake.db.has_column.side_effect=lambda dt, field: field == 'cell_number'
            fake.get_all.side_effect=[[], [Row(name='EMP-1', cell_number='923001234567')]]
            self.assertEqual(incoming._find_reference_by_number('923001234567'), ('Employee', 'EMP-1'))
            self.assertEqual(fake.get_all.call_args.kwargs['fields'], ['name','cell_number'])

    def test_short_numbers_do_not_match_an_unrelated_phone(self):
        self.assertFalse(incoming._numbers_match('1234', '923001234567'))

    def test_unknown_number_returns_no_party(self):
        with patch.object(incoming, 'frappe') as fake:
            fake.get_all.return_value=[]
            fake.db.exists.return_value=False
            self.assertEqual(incoming._find_reference_by_number('923001234567'), (None,None))

    def test_media_parser_preserves_type_and_key(self):
        parsed=EvolutionProvider({}).parse_incoming({'event':'messages.upsert','data':{
            'key':{'id':'id','remoteJid':'923001234567@s.whatsapp.net'},
            'message':{'documentWithCaptionMessage':{'message':{'documentMessage':{'caption':'Invoice', 'mimetype':'application/pdf'}}}}
        }})
        self.assertEqual(parsed['content_type'],'document')
        self.assertEqual(parsed['media_mimetype'],'application/pdf')
        self.assertEqual(parsed['media_key']['id'],'id')
        self.assertEqual(parsed['body'],'Invoice')

    def test_media_failure_does_not_drop_incoming_text(self):
        with patch.object(incoming, 'frappe') as fake, patch.object(incoming, '_find_reference_by_number',return_value=(None,None)), patch.object(incoming, '_save_incoming_media',side_effect=ValueError):
            fake.db.get_value.return_value=None
            fake.get_doc.return_value.content_type='document'
            fake.get_doc.return_value.name='incoming-id'
            result=incoming._store_incoming_message({'from':'923001234567','message_id':'id','body':'<script>bad</script>','content_type':'document'},'786')
            self.assertEqual(result,'incoming-id')
            self.assertIn('&lt;script&gt;',fake.get_doc.call_args.args[0]['message'])
            fake.get_doc.return_value.insert.assert_called_once()
            fake.db.set_value.assert_called_once()


class TestCommittedQueue(TestCase):
    def test_direct_document_insert_does_not_send(self):
        doc=SimpleNamespace(type='Outgoing', message_id='', flags=SimpleNamespace(), notify=MagicMock())
        with patch.object(doc,'notify') as send, patch.object(messages,'enqueue_existing_message') as enqueue:
            messages.WhatsAppMessage.before_insert(doc)
            self.assertEqual(doc.status,'Queued')
            send.assert_not_called()
            doc.name='queued-id'
            messages.WhatsAppMessage.after_insert(doc)
            enqueue.assert_called_once_with('queued-id')

    def test_enqueue_is_after_commit_with_stable_job_id(self):
        with patch.object(messages,'frappe') as fake:
            messages.enqueue_existing_message('msg')
            self.assertTrue(fake.enqueue.call_args.kwargs['enqueue_after_commit'])
            self.assertEqual(fake.enqueue.call_args.kwargs['job_id'],'wa-send-msg')

    def test_bulk_retry_enqueues_without_sending(self):
        from whatsapp_evolution.whatsapp_evolution.doctype.bulk_whatsapp_message import bulk_whatsapp_message as bulk
        obj=SimpleNamespace(name='bulk', check_permission=MagicMock())
        with patch.object(obj,'check_permission') as permission, patch.object(bulk,'frappe') as fake, patch.object(bulk,'enqueue_existing_message') as enqueue, patch.object(bulk,'_',side_effect=lambda x:x):
            fake.get_all.return_value=[Row(name='failed-id')]
            BulkWhatsAppMessage.retry_failed(obj)
            permission.assert_called_once_with('write')
            enqueue.assert_called_once_with('failed-id')
            self.assertEqual(fake.get_all.call_args.kwargs['filters']['status'],'Failed')
            self.assertIn('message_id',fake.get_all.call_args.kwargs['filters'])
