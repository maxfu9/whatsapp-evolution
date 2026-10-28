// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on('WhatsApp Message', {
	refresh: function(frm) {
		if (frm.doc.type == 'Incoming'){
			frm.add_custom_button(__("Reply"), function(){
				frappe.new_doc("WhatsApp Message", {"to": frm.doc.from});

			});
		}

		if (!frm.is_new() && frm.doc.type === 'Outgoing' && frm.doc.message_id) {
			frm.add_custom_button(__('Refresh Status'), async () => {
				await frappe.call({method: 'whatsapp_evolution.utils.receipts.refresh_message_status',
					args: {name: frm.doc.name}, freeze: true});
				await frm.reload_doc();
			});
		}
		if (!frm._whatsapp_status_listener) {
			frm._whatsapp_status_listener = data => {
				if (data.doctype === frm.doctype && data.name === frm.doc.name && !frm.is_dirty()) frm.reload_doc();
			};
			frappe.realtime.on('doc_update', frm._whatsapp_status_listener);
		}
	}
});
