frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Send Statement (WhatsApp)"), () => {
			open_customer_statement_whatsapp_dialog(frm);
		}, __("WhatsApp"));
	}
});

function open_customer_statement_whatsapp_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Send Customer Statement"),
		fields: [
			{
				label: __("Send Mode"),
				fieldname: "send_mode",
				fieldtype: "Select",
				options: "Template\nCustom",
				default: "Template",
				reqd: 1,
				change() {
					const mode = dialog.get_value("send_mode");
					dialog.set_df_property("template", "reqd", mode === "Template" ? 1 : 0);
				}
			},
			{
				label: __("Template"),
				fieldname: "template",
				fieldtype: "Link",
				options: "WhatsApp Templates",
				depends_on: "eval:doc.send_mode=='Template'",
				change() {
					load_statement_template_preview(frm, dialog);
				}
			},
			{
				label: __("Template Message (Editable)"),
				fieldname: "message",
				fieldtype: "Small Text",
				depends_on: "eval:doc.send_mode=='Template'"
			},
			{
				label: __("Custom Message"),
				fieldname: "custom_message",
				fieldtype: "Small Text",
				depends_on: "eval:doc.send_mode=='Custom'"
			},
			{
				fieldtype: "Section Break"
			},
			{
				label: __("Send To Contact"),
				fieldname: "contact",
				fieldtype: "Link",
				options: "Contact",
				get_query: () => ({
					query: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.get_linked_contacts_query",
					filters: { reference_doctype: "Customer", reference_name: frm.doc.name }
				}),
				change() {
					populate_mobile_from_contact(dialog);
				}
			},
			{
				label: __("Mobile No"),
				fieldname: "to",
				fieldtype: "Data",
				reqd: 1
			},
			{
				label: __("WhatsApp Account"),
				fieldname: "whatsapp_account",
				fieldtype: "Link",
				options: "WhatsApp Account",
				get_query: () => ({ filters: { status: "Active" } })
			},
			{
				fieldtype: "Section Break",
				label: __("Statement Options")
			},
			{
				label: __("Company"),
				fieldname: "company",
				fieldtype: "Link",
				options: "Company",
				reqd: 1
			},
			{
				label: __("Report"),
				fieldname: "report",
				fieldtype: "Select",
				options: "General Ledger\nAccounts Receivable",
				default: "General Ledger",
				reqd: 1
			},
			{
				label: __("From Date"),
				fieldname: "from_date",
				fieldtype: "Date",
				depends_on: "eval:doc.report=='General Ledger'"
			},
			{
				label: __("To Date"),
				fieldname: "to_date",
				fieldtype: "Date",
				depends_on: "eval:doc.report=='General Ledger'"
			},
			{
				label: __("Posting Date"),
				fieldname: "posting_date",
				fieldtype: "Date",
				depends_on: "eval:doc.report=='Accounts Receivable'"
			},
			{
				label: __("Include Ageing"),
				fieldname: "include_ageing",
				fieldtype: "Check",
				default: 0
			},
			{
				label: __("Ageing Based On"),
				fieldname: "ageing_based_on",
				fieldtype: "Select",
				options: "Due Date\nPosting Date",
				default: "Due Date"
			},
			{
				label: __("Orientation"),
				fieldname: "orientation",
				fieldtype: "Select",
				options: "Portrait\nLandscape",
				default: "Portrait"
			},
			{
				label: __("Account"),
				fieldname: "account",
				fieldtype: "Link",
				options: "Account"
			},
			{
				label: __("Currency"),
				fieldname: "currency",
				fieldtype: "Link",
				options: "Currency"
			},
			{
				label: __("Letter Head"),
				fieldname: "letter_head",
				fieldtype: "Link",
				options: "Letter Head"
			},
			{
				label: __("PDF Name"),
				fieldname: "pdf_name",
				fieldtype: "Data"
			}
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			if (values.send_mode === "Template" && !values.template) {
				frappe.msgprint(__("Please select a template"));
				return;
			}
			send_customer_statement(frm, dialog, values);
		}
	});

	dialog.show();
	set_default_whatsapp_account(dialog);
	load_customer_statement_defaults(frm, dialog);
}

function load_customer_statement_defaults(frm, dialog) {
	frappe.call({
		method: "whatsapp_evolution.customer_statement.get_customer_statement_defaults",
		args: { customer: frm.doc.name },
		callback(r) {
			const d = r.message || {};
			Object.keys(d).forEach((key) => {
				if (d[key] !== undefined && d[key] !== null) {
					dialog.set_value(key === "mobile_no" ? "to" : key, d[key]);
				}
			});
		}
	});
}

function populate_mobile_from_contact(dialog) {
	const contact_name = dialog.get_value("contact");
	if (!contact_name) return;
	frappe.call({
		method: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.get_contact_whatsapp_numbers",
		args: { contact_name, primary_only: 1 },
		callback(r) {
			const numbers = r.message || [];
			if (numbers.length) dialog.set_value("to", numbers[0]);
		}
	});
}

function set_default_whatsapp_account(dialog) {
	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "WhatsApp Account",
			filters: { is_default_outgoing: 1, status: "Active" },
			fieldname: "name"
		},
		callback(r) {
			if (r.message && r.message.name) {
				dialog.set_value("whatsapp_account", r.message.name);
			}
		}
	});
}

function load_statement_template_preview(frm, dialog) {
	const template = dialog.get_value("template");
	if (!template) {
		dialog.set_value("message", "");
		return;
	}
	frappe.call({
		method: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.get_template_preview",
		args: {
			template: template,
			reference_doctype: "Customer",
			reference_name: frm.doc.name
		},
		callback(r) {
			const preview = r.message || {};
			dialog.set_value("message", preview.rendered_text || preview.template_text || "");
		}
	});
}

function send_customer_statement(frm, dialog, values) {
	frappe.call({
		method: "whatsapp_evolution.customer_statement.send_customer_statement_whatsapp",
		args: {
			customer: frm.doc.name,
			to: values.to,
			send_mode: values.send_mode,
			template: values.template,
			message: values.message,
			custom_message: values.custom_message,
			company: values.company,
			report: values.report,
			from_date: values.from_date,
			to_date: values.to_date,
			posting_date: values.posting_date,
			include_ageing: values.include_ageing ? 1 : 0,
			ageing_based_on: values.ageing_based_on,
			orientation: values.orientation,
			currency: values.currency,
			account: values.account,
			letter_head: values.letter_head,
			pdf_name: values.pdf_name,
			whatsapp_account: values.whatsapp_account || ""
		},
		freeze: true,
		callback(r) {
			const queued = r && r.message && r.message.queued;
			frappe.msgprint(
				queued
					? __("Customer statement queued for WhatsApp send.")
					: __("Customer statement sent successfully.")
			);
			dialog.hide();
		}
	});
}
