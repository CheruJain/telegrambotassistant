"""Deterministic handling for simple Hinglish call-reminder requests."""
from __future__ import annotations

import datetime as dt
import re

import pytz

from app.database import repository as repo

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _date_from_text(text: str, today: dt.date) -> dt.date | None:
    value = text.lower()
    if re.search(r"\b(?:kal|tomorrow)\b", value):
        return today + dt.timedelta(days=1)
    m = re.search(r"\b(\d{1,2})\s*(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", value)
    if m:
        month = _MONTHS[m.group(2)]
        year = today.year
        candidate = dt.date(year, month, int(m.group(1)))
        if candidate < today and month < today.month:
            candidate = dt.date(year + 1, month, int(m.group(1)))
        return candidate
    return None


def _time_from_text(text: str, tz, now: dt.datetime) -> dt.datetime | None:
    value = text.lower().replace("bje", "baje")
    m = re.search(r"\b(\d{1,2})(?::|;)(\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm|baje)\b", value)
    if not m:
        return None
    hour = int(m.group(1) or m.group(4))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or m.group(5) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem == "baje" and hour <= 7:
        hour += 12
    candidate = tz.localize(dt.datetime.combine(now.date(), dt.time(hour, minute)))
    if candidate <= now and not re.search(r"\b(?:kal|tomorrow|\d{1,2}\s*[a-z]+)\b", value):
        return candidate + dt.timedelta(days=1)
    return candidate


def _looks_like_call_request(text: str) -> bool:
    value = text.lower()
    return bool(
        re.search(r"\b(?:call|phone|baat)\b", value)
        and re.search(r"\b(?:karna|karni|krna|krni|dena|deni|call)\b", value)
        and not re.search(r"\b(?:update|change|edit|cancel|cancelled)\b", value)
    )


def _extract_person(text: str) -> str | None:
    value = re.sub(r"\s+", " ", text.strip())
    m = re.search(r"^(.+?)\s+(?:ko|se)\s+call\b", value, re.I)
    if m:
        return m.group(1).strip(" ,.-")
    m = re.search(r"^call\s+(.+?)(?:\s+ko)?\s+(?:karna|krna)\b", value, re.I)
    return m.group(1).strip(" ,.-") if m else None


async def handle_natural_call_reminder(message, user: dict, text: str) -> bool:
    if not _looks_like_call_request(text):
        return False

    tz = pytz.timezone(user.get("timezone") or "Asia/Kolkata")
    now = dt.datetime.now(tz)
    target_date = _date_from_text(text, now.date()) or now.date()

    # "12 sep wale jitne bhi" means: create one call reminder for every existing
    # meeting/call on that date, rather than one generic reminder for the sentence.
    if re.search(r"\b(?:jitne\s+bhi|sab|saare|sare|wale)\b", text.lower()):
        start = tz.localize(dt.datetime.combine(target_date, dt.time.min))
        end = tz.localize(dt.datetime.combine(target_date, dt.time.max))
        rows = repo.get_meetings_in_range(user["id"], start.isoformat(), end.isoformat())
        if not rows:
            await message.reply_text(f"12 Sep ke liye koi call/meeting nahi mili." if target_date.month == 9 and target_date.day == 12 else "Us date ke liye koi call/meeting nahi mili.")
            return True
        created = []
        for row in rows:
            title = row.get("title") or "Call"
            person = row.get("person")
            reminder_text = f"Call {person or title}"
            trigger = start + dt.timedelta(hours=9)
            repo.create_reminder(user["id"], reminder_text, trigger.isoformat(), related_meeting_id=row.get("id"))
            created.append(reminder_text)
        await message.reply_text("Reminders set:\n" + "\n".join(f"• {item}" for item in created))
        return True

    person = _extract_person(text)
    trigger = _time_from_text(text, tz, now)
    if not person or not trigger:
        return False

    repo.create_reminder(user["id"], f"Call {person}", trigger.isoformat())
    await message.reply_text(f"Reminder set for {trigger.strftime('%A, %d %b %I:%M %p').lstrip('0').replace(' 0', ' ')}: Call {person}.")
    return True
