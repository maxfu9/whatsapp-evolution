import frappe

def test():
    doc_data = {
        "doctype": "ToDo",
        "name": "Test-ToDo",
        "owner": "Administrator"
    }

    mock = frappe.new_doc("WhatsApp Notification")
    mock.send_to_all_assignees = 1
    mock.append("recipients", {"receiver_by_document_field": "owner"})

    nums = mock._get_system_user_numbers(doc_data, doc_data)
    print("Fetched Numbers:", nums)

if __name__ == "__main__":
    test()
