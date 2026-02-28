import frappe
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import WhatsAppNotification

def test_strict_filtering():
    # 1. Setup sensitive entity (Customer)
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": "Strict Test Customer",
        "mobile_no": "3333333333" # Raw number that SHOULD BE IGNORED
    }).insert(ignore_permissions=True)

    # 2. Setup Contacts
    contact_a = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "Authorized Contact",
        "phone_nos": [{"phone": "1111111111", "is_whatsapp_number": 1}]
    }).insert(ignore_permissions=True)
    
    contact_b = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "Unauthorized Contact",
        "phone_nos": [{"phone": "2222222222", "is_whatsapp_number": 0}]
    }).insert(ignore_permissions=True)

    # Link contacts to customer
    for c in [contact_a, contact_b]:
        c.append("links", {"link_doctype": "Customer", "link_name": customer.name})
        c.save()

    try:
        # 3. Test resolution
        notif = frappe.new_doc("WhatsApp Notification")
        numbers = notif.get_recipient_numbers(customer, customer.as_dict())
        
        print(f"Customer Mobile Field: {customer.mobile_no}")
        print(f"Resolved Numbers: {numbers}")
        
        if "1111111111" not in numbers:
            print("FAILED: Expected authorized number 1111111111 was missing.")
        if "2222222222" in numbers:
            print("FAILED: Unauthorized number 2222222222 was included.")
        if "3333333333" in numbers:
            print("FAILED: Raw fallback number 3333333333 was included despite sensitivity.")
            
        if numbers == ["1111111111"]:
            print("SUCCESS: Strict filtering is working perfectly for sensitive entities!")
        else:
            print(f"FAILED: Result {numbers} did not match expected ['1111111111']")

    finally:
        # Cleanup
        contact_a.delete()
        contact_b.delete()
        customer.delete()
        frappe.db.commit()

if __name__ == "__main__":
    test_strict_filtering()
