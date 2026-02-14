import frappe
import requests
from .base import BaseProvider


class EvolutionProvider(BaseProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.api_base = settings.get("evolution_api_base")
        self.token = settings.get("evolution_api_token")

    def send_message(self, to_number, message, **kwargs):
        url = f"{self.api_base}/messages"
        payload = {"to": to_number, "text": message}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def parse_incoming(self, data):
        return {
            "from": data["from"],
            "body": data["text"],
            "message_id": data["id"],
            "timestamp": data["timestamp"],
        }


@frappe.whitelist(allow_guest=True)
def handle_webhook():
    data = frappe.local.request.get_json()
    provider = EvolutionProvider(frappe.get_single("WhatsApp Settings").as_dict())
    msg = provider.parse_incoming(data)
    from frappe_whatsapp.incoming import handle_incoming_message

    handle_incoming_message(msg)
    return "OK"
