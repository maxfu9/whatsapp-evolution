# Delivery and incoming-message reliability

## Webhook authentication

Evolution callbacks require `X-Webhook-Secret`, compared in constant time to
`whatsapp_evolution_webhook_secret` in the site's private `site_config.json`.
There is no unauthenticated fallback, including on sites with no configured secret.
Generate a random secret with at least 32 bytes and configure the same value in
Evolution's instance webhook `headers`. Keep existing URL, events and by-event
settings unchanged. Do not put the secret in a URL or commit it to Git.

Configure Evolution's header before activating this version, then migrate and
restart. Verify an unsigned callback returns HTTP 401 and a correctly signed
callback succeeds. Each additional instance delivering to this site needs the
same header. Changing the secret requires updating both ends.

## Receipt recovery

Authenticated receipts are stored in WhatsApp Delivery Receipt before attempting
to match the account and provider message ID. Unmatched receipts are replayed by
the hourly recovery job. Status advancement is protected by a row lock, and
late delivery events cannot downgrade Read. The form's Refresh Status action
queries the provider's stored receipts; it never sends or marks a message read.
The hourly job also checks up to 30 recent messages, oldest-check first, over
the last 30 days. API failures do not block other messages indefinitely.

Confirmed On timestamps indicate when the app obtained confirmation, not the
recipient's exact read time. Recovery cannot invent a receipt WhatsApp has not
provided, including where recipient privacy settings suppress read receipts.

## Sends and retries

New outgoing Message inserts create Queued records. Their send jobs are released
only after the database transaction commits. Workers claim each record once;
Started and Needs Review records are not automatically resent. Document-event
notifications are queued even when their configured delay is zero.

Bulk Retry Failed is permission checked and only requeues definite failures with
no provider ID. It does not retry Needs Review, Delivered or Read messages.
Provider acceptance is Success, not proof of delivery.

## Incoming messages

Incoming batches are processed entry by entry for known active accounts.
Party lookup checks the actual schema, including Employee.cell_number. Unknown
numbers are retained without creating customers or contacts. Media is saved as
private File attachments (20 MB maximum); failed media downloads retain the
message with an explicit warning. Unsupported active content is stored as a
binary download, never as an HTML/SVG document.
