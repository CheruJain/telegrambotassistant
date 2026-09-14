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
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bje)\b",
    re.IGNORECASE,
)
_BARE_HOUR = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*o['’]?clock\b", re.IGNORECASE)


def _model_name() -> str:
    """Normalize deployment-provided model names for google-generativeai."""
    name = (settings.AI_MODEL or "gemini-2.5-flash").strip()
    if name.startswith("models/"):
        name = name[len("models/"):]
    return name


def call_ai_json(system_prompt: str, user_content: str, max_tokens: int = 4096) -> dict:
    """Single Gemini AI call that must return valid JSON."""
    model = genai.GenerativeModel(model_name=_model_name(), system_instruction=system_prompt)
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
    now: Optional[dt.datetime] = None,
) -> Optional[dt.datetime]:
    if not phrase:
        return None
    tz = pytz.timezone(tz_name)
    now = now or dt.datetime.now(tz)
    value = str(phrase).strip()
    lower = value.lower()

    if lower in {"today", "aaj"}:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if lower in {"tomorrow", "kal"}:
        return (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    for weekday, weekday_rel in _WEEKDAYS.items():
        if weekday in lower:
            return now + relativedelta(weekday=weekday_rel(+1))

    match = _HOUR_WORD.search(lower) or _BARE_HOUR.search(lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) if len(match.groups()) >= 3 else None) or ""
        if meridiem.lower() == "pm" and hour < 12:
            hour += 12
        elif meridiem.lower() == "am" and hour == 12:
            hour = 0
        base = now
        if "tomorrow" in lower or "kal" in lower:
            base += dt.timedelta(days=1)
        candidate = tz.localize(dt.datetime.combine(base.date(), dt.time(hour, minute)))
        if candidate <= now and not ("tomorrow" in lower or "kal" in lower):
            candidate += dt.timedelta(days=1)
        return candidate

    try:
        parsed = dateparser.parse(value, default=now.replace(tzinfo=None))
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return tz.localize(parsed)
        return parsed.astimezone(tz)
    except (ValueError, OverflowError):
        return None


def next_recurrence_time(trigger: dt.datetime, recurrence: str) -> dt.datetime:
    value = str(recurrence or "").lower().strip()
    if value in {"daily", "day", "every day"}:
        return trigger + dt.timedelta(days=1)
    if value in {"weekly", "week", "every week"}:
        return trigger + dt.timedelta(weeks=1)
    if value in {"monthly", "month", "every month"}:
        return trigger + relativedelta(months=1)
    return trigger
