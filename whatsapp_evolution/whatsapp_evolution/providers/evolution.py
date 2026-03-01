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
        event = data.get("event")
        payload = data.get("data") or {}
        if isinstance(payload, list):
            payload = payload[0] if payload else {}

        if event == "messages.upsert":
            message = payload.get("message") or {}
            key = payload.get("key") or {}
            
            # Extract number
            sender = key.get("remoteJid") or ""
            if "@" in sender:
                sender = sender.split("@")[0]
            
            # Extract text
            text = (
                message.get("conversation") 
                or (message.get("extendedTextMessage") or {}).get("text")
                or (message.get("imageMessage") or {}).get("caption")
                or (message.get("videoMessage") or {}).get("caption")
                or ""
            )
            
            return {
                "event": event,
                "from": sender,
                "body": text,
                "message_id": key.get("id"),
                "timestamp": payload.get("messageTimestamp"),
                "is_from_me": key.get("fromMe", False)
            }
            
        elif event == "messages.update":
            # Status update (ACK)
            key = payload.get("key") or {}
            update = payload.get("update") or {}
            status = update.get("status")

            return {
                "event": event,
                "message_id": key.get("id"),
                "status": status,
                "to": (key.get("remoteJid") or "").split("@")[0],
                "is_from_me": key.get("fromMe", True)
            }

        return {"event": event}


def _status_rank(status_text):
    order = {"Success": 1, "Sent": 1, "Delivered": 2, "Read": 3, "Played": 4}
    return order.get(status_text or "", 0)


def _map_evolution_status(status_value):
    if status_value is None:
        return None

    if isinstance(status_value, str):
        raw = status_value.strip()
        if not raw:
            return None
        if raw.isdigit():
            status_value = int(raw)
        else:
            text = raw.upper()
            if text in {"PENDING", "SERVER_ACK", "SENT", "ACK", "1"}:
                return "Sent"
            if text in {"DELIVERY_ACK", "DELIVERED", "2"}:
                return "Delivered"
            if text in {"READ", "READ_ACK", "3"}:
                return "Read"
            if text in {"PLAYED", "4"}:
                return "Played"
            return None

    if isinstance(status_value, (int, float)):
        status_map = {
            0: "Sent",
            1: "Sent",
            2: "Delivered",
            3: "Read",
            4: "Played",
        }
        return status_map.get(int(status_value))

    return None


def _message_id_candidates(message_id):
    raw = (message_id or "").strip()
    if not raw:
        return []
    variants = {raw}
    if raw.startswith("wamid."):
        variants.add(raw.replace("wamid.", "", 1))
    else:
        variants.add(f"wamid.{raw}")
    return list(variants)


def _find_message_name_by_id(message_id):
    candidates = _message_id_candidates(message_id)
    if not candidates:
        return None
    rows = frappe.get_all(
        "WhatsApp Message",
        filters={"message_id": ["in", candidates]},
        fields=["name", "status"],
        order_by="modified desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _log_webhook_debug(payload):
    try:
        frappe.logger("whatsapp_evolution.webhook").info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass

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
    
    event_type = data.get("event")
    if not event_type:
        return "No event"

    provider = EvolutionProvider(frappe.get_single("WhatsApp Settings").as_dict())
    msg = provider.parse_incoming(data)
    
    if event_type == "messages.upsert":
        if msg.get("is_from_me"):
            # Update outgoing message status if we find it by ID
            if msg.get("message_id"):
                found = _find_message_name_by_id(msg.get("message_id"))
                if found and _status_rank("Sent") >= _status_rank(found.get("status")):
                    frappe.db.set_value("WhatsApp Message", found.get("name"), "status", "Sent")
            return "OK"
            
        from whatsapp_evolution.incoming import handle_incoming_message
        handle_incoming_message(msg)
        
    elif event_type == "messages.update":
        status_code = msg.get("status")
        message_id = msg.get("message_id")
        key_data = (data.get("data") or {}).get("key") if isinstance(data.get("data"), dict) else {}
        remote_jid = (key_data or {}).get("remoteJid")
        from_me = (key_data or {}).get("fromMe")
        status_text = _map_evolution_status(status_code)

        _log_webhook_debug(
            {
                "event": "messages.update.received",
                "message_id": message_id,
                "status_raw": status_code,
                "status_mapped": status_text,
                "remote_jid": remote_jid,
                "from_me": from_me,
            }
        )

        if message_id and status_code is not None:
            if status_text:
                found = _find_message_name_by_id(message_id)
                if found and _status_rank(status_text) >= _status_rank(found.get("status")):
                    frappe.db.set_value("WhatsApp Message", found.get("name"), "status", status_text)
                    _log_webhook_debug(
                        {
                            "event": "messages.update.applied",
                            "message_id": message_id,
                            "docname": found.get("name"),
                            "previous_status": found.get("status"),
                            "new_status": status_text,
                        }
                    )
                else:
                    _log_webhook_debug(
                        {
                            "event": "messages.update.skipped",
                            "reason": "no_match_or_lower_rank",
                            "message_id": message_id,
                            "status_mapped": status_text,
                            "matched_doc": found.get("name") if found else None,
                            "current_status": found.get("status") if found else None,
                        }
                    )
            else:
                _log_webhook_debug(
                    {
                        "event": "messages.update.skipped",
                        "reason": "unmapped_status",
                        "message_id": message_id,
                        "status_raw": status_code,
                    }
                )
        else:
            _log_webhook_debug(
                {
                    "event": "messages.update.skipped",
                    "reason": "missing_message_id_or_status",
                    "message_id": message_id,
                    "status_raw": status_code,
                }
            )

    return "OK"
