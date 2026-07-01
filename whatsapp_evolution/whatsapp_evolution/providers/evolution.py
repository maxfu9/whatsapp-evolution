import frappe
import requests
import base64
import hashlib
import json
import mimetypes
from urllib.parse import urlparse, unquote
from .base import BaseProvider


class EvolutionProvider(BaseProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.api_base = (settings.get("evolution_api_base") or "").rstrip("/")
        self.token = settings.get("evolution_api_token")
        self.instance = (settings.get("evolution_instance") or "").strip().strip("/")
        self.api_version = (settings.get("evolution_api_version") or "v1").strip().lower()
        if self.api_version not in {"v1", "v2"}:
            self.api_version = "v1"
        self.strict_api_version = self._truthy(settings.get("evolution_strict_api_version"))
        self.send_endpoint = (settings.get("evolution_send_endpoint") or "").strip()

    def _truthy(self, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "apikey": self.token or "",
            "Content-Type": "application/json",
        }

    def _success_result(self, response):
        try:
            return response.json()
        except ValueError:
            return {
                "ok": True,
                "status_code": response.status_code,
                "text": response.text or "",
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

    def _custom_candidate_urls(self):
        if not self.send_endpoint:
            return []
        if "{instance}" in self.send_endpoint:
            if not self.instance:
                return []
            return [self._build_url(self.send_endpoint.replace("{instance}", self.instance))]
        return [self._build_url(self.send_endpoint)]

    def _dedupe_urls(self, urls):
        return [u for i, u in enumerate(urls) if u and u not in urls[:i]]

    def _ordered_urls(self, preferred, fallback):
        if self.strict_api_version:
            return self._dedupe_urls(self._custom_candidate_urls() + preferred)
        return self._dedupe_urls(self._custom_candidate_urls() + preferred + fallback)

    def _text_candidate_urls(self):
        preferred = []
        fallback = []
        if self.instance:
            preferred.extend(
                [
                    self._build_url(f"/message/sendText/{self.instance}"),
                ]
            )
            fallback.append(self._build_url(f"/messages/{self.instance}"))
        preferred.append(self._build_url("/message/sendText"))
        fallback.append(self._build_url("/messages"))
        return self._ordered_urls(preferred, fallback)

    def _media_candidate_urls(self):
        preferred = []
        fallback = []
        if self.instance:
            preferred.extend(
                [
                    self._build_url(f"/message/sendMedia/{self.instance}"),
                ]
            )
            fallback.append(self._build_url(f"/messages/{self.instance}"))
        preferred.append(self._build_url("/message/sendMedia"))
        fallback.append(self._build_url("/messages"))
        return self._ordered_urls(preferred, fallback)

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

    def _instance_record_name(self, record):
        if not isinstance(record, dict):
            return ""
        nested = record.get("instance") if isinstance(record.get("instance"), dict) else {}
        return (
            record.get("instanceName")
            or record.get("instance_name")
            or record.get("name")
            or nested.get("instanceName")
            or nested.get("instance_name")
            or nested.get("name")
            or ""
        )

    def _instance_connection_state(self, record):
        if not isinstance(record, dict):
            return ""
        nested = record.get("instance") if isinstance(record.get("instance"), dict) else {}
        value = (
            record.get("state")
            or record.get("connectionState")
            or record.get("connectionStatus")
            or record.get("status")
            or nested.get("state")
            or nested.get("connectionState")
            or nested.get("connectionStatus")
            or nested.get("status")
            or ""
        )
        return str(value).strip().lower()

    def _iter_instance_records(self, body):
        if isinstance(body, list):
            return [row for row in body if isinstance(row, dict)]
        if not isinstance(body, dict):
            return []
        for key in ("instances", "data", "response"):
            value = body.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = self._iter_instance_records(value)
                if nested:
                    return nested
        return [body]

    def _connection_result_from_body(self, body, url, require_instance_match=False):
        records = self._iter_instance_records(body)
        target = (self.instance or "").strip()
        if require_instance_match and target:
            matched = [row for row in records if self._instance_record_name(row) == target]
            if not matched:
                return {
                    "ok": False,
                    "status": "disconnected",
                    "message": f"Instance '{target}' not found in Evolution response",
                    "url": url,
                    "data": body,
                }
            records = matched

        states = [self._instance_connection_state(row) for row in records]
        state_text = " ".join([state for state in states if state])
        if any(state in {"open", "connected", "online"} for state in states):
            return {"ok": True, "status": "connected", "url": url, "data": body}
        if any(state in {"close", "closed", "disconnected", "offline"} for state in states):
            return {"ok": False, "status": "disconnected", "url": url, "data": body}
        if require_instance_match:
            return {
                "ok": False,
                "status": "unknown",
                "message": f"Instance '{target}' state is unknown{': ' + state_text if state_text else ''}",
                "url": url,
                "data": body,
            }
        return {"ok": True, "status": "reachable", "url": url, "data": body}

    def _ordered_payloads(self, preferred, fallback):
        if self.api_version == "v2":
            return preferred if self.strict_api_version else preferred + fallback
        return fallback if self.strict_api_version else fallback + preferred

    def _payload_shape(self, payload):
        if payload.get("textMessage"):
            return "v1_text"
        if payload.get("mediaMessage"):
            return "v1_media"
        if "mediatype" in payload:
            return "v2_media"
        if "text" in payload:
            return "v2_text"
        return "unknown"

    def _media_payload_mode(self, payload):
        media = payload.get("media")
        if media is None and isinstance(payload.get("mediaMessage"), dict):
            media = payload.get("mediaMessage").get("media")
        return "url" if isinstance(media, str) and media.startswith(("http://", "https://")) else "base64"

    def _log_send_outcome(self, kind, outcome, url=None, payload_shape=None, response=None, error=None):
        try:
            details = {
                "kind": kind,
                "outcome": outcome,
                "api_version": self.api_version,
                "strict_api_version": self.strict_api_version,
            }
            if url:
                details["url"] = url
            if payload_shape:
                details["payload_shape"] = payload_shape
            if response is not None:
                details["status_code"] = response.status_code
                details["response"] = (response.text or "").strip().replace("\n", " ")[:180]
            if error:
                details["error"] = str(error)[:180]
            frappe.logger("whatsapp_evolution.evolution").info(json.dumps(details, ensure_ascii=False))
        except Exception:
            pass

    def _text_payload_variants(self, to_number, message):
        v2_payloads = [
            {"number": to_number, "text": message},
            {"to": to_number, "text": message},
        ]
        v1_payloads = [
            {"number": to_number, "textMessage": {"text": message}},
            {
                "number": to_number,
                "textMessage": {"text": message},
                "options": {"delay": 1200, "presence": "composing"},
            },
        ]
        return self._ordered_payloads(v2_payloads, v1_payloads)

    def _guess_mimetype(self, filename=None, media_type=None):
        guessed = mimetypes.guess_type(filename or "")[0] if filename else None
        if guessed:
            return guessed
        defaults = {
            "image": "image/jpeg",
            "video": "video/mp4",
            "audio": "audio/mpeg",
            "document": "application/octet-stream",
        }
        return defaults.get((media_type or "").lower(), "application/octet-stream")

    def _filename_from_url(self, media_url, media_type):
        parsed = urlparse(media_url or "")
        filename = unquote((parsed.path or "").rstrip("/").split("/")[-1])
        return filename or f"{media_type}.bin"

    def _media_payload_variants(self, to_number, media_type, caption="", media_value="", filename=None):
        mime_type = self._guess_mimetype(filename=filename, media_type=media_type)
        v2_payloads = [
            {
                "number": to_number,
                "mediatype": media_type,
                "mimetype": mime_type,
                "media": media_value,
                "caption": caption or "",
                "fileName": filename or f"{media_type}.bin",
            },
            {
                "to": to_number,
                "mediatype": media_type,
                "mimetype": mime_type,
                "media": media_value,
                "caption": caption or "",
                "fileName": filename or f"{media_type}.bin",
            },
        ]
        v1_payloads = [
            {
                "number": to_number,
                "mediaMessage": {
                    "mediaType": media_type,
                    "fileName": filename or f"{media_type}.bin",
                    "caption": caption or "",
                    "media": media_value,
                },
            },
            {
                "number": to_number,
                "mediaMessage": {
                    "mediaType": media_type,
                    "fileName": filename or f"{media_type}.bin",
                    "caption": caption or "",
                    "media": media_value,
                },
                "options": {"delay": 1200, "presence": "composing"},
            },
        ]
        return self._ordered_payloads(v2_payloads, v1_payloads)

    def send_message(self, to_number, message, **kwargs):
        if not self._acquire_dedup("text", to_number, message or "", ttl=45):
            return {"id": "dedup-skip"}

        payload_variants = self._text_payload_variants(to_number, message)

        errors = []
        seen_session_error = ""
        for url in self._text_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=20)
                    response.raise_for_status()
                    self._log_send_outcome("text", "success", url, self._payload_shape(payload), response=response)
                    return self._success_result(response)
                except requests.HTTPError as e:
                    session_error = self._extract_session_error(e.response)
                    if session_error:
                        seen_session_error = session_error
                    status_code = e.response.status_code if e.response is not None else "?"
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} [{self._payload_shape(payload)}] -> {status_code} {body}".strip())
                except Exception as e:
                    errors.append(f"{url} [{self._payload_shape(payload)}] -> {str(e)}")

        if seen_session_error:
            self._log_send_outcome("text", "failed", error=seen_session_error)
            raise frappe.ValidationError(
                f"Evolution instance '{self.instance or '-'}' is not connected ({seen_session_error}). "
                "Open Evolution Manager, connect the instance (QR), then retry."
            )
        self._log_send_outcome("text", "failed", error=", ".join(errors))
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
                payload_variants.extend(
                    self._media_payload_variants(
                        to_number=to_number,
                        media_type=media_type,
                        caption=caption,
                        media_value=encoded,
                        filename=media_name,
                    )
                )
            except Exception:
                pass

        # Use URL variants only when we don't already have raw bytes.
        if media_url and not media_bytes:
            media_name = filename or self._filename_from_url(media_url, media_type)
            payload_variants.extend(
                self._media_payload_variants(
                    to_number=to_number,
                    media_type=media_type,
                    caption=caption,
                    media_value=media_url,
                    filename=media_name,
                )
            )
            # Optional base64 fallback for Evolution setups that do not accept remote URLs.
            try:
                response = requests.get(media_url, timeout=20)
                response.raise_for_status()
                encoded = base64.b64encode(response.content).decode("ascii")
                payload_variants.extend(
                    self._media_payload_variants(
                        to_number=to_number,
                        media_type=media_type,
                        caption=caption,
                        media_value=encoded,
                        filename=media_name,
                    )
                )
            except Exception:
                pass

        errors = []
        seen_session_error = ""
        for url in self._media_candidate_urls():
            for payload in payload_variants:
                try:
                    response = requests.post(url, json=payload, headers=self._headers(), timeout=25)
                    response.raise_for_status()
                    self._log_send_outcome("media", "success", url, self._payload_shape(payload), response=response)
                    return self._success_result(response)
                except requests.HTTPError as e:
                    session_error = self._extract_session_error(e.response)
                    if session_error:
                        seen_session_error = session_error
                    status_code = e.response.status_code if e.response is not None else "?"
                    mode = self._media_payload_mode(payload)
                    body = ""
                    if e.response is not None:
                        body = (e.response.text or "").strip().replace("\n", " ")[:180]
                    errors.append(f"{url} [{self._payload_shape(payload)}:{mode}] -> {status_code} {body}".strip())
                except Exception as e:
                    mode = self._media_payload_mode(payload)
                    errors.append(f"{url} [{self._payload_shape(payload)}:{mode}] -> {str(e)}")

        if seen_session_error:
            self._log_send_outcome("media", "failed", error=seen_session_error)
            raise frappe.ValidationError(
                f"Evolution instance '{self.instance or '-'}' is not connected ({seen_session_error}). "
                "Open Evolution Manager, connect the instance (QR), then retry."
            )
        self._log_send_outcome("media", "failed", error=", ".join(errors))
        raise frappe.ValidationError(
            f"Evolution media send failed. Tried: {', '.join(errors)}"
        )

    def parse_incoming(self, data):
        event = _normalize_event_type(data.get("event"))
        payload = data.get("data") or {}
        if isinstance(payload, list):
            payload = payload[0] if payload else {}

        if event == "messages.upsert":
            message = payload.get("message") or {}
            key = payload.get("key") or {}

            # Extract number
            sender = (
                key.get("remoteJid")
                or key.get("participant")
                or payload.get("remoteJid")
                or payload.get("participant")
                or ""
            )
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
                "is_from_me": key.get("fromMe", False),
            }

        elif event == "messages.update":
            # Status update (ACK)
            key = payload.get("key") or {}
            update = payload.get("update") or {}
            status = (
                update.get("status")
                if isinstance(update, dict)
                else None
            )
            if status is None and isinstance(update, dict):
                status = update.get("ack")
            if status is None and isinstance(update, dict):
                status = (update.get("message") or {}).get("status")
            if status is None and isinstance(update, dict):
                status = (update.get("message") or {}).get("ack")
            if status is None:
                status = payload.get("status")
            if status is None:
                status = payload.get("ack")
            if status is None:
                status = (payload.get("statusMessage") or {}).get("status")
            if status is None:
                status = (payload.get("message") or {}).get("status")
            if status is None:
                status = (payload.get("message") or {}).get("ack")

            return {
                "event": event,
                "message_id": key.get("id") or payload.get("id"),
                "status": status,
                "to": (key.get("remoteJid") or payload.get("remoteJid") or "").split("@")[0],
                "is_from_me": key.get("fromMe", True),
            }

        return {"event": event}

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

                return self._connection_result_from_body(
                    body,
                    url,
                    require_instance_match="fetchInstances" in url,
                )
            except Exception as e:
                last_error = f"{url} -> {str(e)}"

        return {"ok": False, "status": "error", "message": last_error or "Unable to reach Evolution API"}


def _status_rank(status_text):
    order = {"Success": 1, "Sent": 1, "Delivered": 2, "Read": 3, "Played": 4}
    return order.get(status_text or "", 0)


def _normalize_event_type(event_type):
    if event_type is None:
        return ""

    normalized = str(event_type).strip().lower().replace("_", ".").replace("-", ".")
    aliases = {
        "messages.upsert": "messages.upsert",
        "message.upsert": "messages.upsert",
        "messages.update": "messages.update",
        "message.update": "messages.update",
    }
    return aliases.get(normalized, normalized)


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
            if text in {"DELIVERY_ACK", "DELIVERED", "DELIVERY", "2"}:
                return "Delivered"
            if text in {"READ", "READ_ACK", "READ_RECEIPT", "3"}:
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


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _numbers_match(a, b):
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    # Handle local/international variants (e.g. 0331... vs 92331...)
    return da.endswith(db[-10:]) or db.endswith(da[-10:])


def _find_recent_outgoing_by_number(remote_jid, account_name=None):
    number = (remote_jid or "").split("@")[0]
    if not number:
        return None

    rows = frappe.get_all(
        "WhatsApp Message",
        filters={
            "type": "Outgoing",
            "creation": [">=", frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-24)],
        },
        fields=["name", "status", "to", "whatsapp_account", "creation"],
        order_by="creation desc",
        limit_page_length=80,
    )

    candidates = [r for r in rows if _numbers_match(r.get("to"), number)]
    if account_name:
        account_candidates = [r for r in candidates if (r.get("whatsapp_account") or "") == account_name]
        return account_candidates[0] if account_candidates else None

    return candidates[0] if candidates else None


def _log_webhook_debug(payload):
    try:
        frappe.logger("whatsapp_evolution.webhook").info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _normalize_webhook_data(data):
    payload = data.get("data") or {}
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _extract_instance_name(data, payload_data):
    if not isinstance(data, dict):
        data = {}
    if not isinstance(payload_data, dict):
        payload_data = {}
    return (
        data.get("instance")
        or data.get("instanceName")
        or data.get("instance_name")
        or payload_data.get("instance")
        or payload_data.get("instanceName")
        or payload_data.get("instance_name")
    )


def _account_name_from_instance(instance_name):
    if not instance_name:
        return None
    if frappe.db.exists("WhatsApp Account", {"name": instance_name, "status": "Active"}):
        return instance_name
    return frappe.db.get_value(
        "WhatsApp Account",
        {"evolution_instance": instance_name, "status": "Active"},
        "name",
    )


@frappe.whitelist(allow_guest=True)
def handle_webhook():
    data = frappe.local.request.get_json(silent=True) or {}
    if not data:
        return "No payload"
    
    event_type = _normalize_event_type(data.get("event"))
    if not event_type:
        return "No event"

    provider = EvolutionProvider(frappe.get_single("WhatsApp Settings").as_dict())
    msg = provider.parse_incoming(data)
    payload_data = _normalize_webhook_data(data)
    instance_name = _extract_instance_name(data, payload_data)
    account_name = _account_name_from_instance(instance_name)
    
    if event_type == "messages.upsert":
        if msg.get("is_from_me"):
            # Update outgoing message status if we find it by ID
            if msg.get("message_id"):
                found = _find_message_name_by_id(msg.get("message_id"))
                if found and _status_rank("Sent") >= _status_rank(found.get("status")):
                    frappe.db.set_value("WhatsApp Message", found.get("name"), "status", "Sent")
            return "OK"
            
        from whatsapp_evolution.incoming import handle_incoming_message
        handle_incoming_message(msg, whatsapp_account=account_name)
        
    elif event_type == "messages.update":
        status_code = msg.get("status")
        message_id = msg.get("message_id")
        key_data = payload_data.get("key") if isinstance(payload_data, dict) else {}
        remote_jid = (key_data or {}).get("remoteJid")
        from_me = (key_data or {}).get("fromMe")
        if not remote_jid:
            remote_jid = msg.get("to")
            if remote_jid and "@" not in remote_jid:
                remote_jid = f"{remote_jid}@s.whatsapp.net"
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

        if status_code is not None:
            if status_text:
                found_by_id = _find_message_name_by_id(message_id) if message_id else None
                found = found_by_id
                if not found and remote_jid:
                    found = _find_recent_outgoing_by_number(remote_jid, account_name=account_name)
                if found and _status_rank(status_text) >= _status_rank(found.get("status")):
                    frappe.db.set_value("WhatsApp Message", found.get("name"), "status", status_text)
                    _log_webhook_debug(
                        {
                            "event": "messages.update.applied",
                            "message_id": message_id,
                            "docname": found.get("name"),
                            "previous_status": found.get("status"),
                            "new_status": status_text,
                            "fallback_by_number": bool(not found_by_id and remote_jid),
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
