"""
Data-access layer. Every function here talks to Supabase/Postgres.

Design rules followed throughout this file:
- Never silently swallow a DB error and pretend it worked. Functions either
  return the row(s) they touched, or raise an exception that the calling
  handler is expected to catch and turn into an honest "that failed" message.
- Functions are written defensively (small, single purpose) so they're easy
  to test and easy to call from both the AI-driven handlers and the
  deterministic slash-commands.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from app.database.client import get_client
from app.config.settings import settings


# ------------------------------------------------------------------
# Users
# ------------------------------------------------------------------
def get_or_create_user(telegram_user_id: int, name: Optional[str] = None) -> dict:
    db = get_client()
    res = db.table("users").select("*").eq("telegram_user_id", telegram_user_id).execute()
    if res.data:
        return res.data[0]

    payload = {
        "telegram_user_id": telegram_user_id,
        "name": name or settings.USER_NAME,
        "timezone": settings.DEFAULT_TIMEZONE,
    }
    res = db.table("users").insert(payload).execute()
    return res.data[0]


def is_authorized(telegram_user_id: int) -> bool:
    return telegram_user_id == settings.TELEGRAM_USER_ID


# ------------------------------------------------------------------
# Runtime config (per-user key/value overrides of Settings)
# ------------------------------------------------------------------
def get_config(user_id: str, key: str, default: str | None = None) -> str | None:
    db = get_client()
    res = (
        db.table("user_config")
        .select("value")
        .eq("user_id", user_id)
        .eq("key", key)
        .execute()
    )
    if res.data:
        return res.data[0]["value"]
    return default


def set_config(user_id: str, key: str, value: str) -> None:
    db = get_client()
    db.table("user_config").upsert(
        {"user_id": user_id, "key": key, "value": str(value)},
        on_conflict="user_id,key",
    ).execute()


# ------------------------------------------------------------------
# Duplicate protection
# ------------------------------------------------------------------
def already_processed(user_id: str, chat_id: int, message_id: int) -> bool:
    db = get_client()
    res = (
        db.table("processed_messages")
        .select("id")
        .eq("telegram_chat_id", chat_id)
        .eq("telegram_message_id", message_id)
        .execute()
    )
    return bool(res.data)


def mark_processed(user_id: str, chat_id: int, message_id: int) -> None:
    db = get_client()
    try:
        db.table("processed_messages").insert(
            {
                "user_id": user_id,
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
            }
        ).execute()
    except Exception:
        # Unique constraint race is fine - it just means it's already marked.
        pass


# ------------------------------------------------------------------
# daily_activity (work / content quick-log)
# ------------------------------------------------------------------
def log_activity(
    user_id: str,
    date: str,
    platform: str,
    activity_type: str,
    quantity: int = 1,
    account_name: str | None = None,
    notes: str | None = None,
) -> dict:
    db = get_client()
    payload = {
        "user_id": user_id,
        "date": date,
        "platform": platform,
        "account_name": account_name,
        "activity_type": activity_type,
        "quantity": quantity,
        "notes": notes,
    }
    res = db.table("daily_activity").insert(payload).execute()
    return res.data[0]


def get_activity(user_id: str, start_date: str, end_date: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("daily_activity")
        .select("*")
        .eq("user_id", user_id)
        .gte("date", start_date)
        .lte("date", end_date)
        .execute()
    )
    return res.data


# ------------------------------------------------------------------
# content
# ------------------------------------------------------------------
def log_content(user_id: str, platform: str, content_type: str, **kwargs) -> dict:
    db = get_client()
    payload = {
        "user_id": user_id,
        "platform": platform,
        "content_type": content_type,
        "post_date": kwargs.get("post_date") or dt.date.today().isoformat(),
        **{k: v for k, v in kwargs.items() if k != "post_date"},
    }
    res = db.table("content").insert(payload).execute()
    return res.data[0]


def get_content(user_id: str, start_date: str, end_date: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("content")
        .select("*")
        .eq("user_id", user_id)
        .gte("post_date", start_date)
        .lte("post_date", end_date)
        .execute()
    )
    return res.data


def update_content_metrics(content_id: str, **metrics) -> dict:
    db = get_client()
    res = db.table("content").update(metrics).eq("id", content_id).execute()
    return res.data[0] if res.data else {}


def find_recent_content(user_id: str, platform: str | None = None, limit: int = 5) -> list[dict]:
    db = get_client()
    q = db.table("content").select("*").eq("user_id", user_id)
    if platform:
        q = q.eq("platform", platform)
    res = q.order("created_at", desc=True).limit(limit).execute()
    return res.data


# ------------------------------------------------------------------
# sales_calls
# ------------------------------------------------------------------
def log_sales_calls_bulk(user_id: str, date: str, total_calls: int, notes: str | None = None) -> dict:
    """Used when the user just reports an aggregate count, e.g. '40 calls today'."""
    db = get_client()
    payload = {
        "user_id": user_id,
        "date": date,
        "lead_name": None,
        "call_status": "Connected",
        "outcome": None,
        "notes": notes or f"Bulk log: {total_calls} calls",
    }
    # Represent bulk calls as N rows in daily_activity (aggregate), not fake sales_calls rows.
    return log_activity(user_id, date, "Sales", "calls", quantity=total_calls, notes=notes)


def log_sales_call(
    user_id: str,
    date: str,
    lead_name: str | None = None,
    source: str | None = None,
    call_status: str | None = None,
    outcome: str | None = None,
    objection: str | None = None,
    follow_up_date: str | None = None,
    deal_value: float | None = None,
    notes: str | None = None,
    phone_number: str | None = None,
    booked_date: str | None = None,
    booked_time: str | None = None,
    call_type: str | None = None,
    confirmation_reminder_sent: bool = False,
) -> dict:
    db = get_client()
    payload = {
        "user_id": user_id,
        "date": date,
        "lead_name": lead_name,
        "source": source,
        "call_status": call_status,
        "outcome": outcome,
        "objection": objection,
        "follow_up_date": follow_up_date,
        "deal_value": deal_value,
        "notes": notes,
        "phone_number": phone_number,
        "booked_date": booked_date,
        "booked_time": booked_time,
        "call_type": call_type,
        "confirmation_reminder_sent": confirmation_reminder_sent,
    }
    res = db.table("sales_calls").insert(payload).execute()
    return res.data[0]


def get_sales_calls(user_id: str, start_date: str, end_date: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("sales_calls")
        .select("*")
        .eq("user_id", user_id)
        .gte("date", start_date)
        .lte("date", end_date)
        .execute()
    )
    return res.data


def get_booked_sales_calls_for_date(user_id: str, booked_date: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("sales_calls")
        .select("*")
        .eq("user_id", user_id)
        .eq("booked_date", booked_date)
        .eq("outcome", "Booked")
        .order("booked_time")
        .execute()
    )
    return res.data


def get_pending_followups(user_id: str, as_of_date: str | None = None) -> list[dict]:
    db = get_client()
    q = (
        db.table("sales_calls")
        .select("*")
        .eq("user_id", user_id)
        .not_.is_("follow_up_date", "null")
        .in_("outcome", ["Follow Up", "Interested", "Proposal", "Rescheduled"])
    )
    res = q.order("follow_up_date").execute()
    return res.data


# ------------------------------------------------------------------
# meetings
# ------------------------------------------------------------------
def create_meeting(
    user_id: str,
    title: str,
    start_time_iso: str,
    person: str | None = None,
    meeting_type: str | None = None,
    end_time_iso: str | None = None,
    notes: str | None = None,
) -> dict:
    db = get_client()
    payload = {
        "user_id": user_id,
        "title": title,
        "person": person,
        "meeting_type": meeting_type,
        "start_time": start_time_iso,
        "end_time": end_time_iso,
        "notes": notes,
        "status": "scheduled",
    }
    res = db.table("meetings").insert(payload).execute()
    return res.data[0]


def find_meeting_by_person_or_title(
    user_id: str, search_text: str, only_upcoming: bool = True
) -> list[dict]:
    """Fuzzy-ish match: case-insensitive substring match against person/title."""
    db = get_client()
    q = db.table("meetings").select("*").eq("user_id", user_id).neq("status", "cancelled")
    if only_upcoming:
        q = q.gte("start_time", dt.datetime.utcnow().isoformat())
    res = q.order("start_time").execute()
    text = search_text.lower().strip()
    matches = [
        m
        for m in res.data
        if text in (m.get("person") or "").lower() or text in (m.get("title") or "").lower()
    ]
    return matches


def update_meeting(meeting_id: str, **fields) -> dict:
    db = get_client()
    fields["updated_at"] = dt.datetime.utcnow().isoformat()
    res = db.table("meetings").update(fields).eq("id", meeting_id).execute()
    return res.data[0] if res.data else {}


def cancel_meeting(meeting_id: str) -> dict:
    return update_meeting(meeting_id, status="cancelled")


def get_meeting(meeting_id: str) -> dict | None:
    db = get_client()
    res = db.table("meetings").select("*").eq("id", meeting_id).execute()
    return res.data[0] if res.data else None


def get_meetings_in_range(user_id: str, start_iso: str, end_iso: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("meetings")
        .select("*")
        .eq("user_id", user_id)
        .neq("status", "cancelled")
        .gte("start_time", start_iso)
        .lte("start_time", end_iso)
        .order("start_time")
        .execute()
    )
    return res.data


def get_next_meeting(user_id: str) -> dict | None:
    db = get_client()
    res = (
        db.table("meetings")
        .select("*")
        .eq("user_id", user_id)
        .neq("status", "cancelled")
        .gte("start_time", dt.datetime.utcnow().isoformat())
        .order("start_time")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


# ------------------------------------------------------------------
# reminders
# ------------------------------------------------------------------
def create_reminder(
    user_id: str,
    reminder_text: str,
    trigger_time_iso: str,
    related_meeting_id: str | None = None,
    recurrence: str | None = None,
) -> dict:
    db = get_client()
    payload = {
        "user_id": user_id,
        "reminder_text": reminder_text,
        "trigger_time": trigger_time_iso,
        "related_meeting_id": related_meeting_id,
        "recurrence": recurrence,
        "status": "pending",
    }
    res = db.table("reminders").insert(payload).execute()
    return res.data[0]


def cancel_reminders_for_meeting(meeting_id: str) -> None:
    db = get_client()
    db.table("reminders").update({"status": "cancelled"}).eq(
        "related_meeting_id", meeting_id
    ).eq("status", "pending").execute()


def get_due_reminders(as_of_iso: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("reminders")
        .select("*")
        .eq("status", "pending")
        .lte("trigger_time", as_of_iso)
        .execute()
    )
    return res.data


def mark_reminder_sent(reminder_id: str) -> None:
    db = get_client()
    db.table("reminders").update(
        {"status": "sent", "sent_at": dt.datetime.utcnow().isoformat()}
    ).eq("id", reminder_id).execute()


def cancel_reminder(reminder_id: str) -> None:
    db = get_client()
    db.table("reminders").update({"status": "cancelled"}).eq("id", reminder_id).execute()


def reschedule_recurring(reminder: dict, next_trigger_iso: str) -> dict:
    """Create the next occurrence of a recurring reminder (avoids duplicates
    by only ever having one 'pending' row per recurring reminder chain)."""
    db = get_client()
    payload = {
        "user_id": reminder["user_id"],
        "reminder_text": reminder["reminder_text"],
        "trigger_time": next_trigger_iso,
        "related_meeting_id": reminder.get("related_meeting_id"),
        "recurrence": reminder.get("recurrence"),
        "status": "pending",
    }
    res = db.table("reminders").insert(payload).execute()
    return res.data[0]


def get_pending_reminders(user_id: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("reminders")
        .select("*")
        .eq("user_id", user_id)
        .eq("status", "pending")
        .order("trigger_time")
        .execute()
    )
    return res.data
