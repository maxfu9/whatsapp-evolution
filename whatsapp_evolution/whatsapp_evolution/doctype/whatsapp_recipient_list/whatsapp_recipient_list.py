import frappe
import json
from frappe import _
from frappe.model.document import Document
from whatsapp_evolution.utils import format_number


class WhatsAppRecipientList(Document):
	DEFAULT_PHONE_FIELDS = (
		"contact_mobile",
		"mobile_no",
		"mobile",
		"phone",
		"contact_phone",
		"whatsapp_no",
		"whatsapp_number",
	)
	PARTY_LINK_OPTIONS = {"Customer", "Supplier", "Lead", "Prospect"}

	def validate(self):
		self._normalize_current_recipients()
		self._auto_import_contacts_on_save()
		self._normalize_current_recipients()
		self.validate_recipients()
	
	def validate_recipients(self):
		if not self.is_new():
			if not self.recipients:
				frappe.throw(_("At least one recipient is required"))

	def _normalize_mobile(self, value):
		mobile = (value or "")
		if not isinstance(mobile, str):
			mobile = str(mobile)
		mobile = "".join(char for char in mobile if char.isdigit() or char == "+")
		mobile = mobile.strip()
		return format_number(mobile)

	def _normalize_current_recipients(self):
		"""Normalize and deduplicate manually entered recipient numbers."""
		if not self.get("recipients"):
			return

		seen = set()
		cleaned_rows = []
		for row in self.get("recipients") or []:
			mobile = self._normalize_mobile(row.get("mobile_number"))
			if not mobile:
				continue
			key = self._normalize_key(mobile)
			if key in seen:
				continue
			seen.add(key)
			cleaned_rows.append(
				{
					"mobile_number": mobile,
					"recipient_name": row.get("recipient_name"),
					"recipient_data": row.get("recipient_data"),
				}
			)

		self.set("recipients", [])
		for row in cleaned_rows:
			self.append("recipients", row)

	def _split_mobile_candidates(self, value):
		if value is None:
			return []
		if not isinstance(value, str):
			value = str(value)
		raw = value.replace("\n", ",").replace(";", ",").replace("|", ",")
		out = []
		for part in raw.split(","):
			mobile = self._normalize_mobile(part)
			if mobile:
				out.append(mobile)
		return out

	def _dedupe_numbers(self, numbers):
		seen = set()
		out = []
		for number in numbers or []:
			key = (number or "").lstrip("+")
			if not key or key in seen:
				continue
			seen.add(key)
			out.append(number)
		return out

	def _normalize_key(self, number):
		return (number or "").lstrip("+")

	def _load_excluded_numbers(self):
		raw = self.get("excluded_numbers_json")
		if not raw:
			return set()
		try:
			items = json.loads(raw)
		except Exception:
			return set()
		if not isinstance(items, list):
			return set()
		return {self._normalize_key(self._normalize_mobile(v)) for v in items if self._normalize_mobile(v)}

	def _save_excluded_numbers(self, excluded):
		items = sorted([f"+{n}" if n and not n.startswith("+") else n for n in excluded if n])
		self.excluded_numbers_json = json.dumps(items)

	def _get_contact_numbers(self, contact_name):
		if not contact_name or not frappe.db.exists("Contact", contact_name):
			return []
		contact = frappe.get_doc("Contact", contact_name)
		phone_rows = contact.get("phone_nos") or []

		tick_field = None
		try:
			meta = frappe.get_meta("Contact Phone")
			for fieldname in ("is_whatsapp_number", "is_whatsapp", "whatsapp"):
				if meta.get_field(fieldname):
					tick_field = fieldname
					break
		except Exception:
			tick_field = None

		numbers = []
		if tick_field:
			for row in phone_rows:
				if frappe.utils.cint(row.get(tick_field)):
					numbers.extend(self._split_mobile_candidates(row.get("phone")))
			if numbers:
				return self._dedupe_numbers(numbers)

		for row in phone_rows:
			numbers.extend(self._split_mobile_candidates(row.get("phone")))

		if not numbers:
			numbers.extend(self._split_mobile_candidates(contact.get("mobile_no")))
			numbers.extend(self._split_mobile_candidates(contact.get("phone")))

		return self._dedupe_numbers(numbers)

	def _get_dynamic_link_contact_numbers(self, link_doctype, link_name):
		if not link_doctype or not link_name:
			return []
		contact_names = frappe.get_all(
			"Dynamic Link",
			filters={
				"link_doctype": link_doctype,
				"link_name": link_name,
				"parenttype": "Contact",
			},
			pluck="parent",
		)
		numbers = []
		for contact_name in contact_names:
			numbers.extend(self._get_contact_numbers(contact_name))
		return self._dedupe_numbers(numbers)

	def _guess_mobile_fields(self, doctype):
		meta = frappe.get_meta(doctype)
		valid_columns = set(meta.get_valid_columns() or [])
		fields = []

		for fieldname in self.DEFAULT_PHONE_FIELDS:
			if fieldname in valid_columns:
				fields.append(fieldname)

		for field in meta.fields:
			fname = field.fieldname
			if not fname or fname in fields:
				continue
			if field.fieldtype == "Phone":
				fields.append(fname)
				continue
			if field.fieldtype == "Data":
				lower_name = fname.lower()
				if any(k in lower_name for k in ("mobile", "phone", "whatsapp")):
					fields.append(fname)

		return fields

	def _build_import_fields(self, doctype, mobile_field, name_field=None, data_fields=None):
		meta = frappe.get_meta(doctype)
		valid_columns = set(meta.get_valid_columns() or [])
		fields = ["name"]

		if mobile_field and mobile_field in valid_columns:
			fields.append(mobile_field)
		else:
			fields.extend([f for f in self._guess_mobile_fields(doctype) if f in valid_columns])

		if name_field and name_field in valid_columns:
			fields.append(name_field)

		for default_name in ("customer_name", "supplier_name", "full_name", "title"):
			if default_name in valid_columns:
				fields.append(default_name)

		if data_fields:
			for fieldname in data_fields:
				if fieldname in valid_columns:
					fields.append(fieldname)

		for field in meta.fields:
			if field.fieldtype != "Link":
				continue
			if field.options == "Contact" or field.options in self.PARTY_LINK_OPTIONS:
				if field.fieldname in valid_columns:
					fields.append(field.fieldname)

		return list(dict.fromkeys(fields))

	def _resolve_record_numbers(self, doctype, record, mobile_field=None):
		numbers = []
		if doctype == "Contact" and record.get("name"):
			numbers.extend(self._get_contact_numbers(record.get("name")))

		if mobile_field:
			numbers.extend(self._split_mobile_candidates(record.get(mobile_field)))
		else:
			for fieldname in self._guess_mobile_fields(doctype):
				numbers.extend(self._split_mobile_candidates(record.get(fieldname)))

		if numbers:
			return self._dedupe_numbers(numbers)

		contact_fields = ["contact", "contact_person"]
		for field in contact_fields:
			if record.get(field):
				numbers.extend(self._get_contact_numbers(record.get(field)))

		meta = frappe.get_meta(doctype)
		for field in meta.fields:
			if field.fieldtype == "Link" and field.options in self.PARTY_LINK_OPTIONS:
				party_name = record.get(field.fieldname)
				if party_name:
					numbers.extend(self._get_dynamic_link_contact_numbers(field.options, party_name))

		record_name = record.get("name")
		if record_name:
			numbers.extend(self._get_dynamic_link_contact_numbers(doctype, record_name))

		return self._dedupe_numbers(numbers)

	def _auto_import_contacts_on_save(self):
		"""Auto-fill recipients from Contact on save when import mode is enabled."""
		if not frappe.utils.cint(self.import_from_doctype):
			return
		if self.doctype_to_import and self.doctype_to_import != "Contact":
			return
		self.doctype_to_import = "Contact"

		filters = None
		if self.import_filters:
			try:
				filters = json.loads(self.import_filters)
			except Exception:
				filters = None

		data_fields = None
		if self.data_fields:
			try:
				data_fields = json.loads(self.data_fields)
			except Exception:
				data_fields = None

		self.import_list_from_doctype(
			doctype="Contact",
			mobile_field=(self.mobile_field or None),
			name_field=(self.name_field or "full_name"),
			filters=filters,
			limit=self.import_limit or None,
			data_fields=data_fields,
		)

	def import_list_from_doctype(self, doctype, mobile_field=None, name_field=None, filters=None, limit=None, data_fields=None):
		"""Import recipients from another DocType"""
		self.doctype_to_import = doctype
		self.mobile_field = mobile_field or ""
		self.import_filters = json.dumps(filters) if isinstance(filters, (dict, list)) else (filters or "")
		if data_fields:
			self.data_fields = json.dumps(data_fields)

		if limit:
			self.import_limit = limit

		fields = self._build_import_fields(
			doctype=doctype,
			mobile_field=mobile_field,
			name_field=name_field,
			data_fields=data_fields,
		)
		# Get records from the doctype
		records = frappe.get_all(
			doctype,
			filters=filters,
			fields=fields,
			limit=limit
		)
		
		# Track manual deletions from existing list as persistent exclusions.
		excluded_numbers = self._load_excluded_numbers()
		previous_doc = None
		if not self.is_new():
			try:
				previous_doc = self.get_doc_before_save()
			except Exception:
				previous_doc = None
		if previous_doc:
			prev_numbers = {
				self._normalize_key(self._normalize_mobile(row.get("mobile_number")))
				for row in (previous_doc.get("recipients") or [])
				if self._normalize_mobile(row.get("mobile_number"))
			}
			curr_numbers = {
				self._normalize_key(self._normalize_mobile(row.get("mobile_number")))
				for row in (self.get("recipients") or [])
				if self._normalize_mobile(row.get("mobile_number"))
			}
			excluded_numbers |= (prev_numbers - curr_numbers)
			# If user manually added back a number, remove it from exclusions.
			excluded_numbers -= curr_numbers
		self._save_excluded_numbers(excluded_numbers)

		# Clear existing recipients
		self.recipients = []
		seen_numbers = set()
		
		# Add recipients
		for record in records:
			mobiles = self._resolve_record_numbers(doctype, record, mobile_field=mobile_field)
			if not mobiles:
				continue

			recipient_data = {}
			if data_fields:
				for field in data_fields:
					if record.get(field):
						# Use field name as the variable name in recipient data
						variable_name = field.lower().replace(" ", "_")
						recipient_data[variable_name] = record.get(field)

			recipient_name = ""
			if name_field and record.get(name_field):
				recipient_name = record.get(name_field)
			else:
				recipient_name = (
					record.get("customer_name")
					or record.get("supplier_name")
					or record.get("full_name")
					or record.get("title")
					or record.get("name")
				)

			for mobile in mobiles:
				key = mobile.lstrip("+")
				if key in excluded_numbers:
					continue
				if key in seen_numbers:
					continue
				seen_numbers.add(key)
				self.append(
					"recipients",
					{
						"mobile_number": mobile,
						"recipient_name": recipient_name,
						"recipient_data": json.dumps(recipient_data),
					},
				)
		
		return len(self.recipients)
