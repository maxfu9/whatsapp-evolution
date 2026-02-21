"""Run on each event."""
import frappe

from frappe.core.doctype.server_script.server_script_utils import EVENT_MAP


def run_server_script_for_doc_event(doc, event):
    """Run on each event."""
    if event not in EVENT_MAP:
        return

    if frappe.flags.in_install:
        return

    if frappe.flags.in_migrate:
        return
    
    if frappe.flags.in_uninstall:
        return

    notification = get_notifications_map().get(
        doc.doctype, {}
    ).get(EVENT_MAP[event], None)

    if notification:
        # run all scripts for this doctype + event
        for notification_name in notification:
            try:
                frappe.get_doc(
                    "WhatsApp Notification",
                    notification_name
                ).send_template_message(doc)
            except Exception:
                frappe.log_error(
                    title=f"WhatsApp Notification failed: {notification_name}"
                )


def get_notifications_map():
    """Get mapping."""
    if frappe.flags.in_patch and not frappe.db.table_exists("WhatsApp Notification"):
        return {}

    cached_map = frappe.cache().get_value("whatsapp_notification_map")
    if cached_map is not None:
        return cached_map

    notification_map = {}
    enabled_whatsapp_notifications = frappe.get_all(
        "WhatsApp Notification",
        fields=("name", "reference_doctype", "doctype_event", "notification_type"),
        filters={"disabled": 0},
    )
    for notification in enabled_whatsapp_notifications:
        if notification.notification_type == "DocType Event":
            notification_map.setdefault(
                notification.reference_doctype, {}
            ).setdefault(
                notification.doctype_event, []
            ).append(notification.name)

    frappe.cache().set_value("whatsapp_notification_map", notification_map)

    return notification_map


def trigger_whatsapp_notifications_all():
    """Run all."""
    trigger_whatsapp_notifications("All")


def trigger_whatsapp_notifications_hourly():
    """Run hourly."""
    trigger_whatsapp_notifications("Hourly")


def trigger_whatsapp_notifications_daily():
    """Run daily."""
    trigger_whatsapp_notifications("Daily")


def trigger_whatsapp_notifications_weekly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Weekly")


def trigger_whatsapp_notifications_monthly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Monthly")


def trigger_whatsapp_notifications_yearly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Yearly")


def trigger_whatsapp_notifications_hourly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Hourly Long")


def trigger_whatsapp_notifications_daily_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Daily Long")


def trigger_whatsapp_notifications_weekly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Weekly Long")


def trigger_whatsapp_notifications_monthly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Monthly Long")


def trigger_whatsapp_notifications(event):
    """Run cron."""
    wa_notify_list = frappe.get_list(
        "WhatsApp Notification",
        filters={
            "event_frequency": event,
            "disabled": 0,
        }
    )

    for wa in wa_notify_list:
        frappe.get_doc(
            "WhatsApp Notification",
            wa.name,
        ).send_scheduled_message()

def get_whatsapp_account(phone_id=None, account_type='incoming'):
    """map whatsapp account with message"""
    meta = frappe.get_meta("WhatsApp Account")

    if phone_id:
        if meta.has_field("phone_id"):
            account_name = frappe.db.get_value('WhatsApp Account', {'phone_id': phone_id}, 'name')
            if account_name:
                return frappe.get_doc("WhatsApp Account", account_name)

    account_field_type = 'is_default_incoming' if account_type =='incoming' else 'is_default_outgoing' 
    if meta.has_field(account_field_type):
        default_account_name = frappe.db.get_value('WhatsApp Account', {account_field_type: 1}, 'name')
        if default_account_name:
            return frappe.get_doc("WhatsApp Account", default_account_name)

    if meta.has_field("is_default"):
        default_account_name = frappe.db.get_value("WhatsApp Account", {"is_default": 1, "status": "Active"}, "name")
        if default_account_name:
            return frappe.get_doc("WhatsApp Account", default_account_name)

    fallback_account_name = frappe.db.get_value("WhatsApp Account", {"status": "Active"}, "name")
    if fallback_account_name:
        return frappe.get_doc("WhatsApp Account", fallback_account_name)

    return None


def get_default_evolution_account():
    """Return default active Evolution account (or first active account)."""
    if not frappe.db.table_exists("WhatsApp Account"):
        return None

    default_name = frappe.db.get_value("WhatsApp Account", {"is_default": 1, "status": "Active"}, "name")
    if default_name:
        return frappe.get_doc("WhatsApp Account", default_name)

    fallback_name = frappe.db.get_value("WhatsApp Account", {"status": "Active"}, "name")
    if fallback_name:
        return frappe.get_doc("WhatsApp Account", fallback_name)

    return None


def get_evolution_settings(whatsapp_account=None):
    """Build effective Evolution config from account, with global fallback."""
    settings_doc = frappe.get_single("WhatsApp Settings")
    account_doc = None

    if whatsapp_account and frappe.db.exists("WhatsApp Account", whatsapp_account):
        account_doc = frappe.get_doc("WhatsApp Account", whatsapp_account)
    else:
        account_doc = get_default_evolution_account()

    base = (account_doc.get("evolution_api_base") if account_doc else None) or settings_doc.get("evolution_api_base")
    token = (
        (account_doc.get_password("evolution_api_token", raise_exception=False) if account_doc else None)
        or settings_doc.get_password("evolution_api_token")
    )
    instance = (account_doc.get("evolution_instance") if account_doc else None)
    send_endpoint = (
        (account_doc.get("evolution_send_endpoint") if account_doc else None)
        or settings_doc.get("evolution_send_endpoint")
    )

    return {
        "evolution_api_base": base,
        "evolution_api_token": token,
        "evolution_instance": instance,
        "evolution_send_endpoint": send_endpoint,
        "whatsapp_account": account_doc.name if account_doc else None,
    }


def is_evolution_enabled(whatsapp_account=None):
    settings = get_evolution_settings(whatsapp_account=whatsapp_account)
    return bool(settings.get("evolution_api_base") and settings.get("evolution_api_token"))

def format_number(number):
    """Format number."""
    if not number:
        return number

    if number.startswith("+"):
        number = number[1 : len(number)]

    return number


def cleanup_legacy_rq_jobs(needle="frappe_whatsapp"):
    """Delete stale RQ jobs that reference removed python module paths.

    This fixes rq.exceptions.DeserializationError when opening RQ Job list
    after app renames (e.g. frappe_whatsapp -> whatsapp_evolution).
    """
    from frappe.utils.background_jobs import get_redis_conn

    conn = get_redis_conn()
    needle_bytes = (needle or "").encode("utf-8")
    scanned = 0
    deleted = 0
    cursor = 0
    removed_ids = []

    while True:
        cursor, keys = conn.scan(cursor=cursor, match="rq:job:*", count=500)
        for key in keys or []:
            scanned += 1
            try:
                data = conn.hget(key, "data") or b""
            except Exception:
                continue
            if needle_bytes and needle_bytes not in data:
                continue

            job_id = key.decode().split("rq:job:", 1)[-1]
            removed_ids.append(job_id)
            conn.delete(key)
            # Remove references from queue/registry collections.
            conn.srem("rq:failed", job_id)
            conn.srem("rq:finished", job_id)
            conn.srem("rq:started", job_id)
            conn.srem("rq:deferred", job_id)
            conn.srem("rq:scheduled", job_id)
            conn.zrem("rq:scheduled_jobs", job_id)
            conn.zrem("rq:failed_jobs", job_id)
            conn.zrem("rq:finished_jobs", job_id)
            conn.zrem("rq:started_jobs", job_id)
            conn.zrem("rq:deferred_jobs", job_id)
            deleted += 1
        if cursor == 0:
            break

    return {
        "scanned": scanned,
        "deleted": deleted,
        "needle": needle,
        "removed_job_ids": removed_ids,
    }


def cleanup_broken_rq_jobs():
    """Delete RQ jobs that fail to deserialize."""
    from rq.job import Job
    from rq.exceptions import DeserializationError
    from frappe.utils.background_jobs import get_redis_conn

    conn = get_redis_conn()
    cursor = 0
    scanned = 0
    deleted = 0
    removed_ids = []

    while True:
        cursor, keys = conn.scan(cursor=cursor, match="rq:job:*", count=500)
        for key in keys or []:
            scanned += 1
            job_id = key.decode().split("rq:job:", 1)[-1]
            try:
                job = Job.fetch(job_id, connection=conn)
                # Trigger payload decode; this is where old module paths blow up.
                _ = job.kwargs
            except DeserializationError:
                conn.delete(key)
                conn.srem("rq:failed", job_id)
                conn.srem("rq:finished", job_id)
                conn.srem("rq:started", job_id)
                conn.srem("rq:deferred", job_id)
                conn.srem("rq:scheduled", job_id)
                conn.zrem("rq:scheduled_jobs", job_id)
                conn.zrem("rq:failed_jobs", job_id)
                conn.zrem("rq:finished_jobs", job_id)
                conn.zrem("rq:started_jobs", job_id)
                conn.zrem("rq:deferred_jobs", job_id)
                deleted += 1
                removed_ids.append(job_id)
            except Exception:
                # Ignore transient/missing job errors.
                pass
        if cursor == 0:
            break

    return {"scanned": scanned, "deleted": deleted, "removed_job_ids": removed_ids}
