"""Natural-language date-range history summaries."""
from __future__ import annotations

import datetime as dt
import re

from app.ai.parser import resolve_datetime
from app.database import repository as repo

_HISTORY_RE = re.compile(
    r"\b(?:what|tell me|show me|give me)\b.*\b(?:i(?:'ve| have)?\s+)?(?:done|did|worked on|completed|logged)\b.*\b(?:since|from)\b\s+(.+?)(?:\s+(?:till|until|to)\s+(?:now|today|date)|\s*$)",
    re.I,
)


def is_history_question(text: str) -> bool:
    low = text.lower().strip()
    if not (re.search(r"\b(?:what|show|tell|give)\b", low) and re.search(r"\b(?:done|did|worked|completed|logged)\b", low)):
        return False
    return bool(re.search(r"\b(?:since|from)\b\s+\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)", low))


def _extract_start(text: str, now: dt.datetime) -> dt.date | None:
    m = re.search(
        r"\b(?:since|from)\b\s+(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)(?:\s+\d{4})?)",
        text,
        re.I,
    )
    if not m:
        return None
    resolved = resolve_datetime(m.group(1), now=now)
    return resolved.date() if resolved else None


def _format_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return dt.date.fromisoformat(value).strftime("%d %b %Y")
    except ValueError:
        return value


def build_history(user_id: str, start_date: dt.date, end_date: dt.date, tz) -> str:
    start = start_date.isoformat()
    end = end_date.isoformat()
    activities = repo.get_activity(user_id, start, end)
    sales = repo.get_sales_calls(user_id, start, end)
    meetings = repo.get_meetings_in_range(
        user_id,
        dt.datetime.combine(start_date, dt.time.min, tzinfo=tz).isoformat(),
        dt.datetime.combine(end_date, dt.time.max, tzinfo=tz).isoformat(),
    )

    lines = [f"Your work from {start_date.strftime('%d %b %Y')} to {end_date.strftime('%d %b %Y')}:", ""]

    if activities:
        lines.append(f"Work/activity: {len(activities)} entries")
        for row in activities[:25]:
            qty = row.get("quantity") or 1
            activity = row.get("activity_type") or "activity"
            platform = row.get("account_name") or row.get("platform")
            note = row.get("notes")
            detail = f" — {note}" if note else ""
            prefix = f"{qty} " if qty != 1 else ""
            lines.append(f"• {_format_date(row.get('date'))}: {prefix}{activity} on {platform}{detail}")
        if len(activities) > 25:
            lines.append(f"• +{len(activities) - 25} more activity entries")
    else:
        lines.append("Work/activity: none recorded")

    lines.append("")
    if sales:
        lines.append(f"Sales: {len(sales)} calls")
        for row in sales[:25]:
            lead = row.get("lead_name") or "Unnamed lead"
            outcome = row.get("outcome") or "logged"
            call_type = row.get("call_type")
            suffix = f", {call_type}" if call_type else ""
            lines.append(f"• {_format_date(row.get('date'))}: {lead} — {outcome}{suffix}")
        if len(sales) > 25:
            lines.append(f"• +{len(sales) - 25} more sales entries")
    else:
        lines.append("Sales: none recorded")

    lines.append("")
    if meetings:
        lines.append(f"Meetings: {len(meetings)}")
        for row in meetings[:25]:
            value = dt.datetime.fromisoformat(str(row["start_time"]).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = tz.localize(value)
            value = value.astimezone(tz)
            lines.append(f"• {value.strftime('%d %b %Y, %I:%M %p').lstrip('0')}: {row.get('title') or 'Meeting'}")
        if len(meetings) > 25:
            lines.append(f"• +{len(meetings) - 25} more meetings")
    else:
        lines.append("Meetings: none recorded")

    return "\n".join(lines)


async def handle_history_question(message, user, tz, text: str) -> bool:
    if not is_history_question(text):
        return False
    now = dt.datetime.now(tz)
    start_date = _extract_start(text, now)
    if not start_date:
        await message.reply_text("I couldn't understand the start date. Try: '15 August se abhi tak kya kya kiya hai'.")
        return True
    if start_date > now.date():
        await message.reply_text("The start date can't be in the future.")
        return True
    await message.reply_text(build_history(user["id"], start_date, now.date(), tz))
    return True
