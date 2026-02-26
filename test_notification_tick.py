import frappe
from whatsapp_evolution.whatsapp_evolution.doctype.whatsapp_notification.whatsapp_notification import _get_contact_numbers

def test_notification_tick():
    # Create a test contact
    contact = frappe.get_doc({
        "doctype": "Contact",
        "first_name": "Test Notification Contact",
        "phone_nos": [
            {
                "phone": "1234567890",
                "is_notification_number": 1
            },
            {
                "phone": "0987654321",
                "is_whatsapp_number": 1
            },
            {
                "phone": "1112223333",
                "is_whatsapp_number": 0,
                "is_notification_number": 0
            }
        ]
    }).insert()

    try:
        numbers = _get_contact_numbers(contact.name)
        print(f"Fetched Numbers: {numbers}")
        
        expected = ["1234567890", "0987654321"]
        for num in expected:
            if num not in numbers:
                print(f"FAILED: Expected {num} in {numbers}")
                return
        if "1112223333" in numbers:
            print(f"FAILED: Did not expect 1112223333 in {numbers}")
            return
            
        print("SUCCESS: Notification and WhatsApp ticks are correctly respected!")
        
    finally:
        contact.delete()

if __name__ == "__main__":
    test_notification_tick()
