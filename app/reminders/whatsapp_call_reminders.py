"""Create the three WhatsApp reminders for booked sales calls."""
from __future__ import annotations

import datetime as dt
import json

from app.database.client import get_client

_MARKER = "__WHATSAPP_TEMPLATE__"


def _template_name(stage: str) -> str:
    return {
        "day_before": "call_reminder_day_before",
        "day_of": "call_reminder_day_of",
        "one_hour": "call_reminder_one_hour",
    }[stage]


def _template_parameters(call: dict, stage: str, call_start: dt.datetime) -> list[str]:
    name = str(call.get("lead_name") or "there")
    date = call_start.strftime("%d %B")
    time = call_start.strftime("%I:%M %p").lstrip("0")

    if stage == "one_hour":
        return [name, time]
    return [name, date, time]


def _payload(call: dict, stage: str, call_start: dt.datetime) -> str:
    return _MARKER + json.dumps(
        {
            "recipient": str(call["phone_number"]),
            "template": _template_name(stage),
            "parameters": _template_parameters(call, stage, call_start),
            "stage": stage,
            "call_id": str(call["id"]),
        },
        separators=(",", ":"),
    )


def ensure_whatsapp_call_reminders(user_id: str, now: dt.datetime | None = None) -> int:
    """Ensure day-before, day-of and one-hour WhatsApp reminders exist.

    The reminders are stored in the existing reminders table so no schema
    change is required. The scheduler recognizes the internal marker and
    sends these through WhatsApp instead of Telegram.
    """
    db = get_client()
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    now = (now or dt.datetime.now(ist)).astimezone(ist)
    calls = (
        db.table("sales_calls")
        .select("id,lead_name,phone_number,booked_date,booked_time,outcome")
        .eq("user_id", user_id)
        .eq("outcome", "Booked")
        .execute()
        .data
        or []
    )
    if not calls:
        return 0

    existing = (
        db.table("reminders")
        .select("reminder_text,status")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    existing_texts = {str(row.get("reminder_text") or "") for row in existing}
    created = 0

    for call in calls:
        if not call.get("id") or not call.get("phone_number") or not call.get("booked_date") or not call.get("booked_time"):
            continue
        try:
            call_start = dt.datetime.combine(
                dt.date.fromisoformat(str(call["booked_date"])),
                dt.time.fromisoformat(str(call["booked_time"])[:8]),
                tzinfo=ist,
            )
        except ValueError:
            continue

        stages = (
            ("day_before", call_start - dt.timedelta(days=1)),
            ("day_of", call_start.replace(hour=9, minute=0, second=0, microsecond=0)),
            ("one_hour", call_start - dt.timedelta(hours=1)),
        )

        for stage, trigger in stages:
            if trigger <= now:
                continue
            marker_key = f'"stage":"{stage}"'
            call_key = f'"call_id":"{call["id"]}"'
            if any(_MARKER in text and marker_key in text and call_key in text for text in existing_texts):
                continue
            text = _payload(call, stage, call_start)
            db.table("reminders").insert(
                {
                    "user_id": user_id,
                    "reminder_text": text,
                    "trigger_time": trigger.isoformat(),
                    "status": "pending",
                }
            ).execute()
            existing_texts.add(text)
            created += 1

    return created
