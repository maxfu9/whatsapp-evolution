# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from whatsapp_evolution.whatsapp_evolution.providers.evolution import EvolutionProvider
from whatsapp_evolution.utils import get_evolution_settings

class WhatsAppSettings(Document):
	pass


@frappe.whitelist()
def test_evolution_connection(account=None):
	"""Test Evolution connection for a specific account or all active accounts."""
	results = []
	accounts = []

	if account and frappe.db.exists("WhatsApp Account", account):
		accounts = [frappe.get_doc("WhatsApp Account", account)]
	else:
		for row in frappe.get_all(
			"WhatsApp Account",
			filters={"status": "Active"},
			fields=["name"],
			order_by="is_default desc, modified desc",
		):
			accounts.append(frappe.get_doc("WhatsApp Account", row.name))

	if not accounts:
		settings = get_evolution_settings()
		provider = EvolutionProvider(settings)
		res = provider.test_connection()
		res["account"] = "(Global fallback)"
		return {"results": [res]}

	for acc in accounts:
		settings = get_evolution_settings(acc.name)
		provider = EvolutionProvider(settings)
		res = provider.test_connection()
		res["account"] = acc.name
		res["instance"] = settings.get("evolution_instance")
		results.append(res)

	return {"results": results}
