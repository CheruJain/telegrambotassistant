"""Natural-language date-wise meeting/reminder lookup."""
from __future__ import annotations

import datetime as dt
import re

import pytz

from app.database import repository as repo
from app.database.client import get_client

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
    "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _date_from_text(text: str, today: dt.date) -> dt.date | None:
    value = text.lower()
    if re.search(r"\b(?:aaj|today)\b", value):
        return today
    if re.search(r"\b(?:kal|tomorrow)\b", value):
        return today + dt.timedelta(days=1)
    m = re.search(r"\b(\d{1,2})\s*(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", value)
    if not m:
        return None
    month = _MONTHS[m.group(2)]
    candidate = dt.date(today.year, month, int(m.group(1)))
    if candidate < today and month < today.month:
        candidate = dt.date(today.year + 1, month, int(m.group(1)))
    return candidate


def _looks_like_date_lookup(text: str) -> bool:
    value = text.lower()
    has_date = bool(re.search(r"\b(?:aaj|today|kal|tomorrow)\b", value) or re.search(r"\b\d{1,2}\s*(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", value))
    has_topic = bool(re.search(r"\b(?:meeting|meetings|reminder|reminders|call|calls|wale|waale|schedule|scheduled|details?|kaun)\b", value))
    return has_date and has_topic


def _local_dt(value: str, tz) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = pytz.utc.localize(parsed)
    return parsed.astimezone(tz)


def _phone_for_meeting(meeting: dict, booked_calls: list[dict], tz) -> str | None:
    person = (meeting.get("person") or "").strip().lower()
    start = meeting.get("start_time")
    if not start:
        return None
    try:
        start_dt = _local_dt(start, tz)
    except ValueError:
        return None
    target_date = start_dt.date().isoformat()
    target_time = start_dt.strftime("%H:%M")
    phones = []
    for call in booked_calls:
        lead = (call.get("lead_name") or "").strip().lower()
        if not lead or call.get("booked_date") != target_date:
            continue
        if str(call.get("booked_time") or "")[:5] != target_time:
            continue
        if person and (person in lead or lead in person):
            phone = str(call.get("phone_number") or "").strip()
            if phone and phone not in phones:
                phones.append(phone)
    return ", ".join(phones) if phones else None


def _get_reminders_in_range(user_id: str, start_iso: str, end_iso: str) -> list[dict]:
    return (get_client().table("reminders").select("*").eq("user_id", user_id).eq("status", "pending").gte("trigger_time", start_iso).lte("trigger_time", end_iso).order("trigger_time").execute().data)


async def handle_date_details(message, user: dict, text: str) -> bool:
    if not _looks_like_date_lookup(text):
        return False
    tz = pytz.timezone(user.get("timezone") or "Asia/Kolkata")
    today = dt.datetime.now(tz).date()
    target = _date_from_text(text, today)
    if not target:
        return False

    start_local = tz.localize(dt.datetime.combine(target, dt.time.min))
    end_local = tz.localize(dt.datetime.combine(target, dt.time.max))
    meetings = repo.get_meetings_in_range(user["id"], start_local.isoformat(), end_local.isoformat())
    reminders = _get_reminders_in_range(user["id"], start_local.isoformat(), end_local.isoformat())
    booked_calls = repo.get_booked_sales_calls_for_date(user["id"], target.isoformat())

    lines = [target.strftime("%d-%b-%Y"), "", "MEETINGS / CALLS"]
    if meetings:
        for meeting in meetings:
            start = _local_dt(meeting["start_time"], tz)
            time_label = start.strftime("%I:%M %p").lstrip("0")
            title = (meeting.get("title") or "Meeting").strip()
            person = (meeting.get("person") or "").strip()
            display = f"{title} with {person}" if person and person.lower() not in title.lower() else title
            lines.append(f"• {time_label} — {display}")
            phone = _phone_for_meeting(meeting, booked_calls, tz)
            if phone:
                lines.append(f"  Phone: {phone}")
    else:
        lines.append("• None")

    lines.extend(["", "REMINDERS"])
    if reminders:
        for reminder in reminders:
            trigger = _local_dt(reminder["trigger_time"], tz)
            lines.append(f"• {trigger.strftime('%I:%M %p').lstrip('0')} — {reminder.get('reminder_text') or 'Reminder'}")
    else:
        lines.append("• None")

    await message.reply_text("\n".join(lines))
    return True
