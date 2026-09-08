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
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?",
    re.IGNORECASE,
)


def call_ai_json(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 4096,
) -> dict:
    """Single Gemini AI call that must return valid JSON.

    The parser returns a fairly large structured object. Keep enough output
    budget for Gemini to finish the JSON instead of truncating it mid-field.
    """

    model = genai.GenerativeModel(
        model_name=settings.AI_MODEL,
        system_instruction=system_prompt,
    )

    response = model.generate_content(
        user_content,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )

    raw = response.text.strip()

    # Be tolerant if the model/client wraps otherwise-valid JSON in a code fence
    # or adds a small amount of text despite response_mime_type=application/json.
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # If there is surrounding text, try the first complete JSON object.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass

        raise ValueError(
            f"AI did not return valid JSON: {raw[:500]}"
        )


def parse_message(text: str) -> dict:
    """Runs the message through the AI intent/entity extractor."""
    return call_ai_json(INTENT_SYSTEM_PROMPT, text)


# ------------------------------------------------------------------
# Deterministic date/time resolution
# ------------------------------------------------------------------

def resolve_datetime(
    phrase: Optional[str],
    tz_name: str = "Asia/Kolkata",
    default_hour: int = 9,
    now: Optional[dt.datetime] = None,
) -> Optional[dt.datetime]:
    """
    Resolves a Hindi/English/Hinglish date-time phrase into a
    timezone-aware datetime.
    """

    if not phrase or not phrase.strip():
        return None

    tz = pytz.timezone(tz_name)
    now = now or dt.datetime.now(tz)

    if now.tzinfo is None:
        now = tz.localize(now)

    p = phrase.strip().lower()

    base_date = now.date()
    time_part: Optional[dt.time] = None

    # --- relative day words ---

    if "parso" in p or ("din baad" in p and "2" in p):
        base_date = now.date() + dt.timedelta(days=2)

    elif "aaj" in p or "today" in p:
        base_date = now.date()

    elif "kal" in p:
        # "kal" is ambiguous in Hindi.
        # For scheduling, assume tomorrow.
        base_date = now.date() + dt.timedelta(days=1)

    elif "tomorrow" in p:
        base_date = now.date() + dt.timedelta(days=1)

    elif "yesterday" in p:
        base_date = now.date() - dt.timedelta(days=1)

    # --- "in N minutes/hours" ---

    m = re.search(
        r"(\d+)\s*(minute|min|hour|hr|ghante|ghanta)s?"
        r"\s*(mein|me|later|from now)?",
        p,
    )

    if m:
        qty = int(m.group(1))
        unit = m.group(2)

        if unit.startswith("min"):
            delta = dt.timedelta(minutes=qty)
        else:
            delta = dt.timedelta(hours=qty)

        return now + delta

    # --- weekday names ---

    for word, wk in _WEEKDAYS.items():

        if re.search(rf"\b{word}\b", p):

            candidate = now + relativedelta(weekday=wk(0))

            if candidate.date() == now.date() and time_part is None:
                pass

            base_date = candidate.date()
            break

    # --- explicit clock time ---

    hm = _HOUR_WORD.search(p)

    if hm and hm.group(1):

        hour = int(hm.group(1))
        minute = int(hm.group(2)) if hm.group(2) else 0

        meridiem = (hm.group(3) or "").lower()

        if meridiem == "pm" and hour < 12:
            hour += 12

        elif meridiem == "am" and hour == 12:
            hour = 0

        elif meridiem in ("", "baje") and hour <= 7:
            # Example: "4 baje" -> 4 PM
            hour += 12

        time_part = dt.time(
            hour=hour,
            minute=minute,
        )

    if time_part is None:

        # Last-resort generic date parser
        try:
            parsed = dateparser.parse(
                phrase,
                fuzzy=True,
                default=now.replace(tzinfo=None),
            )

            candidate = (
                tz.localize(parsed)
                if parsed.tzinfo is None
                else parsed
            )

            return candidate

        except (ValueError, OverflowError):
            time_part = dt.time(
                hour=default_hour,
                minute=0,
            )

    naive = dt.datetime.combine(
        base_date,
        time_part,
    )

    return tz.localize(naive)


def next_recurrence_time(
    current_trigger: dt.datetime,
    recurrence: str,
) -> dt.datetime:
    """Compute the next trigger time."""

    if recurrence == "daily":
        return current_trigger + dt.timedelta(days=1)

    if recurrence and recurrence.startswith("weekly:"):
        return current_trigger + dt.timedelta(days=7)

    return current_trigger + dt.timedelta(days=1)
