"""Manual diagnostic for a developer's local test site."""

import frappe
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import _get_tick_fields

def test():
    frappe.init(site="site1.local")
    frappe.connect()
    
    print("Tick fields:", _get_tick_fields(purpose="whatsapp"))

if __name__ == "__main__":
    test()
