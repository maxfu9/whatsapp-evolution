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
						if (window.whatsapp_evolution_ui && window.whatsapp_evolution_ui.msgprint) {
							window.whatsapp_evolution_ui.msgprint(
								__('No active WhatsApp Account found.'),
								'warning',
								{ title: __('WhatsApp Connection Test') }
							);
						} else {
							frappe.msgprint({
								title: __('WhatsApp Connection Test'),
								indicator: 'orange',
								message: __('No active WhatsApp Account found.')
							});
						}
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
					const any_ok = rows.some(x => x.ok);
					if (window.whatsapp_evolution_ui && window.whatsapp_evolution_ui.msgprint) {
						window.whatsapp_evolution_ui.msgprint(
							html,
							any_ok ? 'success' : 'error',
							{ title: __('WhatsApp Connection Test') }
						);
					} else {
						frappe.msgprint({
							title: __('WhatsApp Connection Test'),
							indicator: any_ok ? 'green' : 'red',
							message: html
						});
					}
				}
			});
		});
	}
});
