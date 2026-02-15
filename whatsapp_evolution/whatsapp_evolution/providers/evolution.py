import frappe
import requests
import base64
import hashlib
from .base import BaseProvider


class EvolutionProvider(BaseProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.api_base = (settings.get("evolution_api_base") or "").rstrip("/")
        self.token = settings.get("evolution_api_token")
        self.instance = (settings.get("evolution_instance") or "").strip().strip("/")
        self.send_endpoint = (settings.get("evolution_send_endpoint") or "").strip()

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "apikey": self.token or "",
            "Content-Type": "application/json",
        }

    def _dedup_key(self, kind, to_number, content_hash):
        return f"wa_evo_out:{kind}:{to_number}:{content_hash}"

    def _acquire_dedup(self, kind, to_number, content, ttl=45):
        raw = (content or "").encode("utf-8", errors="ignore")
        content_hash = hashlib.sha1(raw).hexdigest()
        key = self._dedup_key(kind, to_number, content_hash)
        cache = frappe.cache()
        if cache.get_value(key):
            return False
        cache.set_value(key, 1, expires_in_sec=ttl)
        return True

    def _build_url(self, path_or_url):
        if not path_or_url:
            return ""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url.rstrip("/")
        return f"{self.api_base}/{path_or_url.lstrip('/')}".rstrip("/")

    def _text_candidate_urls(self):
        urls = []
        if self.send_endpoint:
            urls.append(self._build_url(self.send_endpoint))
            if self.instance and "{instance}" in self.send_endpoint:
                urls.append(self._build_url(self.send_endpoint.replace("{instance}", self.instance)))
        if self.instance:
            urls.extend(
                [
                    self._build_url(f"/message/sendText/{self.instance}"),
                    self._build_url(f"/messages/{self.instance}"),
                ]
            )
        urls.extend([self._build_url("/message/sendText"), self._build_url("/messages")])
        return [u for i, u in enumerate(urls) if u and u not in urls[:i]]

    def _media_candidate_urls(self):
        urls = []
        if self.instance:
            urls.extend(
                [
                    self._build_url(f"/message/sendMedia/{self.instance}"),
                    self._build_url(f"/messages/{self.instance}"),
                ]
            )
        urls.extend([self._build_url("/message/sendMedia"), self._build_url("/messages")])
        return [u for i, u in enumerate(urls) if u and u not in urls[:i]]

    def send_message(self, to_number, message, **kwargs):
        if not self._acquire_dedup("text", to_number, message or "", ttl=45):
            return {"id": "dedup-skip"}

        payload_variants = [
            {"number": to_number, "text": message},
            {"to": to_number, "text": message},
            {
                "number": to_number,
                "options": {"delay": 1200, "presence": "composing"},
                "textMessage": {"text": message},
            },
        ]

        errors = []
        for url in self._text_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=20)
                    response.raise_for_status()
                    return response.json()
                except requests.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else "?"
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} -> {status_code} {body}".strip())
                except Exception as e:
                    errors.append(f"{url} -> {str(e)}")

        raise frappe.ValidationError(f"Evolution text send failed. Tried: {', '.join(errors)}")

    def send_media(self, to_number, media_url, media_type="document", caption="", media_bytes=None, filename=None):
        if media_bytes:
            # Prefer content hash so signed URLs for same file don't bypass dedup.
            dedup_content = f"{media_type}|{caption or ''}|{hashlib.sha1(media_bytes).hexdigest()}"
        else:
            dedup_content = f"{media_type}|{caption or ''}|{media_url or ''}|{filename or ''}"
        if not self._acquire_dedup("media", to_number, dedup_content, ttl=60):
            return {"id": "dedup-skip"}

        media_type = (media_type or "document").lower()
        media_url = requests.utils.requote_uri(media_url or "")
        payload_variants = [
            {
                "number": to_number,
                "mediaMessage": {
                    "mediatype": media_type,
                    "media": media_url,
                    "caption": caption or "",
                },
            },
            {
                "number": to_number,
                "mediatype": media_type,
                "media": media_url,
                "caption": caption or "",
            },
            {
                "to": to_number,
                "mediatype": media_type,
                "media": media_url,
                "caption": caption or "",
            },
            {
                "number": to_number,
                "mediaMessage": {
                    "mediatype": media_type,
                    "media": media_url,
                    "caption": caption or "",
                },
            },
        ]

        if media_bytes:
            try:
                encoded = base64.b64encode(media_bytes).decode("ascii")
                media_name = filename or f"{media_type}.bin"
                payload_variants.insert(
                    0,
                    {
                        "number": to_number,
                        "mediaMessage": {
                            "mediatype": media_type,
                            "media": encoded,
                            "caption": caption or "",
                            "fileName": media_name,
                        },
                    },
                )
            except Exception:
                pass

        # Optional base64 fallback for Evolution setups that do not accept remote URLs.
        try:
            if media_url and not media_bytes:
                response = requests.get(media_url, timeout=20)
                response.raise_for_status()
                encoded = base64.b64encode(response.content).decode("ascii")
                payload_variants.append({
                    "number": to_number,
                    "mediatype": media_type,
                    "media": encoded,
                    "caption": caption or "",
                    "fileName": media_url.rstrip("/").split("/")[-1] or f"{media_type}.bin",
                })
                payload_variants.append({
                    "number": to_number,
                    "mediaMessage": {
                        "mediatype": media_type,
                        "media": encoded,
                        "caption": caption or "",
                        "fileName": media_url.rstrip("/").split("/")[-1] or f"{media_type}.bin",
                    },
                })
        except Exception:
            pass

        errors = []
        for url in self._media_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=25)
                    response.raise_for_status()
                    return response.json()
                except requests.HTTPError as e:
                    status_code = e.response.status_code if e.response is not None else "?"
                    mode = "base64" if payload.get("fileName") else "url"
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} ({mode}) -> {status_code} {body}".strip())
                except Exception as e:
                    mode = "base64" if payload.get("fileName") else "url"
                    errors.append(f"{url} ({mode}) -> {str(e)}")

        raise frappe.ValidationError(
            f"Evolution media send failed. Tried: {', '.join(errors)}"
        )

    def parse_incoming(self, data):
        if not all(k in data for k in ("from", "text", "id", "timestamp")):
            raise ValueError("Invalid incoming payload")
        return {
            "from": data["from"],
            "body": data["text"],
            "message_id": data["id"],
            "timestamp": data["timestamp"],
        }


@frappe.whitelist(allow_guest=True)
def handle_webhook():
    data = frappe.local.request.get_json(silent=True) or {}
    if not data:
        return "No payload"
    provider = EvolutionProvider(frappe.get_single("WhatsApp Settings").as_dict())
    try:
        msg = provider.parse_incoming(data)
    except ValueError:
        return "Invalid payload"
    from whatsapp_evolution.incoming import handle_incoming_message

    handle_incoming_message(msg)
    return "OK"
