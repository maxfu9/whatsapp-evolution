// Copyright (c) 2025, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on("WhatsApp Account", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__('Test Connection'), function() {
			frappe.call({
				method: 'whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_settings.whatsapp_settings.test_evolution_connection',
				args: { account: frm.doc.name },
				callback: function(r) {
					const rows = (r.message && r.message.results) ? r.message.results : [];
					const x = rows.length ? rows[0] : null;
					if (!x) {
						frappe.msgprint(__('No result returned.'));
						return;
					}
					const state = x.ok ? 'Connected' : (x.status || 'Error');
					const color = x.ok ? 'green' : 'red';
					const info = x.message || x.url || '';
					frappe.msgprint({
						title: __('Evolution Connection Test'),
						indicator: x.ok ? 'green' : 'red',
						message: `<p><b>${frappe.utils.escape_html(frm.doc.name)}</b>: <span style="color:${color};">${frappe.utils.escape_html(state)}</span><br><small>${frappe.utils.escape_html(info)}</small></p>`
					});
				}
			});
		});
	},
});
