"""Webhook."""
import frappe
import json
import requests
from werkzeug.wrappers import Response

from whatsapp_evolution.utils import get_whatsapp_account

REQUEST_TIMEOUT = 15


def _get_primary_change(data):
	entry = (data.get("entry") or [{}])[0]
	changes = entry.get("changes") or [{}]
	return changes[0]


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Get."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")
	account = frappe.db.get_value(
		"WhatsApp Account",
		{"webhook_verify_token": verify_token},
		["name", "webhook_verify_token"],
		as_dict=True,
	)
	if not account and verify_token and frappe.db.exists("WhatsApp Account", verify_token):
		# Backward compatibility: allow account name to be used as verify token.
		account = frappe._dict(name=verify_token, webhook_verify_token=verify_token)

	if not account:
		frappe.throw("No matching WhatsApp account")

	if frappe.form_dict.get("hub.verify_token") != account.webhook_verify_token:
		frappe.throw("Verify token does not match")

	return Response(hub_challenge, status=200)

def post():
	"""Post."""
	data = frappe.local.form_dict
	frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"template": "Webhook",
		"meta_data": json.dumps(data)
	}).insert(ignore_permissions=True)

	change = _get_primary_change(data)
	value = change.get("value", {})
	messages = value.get("messages", [])
	phone_id = value.get("metadata", {}).get("phone_number_id")
	sender_profile_name = next(
		(
			contact.get("profile", {}).get("name")
			for entry in data.get("entry", [])
			for change in entry.get("changes", [])
			for contact in change.get("value", {}).get("contacts", [])
		),
		None,
	)

	whatsapp_account = get_whatsapp_account(phone_id) if phone_id else None
	if not whatsapp_account:
		return

	if messages:
		for message in messages:
			message_type = message.get("type")
			if not message_type:
				continue
			is_reply = True if message.get('context') and 'forwarded' not in message.get('context') else False
			reply_to_message_id = message['context']['id'] if is_reply else None
			if message_type == 'text':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['text']['body'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type":message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'reaction':
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['reaction']['emoji'],
					"reply_to_message_id": message['reaction']['message_id'],
					"message_id": message['id'],
					"content_type": "reaction",
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			elif message_type == 'interactive':
				interactive_data = message['interactive']
				interactive_type = interactive_data.get('type')

				# Handle button reply
				if interactive_type == 'button_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['button_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
				# Handle list reply
				elif interactive_type == 'list_reply':
					frappe.get_doc({
						"doctype": "WhatsApp Message",
						"type": "Incoming",
						"from": message['from'],
						"message": interactive_data['list_reply']['id'],
						"message_id": message['id'],
						"reply_to_message_id": reply_to_message_id,
						"is_reply": is_reply,
						"content_type": "button",
						"profile_name": sender_profile_name,
						"whatsapp_account": whatsapp_account.name
					}).insert(ignore_permissions=True)
			elif message_type in ["image", "audio", "video", "document"]:
				token = whatsapp_account.get_password("token")
				url = f"{whatsapp_account.url}/{whatsapp_account.version}/"

				media_id = message[message_type]["id"]
				headers = {
					'Authorization': 'Bearer ' + token

				}
				response = requests.get(f'{url}{media_id}/', headers=headers, timeout=REQUEST_TIMEOUT)

				if response.status_code == 200:
					media_data = response.json()
					media_url = media_data.get("url")
					mime_type = media_data.get("mime_type")
					file_extension = (mime_type or "application/octet-stream").split('/')[-1]

					if not media_url:
						continue

					media_response = requests.get(media_url, headers=headers, timeout=REQUEST_TIMEOUT)
					if media_response.status_code == 200:
						file_data = media_response.content
						file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"

						message_doc = frappe.get_doc({
							"doctype": "WhatsApp Message",
							"type": "Incoming",
							"from": message['from'],
							"message_id": message['id'],
							"reply_to_message_id": reply_to_message_id,
							"is_reply": is_reply,
							"message": message[message_type].get("caption", ""),
							"content_type" : message_type,
							"profile_name":sender_profile_name,
							"whatsapp_account":whatsapp_account.name
						}).insert(ignore_permissions=True)

						file = frappe.get_doc(
							{
								"doctype": "File",
								"file_name": file_name,
								"attached_to_doctype": "WhatsApp Message",
								"attached_to_name": message_doc.name,
								"content": file_data,
								"attached_to_field": "attach"
							}
						).save(ignore_permissions=True)

						message_doc.attach = file.file_url
						message_doc.save()
			elif message_type == "button":
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message": message['button']['text'],
					"message_id": message['id'],
					"reply_to_message_id": reply_to_message_id,
					"is_reply": is_reply,
					"content_type": message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)
			else:
				frappe.get_doc({
					"doctype": "WhatsApp Message",
					"type": "Incoming",
					"from": message['from'],
					"message_id": message['id'],
					"message": (message.get(message_type) or {}).get(message_type, ""),
					"content_type" : message_type,
					"profile_name":sender_profile_name,
					"whatsapp_account":whatsapp_account.name
				}).insert(ignore_permissions=True)

	else:
		update_status(change)
	return

def update_status(data):
	"""Update status hook."""
	if data.get("field") == "message_template_status_update":
		update_template_status(data['value'])

	elif data.get("field") == "messages":
		update_message_status(data['value'])

def update_template_status(data):
	"""Update template status."""
	frappe.db.sql(
		"""UPDATE `tabWhatsApp Templates`
		SET status = %(event)s
		WHERE id = %(message_template_id)s""",
		data
	)

def update_message_status(data):
	"""Update message status."""
	statuses = data.get("statuses") or []
	if not statuses:
		return
	first_status = statuses[0]
	message_id = first_status.get("id")
	status = first_status.get("status")
	if not message_id or not status:
		return
	conversation = first_status.get("conversation", {}).get("id")
	name = frappe.db.get_value("WhatsApp Message", filters={"message_id": message_id})
	if not name:
		return

	doc = frappe.get_doc("WhatsApp Message", name)
	doc.status = status
	if conversation:
		doc.conversation_id = conversation
	doc.save(ignore_permissions=True)
