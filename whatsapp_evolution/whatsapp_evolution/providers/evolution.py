import frappe
import requests
import base64
import hashlib
import json
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

    def _extract_session_error(self, response):
        """Return Evolution session error text if present in response body."""
        if response is None:
            return ""
        raw = (response.text or "").strip()
        if not raw:
            return ""
        if "SessionError: No sessions" in raw:
            return "SessionError: No sessions"
        try:
            payload = response.json()
        except Exception:
            return ""
        text = json.dumps(payload, ensure_ascii=False)
        if "SessionError: No sessions" in text:
            return "SessionError: No sessions"
        return ""

    def send_message(self, to_number, message, **kwargs):
        if not self._acquire_dedup("text", to_number, message or "", ttl=45):
            return {"id": "dedup-skip"}

        payload_variants = [
            {"number": to_number, "text": message},
            {"to": to_number, "text": message},
            {"number": to_number, "textMessage": {"text": message}},
            {
                "number": to_number,
                "options": {"delay": 1200, "presence": "composing"},
                "textMessage": {"text": message},
            },
        ]

        errors = []
        seen_session_error = ""
        for url in self._text_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=20)
                    response.raise_for_status()
                    return response.json()
                except requests.HTTPError as e:
                    session_error = self._extract_session_error(e.response)
                    if session_error:
                        seen_session_error = session_error
                    status_code = e.response.status_code if e.response is not None else "?"
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} -> {status_code} {body}".strip())
                except Exception as e:
                    errors.append(f"{url} -> {str(e)}")

        if seen_session_error:
            raise frappe.ValidationError(
                f"Evolution instance '{self.instance or '-'}' is not connected ({seen_session_error}). "
                "Open Evolution Manager, connect the instance (QR), then retry."
            )
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
        payload_variants = []

        if media_bytes:
            try:
                encoded = base64.b64encode(media_bytes).decode("ascii")
                media_name = filename or f"{media_type}.bin"
                # Prefer direct base64 payload when bytes are already available.
                payload_variants.append(
                    {
                        "number": to_number,
                        "mediaMessage": {
                            "mediatype": media_type,
                            "media": encoded,
                            "caption": caption or "",
                            "fileName": media_name,
                        },
                    }
                )
                payload_variants.append(
                    {
                        "number": to_number,
                        "mediatype": media_type,
                        "media": encoded,
                        "caption": caption or "",
                        "fileName": media_name,
                    }
                )
            except Exception:
                pass

        # Use URL variants only when we don't already have raw bytes.
        if media_url and not media_bytes:
            payload_variants.extend(
                [
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
                ]
            )
            # Optional base64 fallback for Evolution setups that do not accept remote URLs.
            try:
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
        seen_session_error = ""
        for url in self._media_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=25)
                    response.raise_for_status()
                    return response.json()
                except requests.HTTPError as e:
                    session_error = self._extract_session_error(e.response)
                    if session_error:
                        seen_session_error = session_error
                    status_code = e.response.status_code if e.response is not None else "?"
                    has_file_name = bool(payload.get("fileName") or (payload.get("mediaMessage") or {}).get("fileName"))
                    mode = "base64" if has_file_name else "url"
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} ({mode}) -> {status_code} {body}".strip())
                except Exception as e:
                    has_file_name = bool(payload.get("fileName") or (payload.get("mediaMessage") or {}).get("fileName"))
                    mode = "base64" if has_file_name else "url"
                    errors.append(f"{url} ({mode}) -> {str(e)}")

        if seen_session_error:
            raise frappe.ValidationError(
                f"Evolution instance '{self.instance or '-'}' is not connected ({seen_session_error}). "
                "Open Evolution Manager, connect the instance (QR), then retry."
            )
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

    def test_connection(self):
        """Check API reachability and instance session status."""
        if not self.api_base:
            return {"ok": False, "status": "error", "message": "Missing Evolution API Base"}
        if not self.token:
            return {"ok": False, "status": "error", "message": "Missing Evolution API Token"}
        if not self.instance:
            return {"ok": False, "status": "error", "message": "Missing Evolution Instance on WhatsApp Account"}

        urls = [
            self._build_url(f"/instance/connectionState/{self.instance}"),
            self._build_url(f"/instance/connection-state/{self.instance}"),
            self._build_url(f"/instance/fetchInstances"),
        ]
        last_error = ""
        for url in urls:
            try:
                response = requests.get(url, headers=self._headers(), timeout=20)
                if response.status_code == 404:
                    last_error = f"{url} -> 404"
                    continue
                response.raise_for_status()
                body = {}
                try:
                    body = response.json() or {}
                except Exception:
                    body = {}

                session_error = self._extract_session_error(response)
                if session_error:
                    return {"ok": False, "status": "disconnected", "message": session_error, "url": url}

                raw = json.dumps(body, ensure_ascii=False).lower()
                if any(k in raw for k in ("open", "connected", "online")):
                    return {"ok": True, "status": "connected", "url": url, "data": body}
                if any(k in raw for k in ("close", "closed", "disconnected", "offline")):
                    return {"ok": False, "status": "disconnected", "url": url, "data": body}
                return {"ok": True, "status": "reachable", "url": url, "data": body}
            except Exception as e:
                last_error = f"{url} -> {str(e)}"

        return {"ok": False, "status": "error", "message": last_error or "Unable to reach Evolution API"}


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
