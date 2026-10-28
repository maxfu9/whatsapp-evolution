"""Test compatibility and isolated dummy Evolution account fixtures."""

import frappe

try:
    from frappe.tests import IntegrationTestCase
except ImportError:
    from frappe.tests.utils import FrappeTestCase as IntegrationTestCase


def configure_evolution_test_account(name):
    """Configure only a test account; sends are mocked and no real keys are used."""
    from frappe.utils.password import set_encrypted_password
    frappe.db.set_value("WhatsApp Account", name, {
        "evolution_api_base": "http://127.0.0.1:9",
        "evolution_instance": "ci-test-instance",
        "evolution_api_version": "v2",
    })
    set_encrypted_password("WhatsApp Account", name, "ci-test-token", "evolution_api_token")
