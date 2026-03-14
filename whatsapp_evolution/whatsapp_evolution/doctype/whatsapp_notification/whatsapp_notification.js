// Copyright (c) 2022, Shridhar Patil and contributors
// For license information, please see license.txt
frappe.notification = {
	setup_fieldname_select: function (frm) {
		// get the doctype to update fields
		if (!frm.doc.reference_doctype) {
			return;
		}

		frappe.model.with_doctype(frm.doc.reference_doctype, function () {
			let get_select_options = function (df, parent_field) {
				// Append parent_field name along with fieldname for child table fields
				let select_value = parent_field ? df.fieldname + "," + parent_field : df.fieldname;
				let path = parent_field ? parent_field + " > " + df.fieldname : df.fieldname;

				return {
					value: select_value,
					label: path + " (" + __(df.label, null, df.parent) + ")",
				};
			};

			let get_date_change_options = function () {
				let date_options = $.map(fields, function (d) {
					return d.fieldtype == "Date" || d.fieldtype == "Datetime"
						? get_select_options(d)
						: null;
				});
				// append creation and modified date to Date Change field
				return date_options.concat([
					{ value: "creation", label: `creation (${__("Created On")})` },
					{ value: "modified", label: `modified (${__("Last Modified Date")})` },
				]);
			};

			let fields = frappe.get_doc("DocType", frm.doc.reference_doctype).fields;
			let options = $.map(fields, function (d) {
				return frappe.model.no_value_type.includes(d.fieldtype)
					? null
					: get_select_options(d);
			});

			// set date changed options
			frm.set_df_property("date_changed", "options", get_date_change_options());

			// set value changed options
			frm.set_df_property("value_changed", "options", [""].concat(options));
			frm.set_df_property("set_property_after_alert", "options", [""].concat(options));

			// filter receiver fields to only show User Links or Phone Options
			let receiver_fields = $.map(fields, function (d) {
				if (d.fieldtype == "Table") {
					let child_options = frappe.get_doc("DocType", d.options).fields;
					return $.map(child_options, function (df) {
						return (df.options == "User" && df.fieldtype == "Link") || df.options == "Phone" || df.options == "Email"
							? get_select_options(df, d.fieldname)
							: null;
					});
				} else {
					return (d.options == "User" && d.fieldtype == "Link") || d.options == "Phone" || d.options == "Email"
						? get_select_options(d)
						: null;
				}
			});

			// set receiver by document field options
			frappe.meta.get_docfield(
				"Notification Recipient",
				"receiver_by_document_field",
				frm.doc.name
			).options = [""].concat(["owner"]).concat(receiver_fields);
		});
	},
	setup_alerts_button: function (frm) {
		// body...
		frm.add_custom_button(__('Get Alerts for Today'), function () {
			frappe.call({
				method: 'whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification.call_trigger_notifications',
				args: {
					method: 'daily'
				},
				callback: function (response) {
					if (response.message && response.message.length > 0) {
					} else {
						if (window.whatsapp_evolution_ui && window.whatsapp_evolution_ui.msgprint) {
							window.whatsapp_evolution_ui.msgprint(
								__('No alerts for today.'),
								'info',
								{ title: __('Notifications') }
							);
						} else {
							frappe.msgprint({
								title: __('Notifications'),
								indicator: 'blue',
								message: __('No alerts for today.')
							});
						}
					}
				},
				error: function (error) {
					if (window.whatsapp_evolution_ui && window.whatsapp_evolution_ui.msgprint) {
						window.whatsapp_evolution_ui.msgprint(
							__('Failed to trigger notifications.'),
							'error',
							{ title: __('Notifications') }
						);
					} else {
						frappe.msgprint({
							title: __('Notifications'),
							indicator: 'red',
							message: __('Failed to trigger notifications.')
						});
					}
				}
			});
		});
	}
};


frappe.ui.form.on('WhatsApp Notification', {
	refresh: function (frm) {
		frm.events.load_template(frm, true);
		frappe.notification.setup_fieldname_select(frm);
		frappe.notification.setup_alerts_button(frm);

		frm.set_query("print_format", function () {
			return {
				filters: {
					doc_type: frm.doc.reference_doctype
				}
			};
		});
	},
	template: function (frm) {
		frm.events.load_template(frm, false);
	},
	load_template: function (frm, silent_refresh = false) {
		const set_field = (fieldname, value) => {
			if (frm.doc[fieldname] === value) {
				return;
			}
			if (silent_refresh) {
				frm.doc[fieldname] = value;
				frm.refresh_field(fieldname);
				return;
			}
			frm.set_value(fieldname, value);
		};

		frappe.db.get_value(
			"WhatsApp Templates",
			frm.doc.template,
			["template", "header_type"],
			(r) => {
				if (r && r.template) {
					set_field("header_type", r.header_type || "");
					if (['DOCUMENT', "IMAGE"].includes(r.header_type)) {
						frm.toggle_display("custom_attachment", true);
						frm.toggle_display("attach_document_print", true);
						if (!silent_refresh && !frm.doc.custom_attachment) {
							set_field("attach_document_print", 1);
						}
						if (!silent_refresh) {
							frm.trigger("attach_document_print");
						}
					} else {
						frm.toggle_display("custom_attachment", false);
						frm.toggle_display("attach_document_print", false);
						if (!silent_refresh) {
							set_field("attach_document_print", 0);
							set_field("custom_attachment", 0);
						}
					}

					frm.refresh_field("custom_attachment")

					set_field("code", r.template || "");
				}
			}
		)
	},
	custom_attachment: function (frm) {
		if (frm.doc.custom_attachment == 1 && ['DOCUMENT', "IMAGE"].includes(frm.doc.header_type)) {
			frm.set_df_property('file_name', 'reqd', frm.doc.custom_attachment)
		} else {
			frm.set_df_property('file_name', 'reqd', 0)
		}

		// frm.toggle_display("attach_document_print", !frm.doc.custom_attachment);
		if (frm.doc.header_type) {
			frm.set_value("attach_document_print", !frm.doc.custom_attachment)
		}
	},
	attach_document_print: function (frm) {
		// frm.toggle_display("custom_attachment", !frm.doc.attach_document_print);
		if (['DOCUMENT', "IMAGE"].includes(frm.doc.header_type)) {
			frm.set_value("custom_attachment", !frm.doc.attach_document_print)
		}
		if (frm.doc.attach_document_print && !frm.doc.print_format && frm.doc.reference_doctype) {
			frappe.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "DocType",
					filters: { name: frm.doc.reference_doctype },
					fieldname: "default_print_format"
				},
				callback: function (r) {
					if (r.message && r.message.default_print_format) {
						frm.set_value("print_format", r.message.default_print_format);
					}
				}
			});
		}
	},
	reference_doctype: function (frm) {
		frappe.notification.setup_fieldname_select(frm);
		frm.set_query("print_format", function () {
			return {
				filters: {
					doc_type: frm.doc.reference_doctype
				}
			};
		});
	},
});
