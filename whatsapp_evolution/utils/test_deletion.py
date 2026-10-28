"""Deletion tests never contact WhatsApp or delete production documents."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch
from types import SimpleNamespace
import time
import requests

from whatsapp_evolution.utils import deletion


class Row(dict):
    __getattr__=dict.get


def message(**values):
    return Row({'name':'message-record','type':'Outgoing','status':'Read','message_id':'provider-id',
        'whatsapp_account':'786','to':'923001234567','creation':datetime(2030,1,1),
        'reference_doctype':'Sales Invoice','reference_name':'INV-1',**values})


class TestDeletionTriggers(TestCase):
    def setUp(self):
        clock=patch.object(deletion,'now_datetime',return_value=datetime(2030,1,1,1))
        clock.start();self.addCleanup(clock.stop)

    def test_message_delete_queues_only_after_commit(self):
        with patch.object(deletion,'frappe') as fake,patch.object(deletion,'_locked_message',return_value=message()):
            fake.db.exists.return_value=False
            deletion.message_deleted(SimpleNamespace(name='message-record'))
            self.assertTrue(fake.enqueue.call_args.kwargs['enqueue_after_commit'])
            self.assertEqual(fake.get_doc.call_args.args[0]['source_doctype'],'WhatsApp Message')

    def test_invoice_delete_captures_audit_and_unlinks_message(self):
        with patch.object(deletion,'frappe') as fake,patch.object(deletion,'_locked_message',return_value=message()):
            fake.get_all.return_value=['message-record'];fake.db.exists.return_value=False
            deletion.invoice_deleted(SimpleNamespace(doctype='Sales Invoice',name='INV-1'))
            self.assertEqual(fake.get_doc.call_args.args[0]['reference_name'],'INV-1')
            self.assertEqual(fake.get_doc.call_args.args[0]['source_doctype'],'Sales Invoice')
            fake.db.set_value.assert_called_once_with('WhatsApp Message','message-record',{'reference_doctype':'','reference_name':''})

    def test_active_send_blocks_delete(self):
        with patch.object(deletion,'frappe') as fake,patch.object(deletion,'_locked_message',return_value=message(status='Started')):
            fake.throw.side_effect=ValueError('send in progress')
            with self.assertRaises(ValueError):deletion.message_deleted(SimpleNamespace(name='message-record'))
            fake.enqueue.assert_not_called()

    def test_queued_message_is_stopped_without_remote_delete(self):
        with patch.object(deletion,'frappe') as fake:
            fake.db.exists.return_value=False
            deletion._queue_deletion(message(status='Queued',message_id='queue-placeholder'),'Sales Invoice','INV-1')
            fake.enqueue.assert_not_called()
            fake.db.set_value.assert_called_once_with('WhatsApp Message','message-record','status','Cancelled')
            self.assertEqual(fake.get_doc.call_args.args[0]['status'],'Stopped')

    def test_expired_message_not_enqueued(self):
        with patch.object(deletion,'frappe') as fake:
            fake.db.exists.return_value=False
            deletion._queue_deletion(message(creation=datetime(2029,1,1)),'Sales Invoice','INV-1')
            fake.enqueue.assert_not_called()
            self.assertEqual(fake.get_doc.call_args.args[0]['status'],'Expired')

    def test_sent_message_missing_provider_id_requires_review(self):
        with patch.object(deletion,'frappe') as fake:
            fake.db.exists.return_value=False
            deletion._queue_deletion(message(status='Success',message_id=''),'Sales Invoice','INV-1')
            fake.enqueue.assert_not_called()
            self.assertEqual(fake.get_doc.call_args.args[0]['status'],'Needs Review')

    def test_duplicate_request_not_requeued(self):
        with patch.object(deletion,'frappe') as fake:
            fake.db.exists.return_value=True
            deletion._queue_deletion(message(),'Sales Invoice','INV-1')
            fake.get_doc.assert_not_called();fake.enqueue.assert_not_called()

    def test_incoming_or_unlinked_message_never_revokes(self):
        for row in [message(type='Incoming'),message(reference_doctype='Customer')]:
            with self.subTest(row=row),patch.object(deletion,'frappe') as fake,patch.object(deletion,'_locked_message',return_value=row):
                deletion.message_deleted(SimpleNamespace(name='message-record'))
                fake.enqueue.assert_not_called()


class TestDeletionWorker(TestCase):
    def run_worker(self, source_exists=False, post_rows=None, delete_code=201, delete_error=None):
        row=Row(name='request-id',status='Pending',source_doctype='WhatsApp Message',source_name='deleted-msg',
                whatsapp_account='786',message_id='provider-id')
        with patch.object(deletion,'frappe') as fake, \
             patch('whatsapp_evolution.utils.get_evolution_settings',return_value={
                 'whatsapp_account':'786','evolution_api_base':'https://provider.example','evolution_instance':'786'}), \
             patch.object(deletion.requests,'post') as post,patch.object(deletion.requests,'delete') as delete, \
             patch.object(deletion,'_finish') as finish:
            fake.db.get_value.side_effect=[row,'Active']
            fake.db.exists.return_value=source_exists
            post.return_value.json.return_value={'messages':{'records':post_rows if post_rows is not None else [{
                'key':{'id':'provider-id','remoteJid':'original-chat@s.whatsapp.net','fromMe':True},
                'messageTimestamp':int(time.time())-60}]}}
            delete.return_value.status_code=delete_code
            if delete_error:delete.side_effect=delete_error
            deletion.process_deletion('request-id')
            return fake,post,delete,finish

    def test_source_still_exists_prevents_remote_delete(self):
        fake,post,delete,finish=self.run_worker(source_exists=True)
        post.assert_not_called();delete.assert_not_called()
        self.assertEqual(finish.call_args.args[1],'Stopped')

    def test_exact_original_key_used_not_guessed_phone(self):
        fake,post,delete,finish=self.run_worker()
        self.assertEqual(delete.call_args.kwargs['json']['remoteJid'],'original-chat@s.whatsapp.net')
        self.assertFalse(delete.call_args.kwargs['allow_redirects'])
        self.assertEqual(finish.call_args.args[1],'Requested')

    def test_unmatched_key_never_deletes(self):
        fake,post,delete,finish=self.run_worker(post_rows=[{'key':{'id':'another','fromMe':True}}])
        delete.assert_not_called();self.assertEqual(finish.call_args.args[1],'Failed')

    def test_incoming_key_never_deletes(self):
        fake,post,delete,finish=self.run_worker(post_rows=[{'key':{'id':'provider-id','fromMe':False}}])
        delete.assert_not_called();self.assertEqual(finish.call_args.args[1],'Failed')

    def test_provider_timestamp_is_checked(self):
        fake,post,delete,finish=self.run_worker(post_rows=[{'key':{'id':'provider-id','fromMe':True,'remoteJid':'chat'},'messageTimestamp':int(time.time())-3*86400}])
        delete.assert_not_called();self.assertEqual(finish.call_args.args[1],'Expired')

    def test_timeout_does_not_claim_success_or_retry(self):
        fake,post,delete,finish=self.run_worker(delete_error=requests.Timeout())
        delete.assert_called_once();self.assertEqual(finish.call_args.args[1],'Needs Review')

    def test_server_error_outcome_is_unknown(self):
        fake,post,delete,finish=self.run_worker(delete_code=500)
        self.assertEqual(finish.call_args.args[1],'Needs Review')

    def test_rejected_request_is_failed(self):
        fake,post,delete,finish=self.run_worker(delete_code=401)
        self.assertEqual(finish.call_args.args[1],'Failed')
