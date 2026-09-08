"""Natural-language updates for existing booked sales calls."""
from __future__ import annotations

import calendar
import datetime as dt
import re

from telegram import Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database.client import get_client


_PHONE_RE = re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")
_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:\s*(\d{4}))?\b",
    re.I,
)


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:update|change|edit)\s+([A-Za-z][A-Za-z .'-]{0,50}?)(?:\s+(?:ka|ki|ke)\s+|\s+phone|\s+number|\s+call|\s+status|\s+type|$)",
        r"([A-Za-z][A-Za-z .'-]{0,50}?)\s+(?:ka|ki|ke)\s+(?:number|phone|call|status|type)",
        r"(?:cancel|cancelled|canceled)\s+(?:the\s+)?([A-Za-z][A-Za-z .'-]{0,50}?)(?:\s+(?:ki|ka|ke)\s+meeting|\s+meeting|\s+call|\s*$)",
        r"([A-Za-z][A-Za-z .'-]{0,50}?)\s+(?:ki|ka|ke)\s+meeting.*\b(?:cancel|cancelled|canceled)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            name = m.group(1).strip(" ,.-")
            name = re.sub(r"^(?:update|change|edit)\s+", "", name, flags=re.I).strip()
            if name:
                return name
    return None


def _is_cancel_message(text: str) -> bool:
    low = text.lower()
    return bool(re.search(r"\b(cancel|cancelled|canceled)\b", low) and re.search(r"\b(meeting|call)\b", low))


def _is_update_message(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(r"\b(update|change|edit)\b", low)
        or re.search(r"\b(?:ka|ki|ke)\s+(?:number|phone|call|status|type)\b", low)
    ) and bool(_PHONE_RE.search(text) or re.search(r"\b(?:discovery|follow\s*up|demo|closing)\b", low))


def _date_from_text(text: str, today: dt.date) -> dt.date | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2).lower()
    if month_name == "sept":
        month_name = "sep"
    month = list(calendar.month_abbr).index(month_name[:3].title())
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _get_user_id(db, telegram_user_id: int) -> str:
    result = (
        db.table("users")
        .select("id")
        .eq("telegram_user_id", telegram_user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Authorized user does not exist")
    return result.data[0]["id"]


def _find_booked_calls(db, user_id: str, name: str) -> list[dict]:
    result = (
        db.table("sales_calls")
        .select("*")
        .eq("user_id", user_id)
        .eq("outcome", "Booked")
        .order("booked_date")
        .order("booked_time")
        .execute()
    )
    return [
        row for row in (result.data or [])
        if name.lower() in (row.get("lead_name") or "").lower()
    ]


def _format_match(row: dict) -> str:
    date = row.get("booked_date") or "date unknown"
    time = str(row.get("booked_time") or "").strip()
    if len(time) >= 5:
        try:
            time = dt.datetime.strptime(time[:8], "%H:%M:%S").strftime("%I:%M %p").lstrip("0")
        except ValueError:
            try:
                time = dt.datetime.strptime(time[:5], "%H:%M").strftime("%I:%M %p").lstrip("0")
            except ValueError:
                pass
    call_type = row.get("call_type") or "Call"
    phone = row.get("phone_number")
    extra = f" — {phone}" if phone else ""
    return f"{date} {time} — {call_type}{extra}"


async def _handle_cancel(update: Update, name: str) -> bool:
    message = update.message
    db = get_client()
    user_id = _get_user_id(db, update.effective_user.id)
    matches = _find_booked_calls(db, user_id, name)
    if not matches:
        return False

    target_date = _date_from_text((message.text or ""), dt.datetime.now().date())
    if target_date:
        dated = [r for r in matches if r.get("booked_date") == target_date.isoformat()]
        if dated:
            matches = dated

    if len(matches) > 1:
        lines = [f"I found {len(matches)} booked calls for {name}. Which one should I cancel?"]
        for i, row in enumerate(matches, 1):
            lines.append(f"{i}. {_format_match(row)}")
        await message.reply_text("\n".join(lines))
        return True

    target = matches[0]
    db.table("sales_calls").update({"outcome": "Cancelled"}).eq("id", target["id"]).execute()

    # Cancel any reminder tied to the matching meeting/call so it cannot fire later.
    meeting_result = (
        db.table("meetings")
        .select("id")
        .eq("user_id", user_id)
        .neq("status", "cancelled")
        .ilike("person", target.get("lead_name") or name)
        .execute()
    )
    for meeting in meeting_result.data or []:
        db.table("reminders").update({"status": "cancelled"}).eq(
            "related_meeting_id", meeting["id"]
        ).eq("status", "pending").execute()
        db.table("meetings").update({"status": "cancelled"}).eq("id", meeting["id"]).execute()

    await message.reply_text(f"Cancelled: {target.get('lead_name') or name} — {_format_match(target)}.")
    return True


async def handle_possible_sales_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message.text or "").strip()
    if not text or update.effective_user.id != settings.TELEGRAM_USER_ID:
        return

    name = _extract_name(text)
    if not name:
        return

    if _is_cancel_message(text):
        await _handle_cancel(update, name)
        return

    if not _is_update_message(text):
        return

    db = get_client()
    user_id = _get_user_id(db, update.effective_user.id)
    matches = _find_booked_calls(db, user_id, name)
    if not matches:
        await message.reply_text(f"I couldn't find a booked call for {name}.")
        return

    # Never guess when the same lead name exists more than once.
    phone = _PHONE_RE.search(text)
    phone_value = phone.group(0).replace(" ", "").replace("-", "") if phone else None
    if phone_value:
        phone_matches = [r for r in matches if (r.get("phone_number") or "").replace(" ", "").replace("-", "") == phone_value]
        if phone_matches:
            matches = phone_matches

    target_date = _date_from_text(text, dt.datetime.now().date())
    if target_date:
        dated = [r for r in matches if r.get("booked_date") == target_date.isoformat()]
        if dated:
            matches = dated

    if len(matches) > 1:
        lines = [f"I found {len(matches)} booked calls for {name}. Which one do you mean?"]
        for i, row in enumerate(matches, 1):
            lines.append(f"{i}. {_format_match(row)}")
        await message.reply_text("\n".join(lines))
        return

    target = matches[0]
    updates = {}
    if phone_value:
        updates["phone_number"] = phone_value

    low = text.lower()
    call_types = {
        "discovery": "Discovery",
        "follow up": "Follow Up",
        "follow-up": "Follow Up",
        "demo": "Demo",
        "closing": "Closing",
    }
    for key, value in call_types.items():
        if key in low:
            updates["call_type"] = value
            break

    status_map = {
        "booked": "Booked",
        "rescheduled": "Rescheduled",
        "interested": "Interested",
        "not interested": "Not Interested",
        "won": "Won",
        "lost": "Lost",
    }
    for key, value in status_map.items():
        if key in low:
            updates["outcome"] = value
            break

    if not updates:
        return

    updated = db.table("sales_calls").update(updates).eq("id", target["id"]).execute()
    if not updated.data:
        await message.reply_text("I couldn't update that booked call. Please try again.")
        return

    changed = []
    if "phone_number" in updates:
        changed.append("phone number")
    if "call_type" in updates:
        changed.append("call type")
    if "outcome" in updates:
        changed.append("status")
    await message.reply_text(f"Updated {target['lead_name']}: {', '.join(changed)}.")
