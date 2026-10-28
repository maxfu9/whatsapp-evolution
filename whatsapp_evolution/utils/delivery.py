"""Delivery outcomes and recovery of abandoned app-owned send records."""

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils import background_jobs


def get_job(job_id):
    """Use the native helper, with the equivalent RQ lookup on Frappe v14."""
    if hasattr(background_jobs, "get_job"):
        return background_jobs.get_job(job_id)
    from rq.exceptions import NoSuchJobError
    from rq.job import Job
    try:
        return Job.fetch(background_jobs.create_job_id(job_id), connection=background_jobs.get_redis_conn())
    except NoSuchJobError:
        return None


def send_status(response=None, error=None):
    if error is not None:
        return "Needs Review" if getattr(error, "delivery_unknown", False) else "Failed"
    if isinstance(response, dict) and (response.get("skipped") or response.get("id") == "dedup-skip"):
        return "Skipped"
    return "Success"


def send_job_id(message_name):
    return f"wa-send-{message_name}"


def start_queued_message(name):
    if not name:
        return True
    status = frappe.db.get_value("WhatsApp Message", name, "status", for_update=True)
    if status != "Queued":
        # Do not replay finished jobs, uncertain attempts, or records flagged by
        # reconciliation. A retry of a Started job could duplicate delivery.
        frappe.db.commit()
        return False
    frappe.db.set_value("WhatsApp Message", name, {"status": "Started", "last_error": "", "message_id": ""})
    frappe.db.commit()
    return True


def reconcile_stale_messages():
    """Flag abandoned attempts for review, never resend them or claim failure."""
    cutoff = add_to_date(now_datetime(), hours=-24)
    rows = frappe.get_all(
        "WhatsApp Message",
        filters={"type": "Outgoing", "status": ["in", ["Queued", "Started"]], "modified": ["<", cutoff]},
        fields=["name"], limit_page_length=500, order_by="modified asc",
    )
    for row in rows:
        job = get_job(send_job_id(row.name))
        if job:
            status = job.get_status(refresh=True)
            if getattr(status, "value", status) in {"queued", "started", "deferred", "scheduled"}:
                continue
        current = frappe.db.get_value(
            "WhatsApp Message", row.name, ["status", "modified"], as_dict=True, for_update=True,
        )
        if current and current.status in {"Queued", "Started"} and current.modified < cutoff:
            frappe.db.set_value("WhatsApp Message", row.name, {
                "status": "Needs Review",
                "last_error": "No completed send outcome was recorded within 24 hours. "
                "Delivery is unknown. Check WhatsApp before sending again; no automatic retry was made.",
            })
        frappe.db.commit()
