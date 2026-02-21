// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt

frappe.ui.form.on('WhatsApp Settings', {
	refresh: function(frm) {
		frm.add_custom_button(__('Test Connection'), function() {
			frappe.call({
				method: 'whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_settings.whatsapp_settings.test_evolution_connection',
				callback: function(r) {
					const rows = (r.message && r.message.results) ? r.message.results : [];
					if (!rows.length) {
						frappe.msgprint(__('No active WhatsApp Account found.'));
						return;
					}
					let html = '<div>';
					rows.forEach(function(x) {
						const state = x.ok ? 'Connected' : (x.status || 'Error');
						const color = x.ok ? 'green' : 'red';
						const info = x.message || x.url || '';
						html += `
							<p style="margin-bottom:8px;">
								<b>${frappe.utils.escape_html(x.account || '-')}</b>
								${x.instance ? ` (${frappe.utils.escape_html(x.instance)})` : ''}
								: <span style="color:${color};">${frappe.utils.escape_html(state)}</span>
								${info ? `<br><small>${frappe.utils.escape_html(info)}</small>` : ''}
							</p>
						`;
					});
					html += '</div>';
					frappe.msgprint({
						title: __('Evolution Connection Test'),
						indicator: rows.some(x => x.ok) ? 'green' : 'red',
						message: html
					});
				}
			});
		});
	}
});
