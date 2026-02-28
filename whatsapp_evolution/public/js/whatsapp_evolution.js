// WhatsApp Evolution Dialogue - v2.2 (Manual Validation for Attachments)
$(document).on("app_ready", () => {
	frappe.router.on("change", () => {
		const route = frappe.get_route();
		if (!(route && route[0] === "Form" && route[1])) {
			return;
		}

		frappe.ui.form.on(route[1], {
			refresh(frm) {
				if (frm.is_new()) {
					return;
				}

				frm.page.add_menu_item(__("Send To WhatsApp"), () => {
					open_whatsapp_dialog(frm);
				});
			}
		});
	});
});

function open_whatsapp_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Send To WhatsApp"),
		fields: [
			{
				label: __("Send Mode"),
				fieldname: "send_mode",
				fieldtype: "Select",
				options: ["Template", "Custom"],
				default: "Template",
				reqd: 1,
				change() {
					toggle_mode_fields(dialog);
				}
			},
			{
				label: __("Template"),
				fieldname: "template",
				fieldtype: "Link",
				options: "WhatsApp Templates",
				depends_on: "eval:doc.send_mode=='Template'",
				reqd: 1,
				get_query: () => ({
					filters: { for_doctype: frm.doc.doctype }
				}),
				change() {
					load_template_preview(frm, dialog);
				}
			},
			{
				label: __("Template Message (Editable)"),
				fieldname: "template_body",
				fieldtype: "Small Text",
				depends_on: "eval:doc.send_mode=='Template'",
				description: __("Edit before sending if needed")
			},
			{
				fieldname: "template_raw",
				fieldtype: "Data",
				hidden: 1
			},
			{
				label: __("Custom Message"),
				fieldname: "custom_message",
				fieldtype: "Small Text",
				depends_on: "eval:doc.send_mode=='Custom'"
			},
			{
				label: __("Content Type"),
				fieldname: "content_type",
				fieldtype: "Select",
				options: "text\ndocument\nimage\nvideo\naudio",
				default: "text",
				depends_on: "eval:doc.send_mode=='Custom'",
				change() {
					toggle_attachment_field(dialog);
				}
			},
			{
				label: __("Attachment"),
				fieldname: "attach",
				fieldtype: "Attach",
				depends_on: "eval:doc.send_mode=='Custom' && doc.content_type!='text' && !doc.attach_document_print"
			},
			{
				label: __("Attach Document Print (PDF)"),
				fieldname: "attach_document_print",
				fieldtype: "Check",
				default: 0,
				change() {
					const checked = dialog.get_value("attach_document_print");
					// When PDF print is chosen, hide the manual attach field and make it non-required
					toggle_attachment_field(dialog);
					if (checked && !dialog.get_value("print_format")) {
						frappe.call({
							method: "frappe.client.get_value",
							args: {
								doctype: "DocType",
								filters: { name: frm.doc.doctype },
								fieldname: "default_print_format"
							},
							callback: function (r) {
								if (r.message && r.message.default_print_format) {
									dialog.set_value("print_format", r.message.default_print_format);
								}
							}
						});
					}
				}
			},
			{
				label: __("Print Format"),
				fieldname: "print_format",
				fieldtype: "Link",
				options: "Print Format",
				depends_on: "eval:doc.attach_document_print==1",
				get_query: () => ({
					filters: { doc_type: frm.doc.doctype }
				})
			},
			{
				label: __("No Letterhead"),
				fieldname: "no_letterhead",
				fieldtype: "Check",
				default: 0,
				depends_on: "eval:doc.attach_document_print==1"
			},
			{
				label: __("Send To Contact"),
				fieldname: "contact",
				fieldtype: "Link",
				options: "Contact",
				get_query: () => ({
					query: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.get_linked_contacts_query",
					filters: {
						reference_doctype: frm.doc.doctype,
						reference_name: frm.doc.name
					}
				}),
				change() {
					populate_mobile_from_contact(dialog);
				}
			},
			{
				label: __("Mobile No"),
				fieldname: "mobile_no",
				fieldtype: "Data",
				reqd: 1,
				description: __("Use country code, e.g. 923001234567")
			},
			{
				label: __("WhatsApp Account"),
				fieldname: "whatsapp_account",
				fieldtype: "Link",
				options: "WhatsApp Account",
				get_query: () => ({
					filters: { status: "Active" }
				})
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			if (values.send_mode === "Custom" && values.content_type !== "text" && !values.attach && !values.attach_document_print) {
				frappe.msgprint(__("Please attach a file or select 'Attach Document Print'"));
				return;
			}
			send_whatsapp_message(frm, dialog, values);
		}
	});

	autofill_mobile_from_doc(frm, dialog);
	toggle_mode_fields(dialog);
	set_default_whatsapp_account(dialog);

	dialog.show();
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

function toggle_mode_fields(dialog) {
	const mode = dialog.get_value("send_mode") || "Template";
	const template_field = dialog.get_field("template");
	if (template_field) {
		template_field.df.reqd = mode === "Template" ? 1 : 0;
		template_field.refresh();
	}

	if (mode !== "Template") {
		dialog.set_value("template_body", "");
		dialog.set_value("template_raw", "");
	}
	toggle_attachment_field(dialog);
}

function toggle_attachment_field(dialog) {
	const attach_field = dialog.get_field("attach");

	if (!attach_field) {
		return;
	}

	// Just refresh to update visibility based on depends_on
	attach_field.refresh();
}

function send_whatsapp_message(frm, dialog, values) {
	if (!values || !values.mobile_no) {
		frappe.msgprint(__("Please enter a valid mobile number"));
		return;
	}

	const is_template = values.send_mode === "Template";
	const method = is_template
		? "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.send_template"
		: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.send_custom";

	const args = {
		to: values.mobile_no,
		reference_doctype: frm.doc.doctype,
		reference_name: frm.doc.name,
		whatsapp_account: values.whatsapp_account || ""
	};

	if (is_template) {
		if (!values.template) {
			frappe.msgprint(__("Please select a template"));
			return;
		}
		args.template = values.template;
		args.message = values.template_body || values.template_raw || "";
		args.attach = values.attach || "";
		args.attach_document_print = values.attach_document_print ? 1 : 0;
		args.print_format = values.print_format || "";
		args.no_letterhead = values.no_letterhead ? 1 : 0;
	} else {
		args.message = values.custom_message || "";
		args.attach = values.attach || "";
		args.content_type = values.content_type || "text";
		args.attach_document_print = values.attach_document_print ? 1 : 0;
		args.print_format = values.print_format || "";
		args.no_letterhead = values.no_letterhead ? 1 : 0;
	}

	frappe.call({
		method,
		args,
		freeze: true,
		callback: (r) => {
			const queued = r && r.message && r.message.queued;
			frappe.msgprint(
				queued
					? __("WhatsApp message queued for {0}", [values.mobile_no])
					: __("Successfully sent to {0}", [values.mobile_no])
			);
			dialog.hide();
		},
		error: (r) => {
			let server_message = __("Send failed. Please check WhatsApp Settings and try again.");
			if (r && r._server_messages) {
				try {
					const arr = JSON.parse(r._server_messages || "[]");
					if (Array.isArray(arr) && arr.length) {
						server_message = arr[0];
					}
				} catch (e) {
					server_message = r.message || server_message;
				}
			} else if (r && r.message) {
				server_message = r.message;
			}
			try {
				const parsed = typeof server_message === "string" ? JSON.parse(server_message) : server_message;
				frappe.msgprint(parsed.message || __("Send failed."));
			} catch (e) {
				frappe.msgprint(typeof server_message === "string" ? server_message : __("Send failed."));
			}
		}
	});
}


function build_document_print_url(frm, values) {
	if (!values.attach_document_print) {
		return "";
	}
	const format = encodeURIComponent(values.print_format || "Standard");
	const no_letterhead = values.no_letterhead ? 1 : 0;
	return `${window.location.origin}/api/method/frappe.utils.print_format.download_pdf?doctype=${encodeURIComponent(frm.doc.doctype)}&name=${encodeURIComponent(frm.doc.name)}&format=${format}&no_letterhead=${no_letterhead}`;
}

function populate_mobile_from_contact(dialog) {
	const contact_name = dialog.get_value("contact");
	if (!contact_name) {
		return;
	}
	frappe.call({
		method: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.get_contact_whatsapp_numbers",
		args: {
			contact_name: contact_name
		},
		callback(r) {
			const numbers = r.message || [];
			if (numbers.length) {
				dialog.set_value("mobile_no", numbers[0]);
			}
		}
	});
}

function autofill_mobile_from_doc(frm, dialog) {
	// For manual dialogue, we want to be helpful but strict.
	// We'll call the backend to get all "WhatsApp" authorized numbers for this document.
	frappe.call({
		method: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.get_authorized_whatsapp_numbers",
		args: {
			reference_doctype: frm.doc.doctype,
			reference_name: frm.doc.name
		},
		callback(r) {
			const numbers = r.message || [];
			if (numbers.length) {
				dialog.set_value("mobile_no", numbers[0]);
			} else {
				// Fallback to basic field pull
				const candidates = [
					frm.doc.mobile_no,
					frm.doc.mobile,
					frm.doc.phone,
					frm.doc.contact_mobile,
					frm.doc.whatsapp_no
				].filter(Boolean);

				if (candidates.length) {
					dialog.set_value("mobile_no", candidates[0]);
				}
			}
		}
	});
}

function load_template_preview(frm, dialog) {
	const template = dialog.get_value("template");
	if (!template) {
		dialog.set_value("template_body", "");
		dialog.set_value("template_raw", "");
		set_default_whatsapp_account(dialog);
		return;
	}

	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "WhatsApp Templates",
			filters: { name: template },
			fieldname: ["whatsapp_account"]
		},
		callback(res) {
			const msg = res.message || {};
			if (msg.whatsapp_account) {
				dialog.set_value("whatsapp_account", msg.whatsapp_account);
			} else {
				set_default_whatsapp_account(dialog);
			}
		}
	});

	frappe.call({
		method: "whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message.get_template_preview",
		args: {
			template,
			reference_doctype: frm.doc.doctype,
			reference_name: frm.doc.name
		},
		callback(r) {
			const preview = r.message || {};
			const raw = preview.template_text || "";
			dialog.set_value("template_raw", raw);
			dialog.set_value("template_body", preview.rendered_text || raw);
		},
		error() {
			frappe.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "WhatsApp Templates",
					filters: { name: template },
					fieldname: ["template", "template_message"]
				},
				callback(res) {
					const msg = res.message || {};
					const raw = msg.template_message || msg.template || "";
					dialog.set_value("template_raw", raw);
					dialog.set_value("template_body", raw);
				}
			});
		}
	});
}
