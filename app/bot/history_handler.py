"""Natural-language date-range history summaries."""
from __future__ import annotations

import datetime as dt
import html
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
    m = re.search(r"\b(?:since|from)\b\s+(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december)(?:\s+\d{4})?)", text, re.I)
    if not m:
        return None
    resolved = resolve_datetime(m.group(1), now=now)
    return resolved.date() if resolved else None


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


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

    deals_closed = sum(1 for row in sales if str(row.get("outcome") or "").lower() in {"won", "closed", "deal closed"})
    tasks_completed = sum(
        1 for row in activities
        if str(row.get("activity_type") or "").lower() in {"task_completed", "task completed", "completed", "task"}
        and "complete" in str(row.get("notes") or "").lower()
    )

    lines = [
        f"<b>📈  ACTIVITY REPORT ({start_date.strftime('%d %b')} – {end_date.strftime('%d %b')})</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📞  Sales Calls: {len(sales)}",
        f"🤝  Meetings Held: {len(meetings)}",
        f"🎯  Deals Closed: {deals_closed}",
        f"📝  Tasks Completed: {tasks_completed}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not activities and not sales and not meetings:
        lines.insert(-1, "No activity recorded for this period")
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
    await message.reply_text(build_history(user["id"], start_date, now.date(), tz), parse_mode="HTML")
    return True
