import json

path = "/Users/kashif/erpbench/..."
# write actual path
path = "/Users/kashif/erpnext15-bench/apps/whatsapp_evolution/whatsapp_evolution/whatsapp_evolution/doctype/whatsapp_notification/whatsapp_notification.json"

with open(path, "r") as f:
    data = json.load(f)

# Insert fields
new_fields = [
    {
        "default": "0",
        "fieldname": "send_to_all_assignees",
        "fieldtype": "Check",
        "label": "Send To All Assignees"
    },
    {
        "fieldname": "recipients",
        "fieldtype": "Table",
        "label": "Recipients",
        "options": "Notification Recipient"
    }
]

# Find index of condition
idx = next(i for i, f in enumerate(data["fields"]) if f["fieldname"] == "condition")
data["fields"] = data["fields"][:idx] + new_fields + data["fields"][idx:]

# Find condition in field_order
if "field_order" in data:
    try:
        f_idx = data["field_order"].index("condition")
        data["field_order"].insert(f_idx, "send_to_all_assignees")
        data["field_order"].insert(f_idx + 1, "recipients")
    except ValueError:
        pass

with open(path, "w") as f:
    json.dump(data, f, indent=1)

