import frappe
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import WhatsAppNotification
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_message.whatsapp_message import get_authorized_whatsapp_numbers

def test_split_consents():
    # Setup Customer
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": "Split Consent Test",
    }).insert(ignore_permissions=True)

    # Authorized for Notifications ONLY
    contact_notif = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "Notification Only",
        "links": [{"link_doctype": "Customer", "link_name": customer.name}],
        "phone_nos": [{"phone": "1111111111", "is_notification_number": 1, "is_whatsapp_number": 0}]
    }).insert(ignore_permissions=True)
    
    # Authorized for WhatsApp ONLY
    contact_wa = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "WhatsApp Only",
        "links": [{"link_doctype": "Customer", "link_name": customer.name}],
        "phone_nos": [{"phone": "2222222222", "is_notification_number": 0, "is_whatsapp_number": 1}]
    }).insert(ignore_permissions=True)

    try:
        # 1. Test Automated Notification Resolution (Purpose: Notification)
        notif = frappe.new_doc("WhatsApp Notification")
        notif_numbers = notif.get_recipient_numbers(customer, customer.as_dict())
        print(f"Automated Notification Resolved: {notif_numbers}")
        
        # 2. Test Manual Dialogue Resolution (Purpose: WhatsApp)
        wa_numbers = get_authorized_whatsapp_numbers("Customer", customer.name)
        print(f"Manual Dialogue Resolved: {wa_numbers}")

        # Verification
        success = True
        if "1111111111" not in notif_numbers:
            print("FAILED: Notification Only contact (1111111111) missing from automated alerts.")
            success = False
        if "2222222222" in notif_numbers:
            print("FAILED: WhatsApp Only contact (2222222222) incorrectly included in automated alerts.")
            success = False
            
        if "2222222222" not in wa_numbers:
            print("FAILED: WhatsApp Only contact (2222222222) missing from manual dialogue.")
            success = False
        if "1111111111" in wa_numbers:
            print("FAILED: Notification Only contact (1111111111) incorrectly included in manual dialogue.")
            success = False

        if success:
            print("SUCCESS: Distinction between WhatsApp and Notification consents is working perfectly!")

    finally:
        # Cleanup
        contact_notif.delete()
        contact_wa.delete()
        customer.delete()
        frappe.db.commit()

if __name__ == "__main__":
    test_split_consents()
