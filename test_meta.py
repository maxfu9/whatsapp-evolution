import frappe

def test():
    frappe.init(site="site1.local")
    frappe.connect()
    
    from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import _get_whatsapp_tick_fields
    print("Tick fields:", _get_whatsapp_tick_fields())

test()
