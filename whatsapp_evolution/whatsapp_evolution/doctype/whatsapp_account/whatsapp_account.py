# Copyright (c) 2025, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from whatsapp_evolution.utils import get_evolution_settings, format_number
from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider


class WhatsAppAccount(Document):
	def on_update(self):
		"""Check there is only one default of each type."""
		self.there_must_be_only_one_default()

	def there_must_be_only_one_default(self):
		"""If current WhatsApp Account is default, un-default all other accounts."""
		for field in ("is_default", "is_default_incoming", "is_default_outgoing"):
			if not frappe.get_meta("WhatsApp Account").has_field(field):
				continue
			if not self.get(field):
				continue

			for whatsapp_account in frappe.get_all("WhatsApp Account", filters={field: 1}):
				if whatsapp_account.name == self.name:
					continue

				whatsapp_account = frappe.get_doc("WhatsApp Account", whatsapp_account.name)
				whatsapp_account.set(field, 0)
				whatsapp_account.save()


@frappe.whitelist()
def check_recipient_number(account, number):
	"""Check whether a number exists on WhatsApp for the selected Evolution account."""
	if not account or not frappe.db.exists("WhatsApp Account", account):
		frappe.throw("Invalid WhatsApp Account")
	if not number:
		frappe.throw("Mobile number is required")

	settings = get_evolution_settings(account)
	provider = EvolutionProvider(settings)
	normalized = format_number(number)
	exists = provider.check_number_exists(normalized)

	return {
		"number": normalized,
		"exists": exists,
		"status": (
			"connected" if exists is True else
			"not_found" if exists is False else
			"unknown"
		),
		"message": (
			f"Number {normalized} exists on WhatsApp."
			if exists is True else
			f"Number {normalized} is not registered on WhatsApp."
			if exists is False else
			"Could not verify number from Evolution API. Check API version/endpoint permissions."
		),
	}
