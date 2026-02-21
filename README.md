# WhatsApp Evolution

WhatsApp integration for Frappe/ERPNext using Evolution API.

Publisher: Europlast  
Contact: hello@europlast.pk

## What This App Supports

- Evolution API based sending (text and media)
- Multiple WhatsApp accounts with default incoming/outgoing selection
- Template-based and manual messaging
- "Send To WhatsApp" action from document forms
- Background sending for notifications and queued sends
- WhatsApp Notifications on DocType events and scheduler events
- Bulk messaging with one-by-one delay
- Attachment sending (uploaded file or document print PDF)
- Contact-driven recipient resolution (linked Contact / party)
- Inbound webhook processing and message logging
- Delivery and error logging in WhatsApp Notification Log / WhatsApp Message

## Important Scope

- This app is configured for Evolution API workflows.
- Legacy Meta-specific sync/flow paths are removed from active usage.

## Requirements

- Frappe/ERPNext v15 bench
- A running Evolution API server
- A connected Evolution instance (QR paired)
- wkhtmltopdf if you want reliable PDF print generation

## Installation

### 1) Get app (recommended)

Use an explicit app name (underscore):

```bash
bench get-app whatsapp_evolution https://github.com/maxfu9/whatsapp-evolution.git
```

If your Bench version does not support explicit app name for `get-app`, use manual clone:

```bash
cd ~/frappe-bench
git clone https://github.com/maxfu9/whatsapp-evolution apps/whatsapp_evolution
./env/bin/pip install -e apps/whatsapp_evolution
```

### 2) Install app on site

```bash
bench --site <your-site> install-app whatsapp_evolution
```

### 3) Migrate and rebuild

```bash
bench --site <your-site> migrate
bench build
bench --site <your-site> clear-cache
bench restart
```

## Updating App

For benches affected by hyphen/underscore resolver issues, use this stable update flow:

```bash
cd ~/frappe-bench/apps/whatsapp_evolution
git pull upstream master

cd ~/frappe-bench
./env/bin/pip install -e apps/whatsapp_evolution
bench --site <your-site> migrate
bench build
bench --site <your-site> clear-cache
bench restart
```

## Initial Setup

### 1) Configure global defaults in WhatsApp Settings

Open `WhatsApp Settings` and set:

- `Evolution API Base`
- `Evolution API Token`
- Optional: `Evolution Send Endpoint`
- `Attachment Delivery Mode`
  - `File Only`
  - `Fallback to Link`

### 2) Create WhatsApp Account(s)

Open `WhatsApp Account` and configure per account:

- `Account Name`
- `Status = Active`
- `Evolution Instance` (example: `erpnext`)
- Optional per-account overrides:
  - `Evolution API Base`
  - `Evolution API Token`
  - `Evolution Send Endpoint`
- Set one account as `Default Outgoing`
- Set one account as `Default Incoming` (if needed)

Use **Test Connection** button on both `WhatsApp Settings` and `WhatsApp Account`.

## Templates

Create template text in `WhatsApp Templates` and map variables in notifications/bulk sends.

Example placeholders:

- `{{1}}`, `{{2}}`, `{{3}}` ...

The app can render template text using document data before sending.

## Send From Document

Any submitted/saved document form has **Send To WhatsApp** menu action.

Modes:

- `Template`
- `Custom`

Options:

- Pick linked Contact (auto-load mobile)
- Attach uploaded file
- Attach document print PDF
- Choose print format / no letterhead
- Add timeline comment

## WhatsApp Notification

Use `WhatsApp Notification` to auto-send messages on:

- DocType events (`After Submit`, `After Save`, etc.)
- Scheduler events (`Hourly`, `Daily`, etc.)
- Day-based events (`Days Before`, `Days After`)

Key options:

- `Delay (Seconds)` for background delay
- Auto recipient resolution from contact/party if `Field Name` is blank
- `Attach Document Print`
- `Custom attachment`
- Set a field value after successful send

## Bulk WhatsApp Message

Use `Bulk WhatsApp Message` for campaigns and operational blasts.

Features:

- Recipient Type: `Individual` or `Recipient List`
- Template or manual content
- Variable mode: `Common` / `Unique`
- `Delay Between Messages (Seconds)` to send one-by-one
- Scheduled time support
- Status tracking (`Queued`, `In Progress`, `Completed`, `Partially Failed`)

## Webhook

Endpoint:

`/api/method/whatsapp_evolution.utils.webhook.webhook`

Use this for inbound processing and status updates routed through configured account logic.

## ERPNext Balance Fields Added

For WhatsApp template usage, this app can persist balance fields:

- Sales Invoice:
  - `wa_balance_before_invoice`
  - `wa_balance_after_invoice`
- Payment Entry:
  - `wa_balance_before_payment`
  - `wa_balance_after_payment`

## Troubleshooting

### Messages send but no RQ jobs visible

Fast jobs can finish before list refresh. Verify using:

- `WhatsApp Notification Log`
- `WhatsApp Message` status/history

### Attachment send errors

Check:

- Evolution instance is connected
- URL/base/token/instance are correct
- Site URL is reachable from Evolution service
- Attachment mode in `WhatsApp Settings`

### Build issues

If assets are stale:

```bash
bench build
bench --site <your-site> clear-cache
bench restart
```

### `No module named 'whatsapp-evolution'` or `.../apps/whatsapp-evolution/whatsapp-evolution/__init__.py`

Your Bench app registry likely saved the hyphen key. Normalize to underscore:

```bash
cd ~/frappe-bench
sed -i '/^whatsapp-evolution$/d' apps.txt sites/apps.txt
grep -qx 'whatsapp_evolution' apps.txt || echo 'whatsapp_evolution' >> apps.txt
grep -qx 'whatsapp_evolution' sites/apps.txt || echo 'whatsapp_evolution' >> sites/apps.txt
awk 'NF && !seen[$0]++' apps.txt > /tmp/apps_clean && mv /tmp/apps_clean apps.txt
awk 'NF && !seen[$0]++' sites/apps.txt > /tmp/sites_apps_clean && mv /tmp/sites_apps_clean sites/apps.txt
./env/bin/pip install -e apps/whatsapp_evolution
```

### `No module named 'lark_integrationwhatsapp_evolution'`

This means two app names were merged into one line in app lists. Recreate clean files:

```bash
cd ~/frappe-bench
cat > apps.txt <<'EOF'
frappe
eurotheme
frappe_desk_theme
pdf_on_submit
erpnext
material_theme
europlast_whatsapp
go1_webshop
print_designer
crm
hrms
whatsapp_evolution
customer_statements
builder
payments
webshop
lark_integration
EOF
cp apps.txt sites/apps.txt
```

## License

MIT
