"""Natural-language updates for existing booked sales calls."""
from __future__ import annotations

import calendar
import datetime as dt
import re

from telegram import Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database.client import get_client
from app.ai.parser import resolve_datetime

_PHONE_RE = re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")
_DATE_RE = re.compile(r"\b(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:\s*(\d{4}))?\b", re.I)
_TIME_RE = re.compile(r"\b(\d{1,2})(?::|;)(\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm)\b", re.I)


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:update|change|edit)\s+([A-Za-z][A-Za-z .'-]{0,50}?)(?=\s+(?:ka|ki|ke)\s+|\s+(?:phone|number|call|status|type|time|date)\b|\s+to\b|$)",
        r"([A-Za-z][A-Za-z .'-]{0,50}?)\s+(?:ka|ki|ke)\s+(?:number|phone|call|status|type|time|date)\b",
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
        or re.search(r"\b(?:ka|ki|ke)\s+(?:number|phone|call|status|type|time|date)\b", low)
    ) and bool(
        _PHONE_RE.search(text)
        or re.search(r"\b(?:discovery|follow\s*up|demo|closing|strategy|completed|complete|interested|won|lost|time|date)\b", low)
        or _TIME_RE.search(text)
        or _DATE_RE.search(text)
    )


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
    result = db.table("users").select("id").eq("telegram_user_id", telegram_user_id).limit(1).execute()
    if not result.data:
        raise RuntimeError("Authorized user does not exist")
    return result.data[0]["id"]


def _find_booked_calls(db, user_id: str, name: str) -> list[dict]:
    result = db.table("sales_calls").select("*").eq("user_id", user_id).eq("outcome", "Booked").order("booked_date").order("booked_time").execute()
    return [row for row in (result.data or []) if name.lower() in (row.get("lead_name") or "").lower()]


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


def _extract_updates(text: str) -> dict:
    updates = {}
    phone = _PHONE_RE.search(text)
    if phone:
        updates["phone_number"] = phone.group(0).replace(" ", "").replace("-", "")

    low = text.lower()
    call_types = {"discovery": "Discovery", "follow up": "Follow Up", "follow-up": "Follow Up", "demo": "Demo", "closing": "Closing", "strategy": "Strategy"}
    for key, value in call_types.items():
        if key in low:
            updates["call_type"] = value
            break

    status_map = {"not interested": "Not Interested", "rescheduled": "Rescheduled", "interested": "Interested", "booked": "Booked", "won": "Won", "lost": "Lost", "completed": "Completed", "complete": "Completed"}
    for key, value in status_map.items():
        if key in low:
            updates["outcome"] = value
            break

    now = dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    date_value = _date_from_text(text, now.date())
    if date_value:
        updates["booked_date"] = date_value.isoformat()

    time_match = _TIME_RE.search(text)
    if time_match:
        if time_match.group(1):
            hour, minute, meridiem = int(time_match.group(1)), int(time_match.group(2)), (time_match.group(3) or "").lower()
        else:
            hour, minute, meridiem = int(time_match.group(4)), 0, (time_match.group(5) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        elif not meridiem and hour <= 7:
            hour += 12
        updates["booked_time"] = f"{hour:02d}:{minute:02d}:00"

    return updates


async def _apply_update(message, target: dict, updates: dict) -> bool:
    if not updates:
        await message.reply_text("I couldn't find a detail to update.")
        return False
    db = get_client()
    result = db.table("sales_calls").update(updates).eq("id", target["id"]).execute()
    if not result.data:
        await message.reply_text("I couldn't update that booked call. Please try again.")
        return False

    # Keep the calendar meeting in sync when its sales-call time/date changes.
    if "booked_date" in updates or "booked_time" in updates:
        meeting_query = db.table("meetings").select("id,start_time,title").eq("user_id", target["user_id"]).eq("status", "scheduled").ilike("person", target["lead_name"])
        meetings = meeting_query.execute().data or []
        for meeting in meetings:
            current = dt.datetime.fromisoformat(str(meeting["start_time"]).replace("Z", "+00:00"))
            new_date = dt.date.fromisoformat(updates.get("booked_date", current.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()))
            new_time = updates.get("booked_time", current.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%H:%M:%S"))
            naive = dt.datetime.combine(new_date, dt.time.fromisoformat(new_time[:8]))
            ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
            new_start = naive.replace(tzinfo=ist).isoformat()
            db.table("meetings").update({"start_time": new_start, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}).eq("id", meeting["id"]).execute()
            db.table("reminders").update({"status": "cancelled"}).eq("related_meeting_id", meeting["id"]).eq("status", "pending").execute()
            reminder_time = naive.replace(tzinfo=ist) - dt.timedelta(minutes=60)
            if reminder_time > dt.datetime.now(ist):
                db.table("reminders").insert({"user_id": target["user_id"], "reminder_text": f"Confirm attendance: {target['lead_name']} — sales call at {reminder_time.astimezone(ist).strftime('%I:%M %p').lstrip('0')}", "trigger_time": reminder_time.isoformat(), "related_meeting_id": meeting["id"], "recurrence": "none", "status": "pending"}).execute()

    changed = []
    if "phone_number" in updates: changed.append("phone number")
    if "call_type" in updates: changed.append("call type")
    if "outcome" in updates: changed.append("status")
    if "booked_date" in updates: changed.append("date")
    if "booked_time" in updates: changed.append("time")
    await message.reply_text(f"Updated {target['lead_name']}: {', '.join(changed)}.")
    return True


async def _handle_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending = context.user_data.get("pending_sales_selection")
    if not pending:
        return False
    text = (update.message.text or "").strip()
    if not re.fullmatch(r"\d+", text):
        return False
    index = int(text) - 1
    if index < 0 or index >= len(pending["ids"]):
        await update.message.reply_text("Please choose one of the listed numbers.")
        return True
    db = get_client()
    user_id = _get_user_id(db, update.effective_user.id)
    rows = _find_booked_calls(db, user_id, pending["name"])
    by_id = {str(row["id"]): row for row in rows}
    target = by_id.get(str(pending["ids"][index]))
    if not target:
        context.user_data.pop("pending_sales_selection", None)
        await update.message.reply_text("That booking is no longer available. Please send the update again.")
        return True
    context.user_data.pop("pending_sales_selection", None)
    await _apply_update(update.message, target, pending["updates"])
    return True


async def handle_sales_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_selection(update, context)


async def handle_possible_sales_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message.text or "").strip()
    if not text or update.effective_user.id != settings.TELEGRAM_USER_ID:
        return
    if await _handle_selection(update, context):
        return
    name = _extract_name(text)
    if not name:
        return
    if _is_cancel_message(text):
        db = get_client()
        user_id = _get_user_id(db, update.effective_user.id)
        matches = _find_booked_calls(db, user_id, name)
        if not matches:
            return
        if len(matches) > 1:
            context.user_data["pending_sales_selection"] = {"name": name, "ids": [str(r["id"]) for r in matches], "updates": {"outcome": "Cancelled"}}
            await message.reply_text("I found multiple booked calls for that name. Please reply with the number of the one to cancel.")
            return
        await _apply_update(message, matches[0], {"outcome": "Cancelled"})
        return
    if not _is_update_message(text):
        return

    db = get_client()
    user_id = _get_user_id(db, update.effective_user.id)
    matches = _find_booked_calls(db, user_id, name)
    if not matches:
        await message.reply_text(f"I couldn't find a booked call for {name}.")
        return

    updates = _extract_updates(text)
    phone_value = updates.get("phone_number")
    if phone_value:
        phone_matches = [r for r in matches if (r.get("phone_number") or "").replace(" ", "").replace("-", "") == phone_value]
        if phone_matches:
            matches = phone_matches

    if len(matches) > 1:
        context.user_data["pending_sales_selection"] = {"name": name, "ids": [str(r["id"]) for r in matches], "updates": updates}
        lines = [f"I found {len(matches)} booked calls for {name}. Which one do you mean?"]
        for i, row in enumerate(matches, 1):
            lines.append(f"{i}. {_format_match(row)}")
        await message.reply_text("\n".join(lines))
        return
    await _apply_update(message, matches[0], updates)
