"""
Natural-language parsing layer.

Cost-control strategy (see spec section 28):
- The AI call happens exactly once per incoming message (never chained),
  and only when the message isn't a recognized slash-command.
- Date/time resolution is done deterministically with Python code.
- The AI is only asked to classify intent and extract raw entities.
"""
from __future__ import annotations

import json
import re
import datetime as dt
from typing import Any, Optional

import google.generativeai as genai
import pytz
from dateutil import parser as dateparser
from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU

from app.config.settings import settings
from app.ai.prompts import INTENT_SYSTEM_PROMPT


genai.configure(api_key=settings.AI_API_KEY)


_WEEKDAYS = {
    "monday": MO, "mon": MO, "somvar": MO,
    "tuesday": TU, "tue": TU, "mangalvar": TU,
    "wednesday": WE, "wed": WE, "budhvar": WE,
    "thursday": TH, "thu": TH, "guruvar": TH,
    "friday": FR, "fri": FR, "shukravar": FR,
    "saturday": SA, "sat": SA, "shanivar": SA,
    "sunday": SU, "sun": SU, "raviwar": SU, "ravivar": SU,
}

_HOUR_WORD = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)\b",
    re.IGNORECASE,
)
_BARE_HOUR = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*o['’]?clock\b", re.IGNORECASE)


def call_ai_json(system_prompt: str, user_content: str, max_tokens: int = 4096) -> dict:
    """Single Gemini AI call that must return valid JSON."""
    model = genai.GenerativeModel(model_name=settings.AI_MODEL, system_instruction=system_prompt)
    response = model.generate_content(
        user_content,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    raw = response.text.strip()
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"AI did not return valid JSON: {raw[:500]}")


def parse_message(text: str) -> dict:
    return call_ai_json(INTENT_SYSTEM_PROMPT, text)


def resolve_datetime(
    phrase: Optional[str],
    tz_name: str = "Asia/Kolkata",
    default_hour: int = 9,
    now: Optional[dt.datetime] = None,
) -> Optional[dt.datetime]:
    """Resolve a Hindi/English/Hinglish date-time phrase."""
    if not phrase or not phrase.strip():
        return None

    tz = pytz.timezone(tz_name)
    now = now or dt.datetime.now(tz)
    if now.tzinfo is None:
        now = tz.localize(now)

    # Telegram users may type 5;30. Treat a semicolon between digits as a colon.
    p = re.sub(r"(?<=\d)\s*;\s*(?=\d)", ":", phrase.strip().lower())
    base_date = now.date()
    time_part: Optional[dt.time] = None

    if "parso" in p or ("din baad" in p and "2" in p):
        base_date = now.date() + dt.timedelta(days=2)
    elif "aaj" in p or "today" in p:
        base_date = now.date()
    elif "kal" in p:
        base_date = now.date() + dt.timedelta(days=1)
    elif "tomorrow" in p:
        base_date = now.date() + dt.timedelta(days=1)
    elif "yesterday" in p:
        base_date = now.date() - dt.timedelta(days=1)

    m = re.search(r"(\d+)\s*(minute|min|hour|hr|ghante|ghanta)s?\s*(mein|me|later|from now)?", p)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        delta = dt.timedelta(minutes=qty) if unit.startswith("min") else dt.timedelta(hours=qty)
        return now + delta

    for word, wk in _WEEKDAYS.items():
        if re.search(rf"\b{word}\b", p):
            candidate = now + relativedelta(weekday=wk(0))
            base_date = candidate.date()
            break

    hm = _HOUR_WORD.search(p) or _BARE_HOUR.search(p)
    if hm and hm.group(1):
        hour = int(hm.group(1))
        minute = int(hm.group(2)) if hm.group(2) else 0
        meridiem = (hm.group(3) or "").lower() if hm.lastindex and hm.lastindex >= 3 else ""
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif meridiem in ("", "baje") and hour <= 7:
            hour += 12
        time_part = dt.time(hour=hour, minute=minute)

    if time_part is not None and not any(word in p for word in ("kal", "tomorrow", "yesterday", "aaj", "today", "parso")):
        try:
            date_phrase = _HOUR_WORD.sub(" ", p)
            date_phrase = _BARE_HOUR.sub(" ", date_phrase)
            parsed = dateparser.parse(date_phrase, fuzzy=True, default=now.replace(tzinfo=None))
            if parsed is not None and re.search(r"\b\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)", p):
                base_date = parsed.date()
        except (ValueError, OverflowError, TypeError):
            pass

    if time_part is None:
        try:
            parsed = dateparser.parse(phrase, fuzzy=True, default=now.replace(tzinfo=None))
            if parsed is not None:
                candidate = tz.localize(parsed) if parsed.tzinfo is None else parsed
                return candidate
        except (ValueError, OverflowError, TypeError):
            time_part = dt.time(hour=default_hour, minute=0)

    return tz.localize(dt.datetime.combine(base_date, time_part))


def next_recurrence_time(current_trigger: dt.datetime, recurrence: str) -> dt.datetime:
    if recurrence == "daily":
        return current_trigger + dt.timedelta(days=1)
    if recurrence and recurrence.startswith("weekly:"):
        return current_trigger + dt.timedelta(days=7)
    return current_trigger + dt.timedelta(days=1)
