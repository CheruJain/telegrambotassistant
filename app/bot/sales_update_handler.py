"""Natural-language updates for existing booked sales calls."""
from __future__ import annotations

import re
import datetime as dt

from telegram import Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database.client import get_client


_PHONE_RE = re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b")


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:update|change|edit)\s+([A-Za-z][A-Za-z .'-]{0,50}?)(?:\s+(?:ka|ki|ke)\s+|\s+phone|\s+number|\s+call|\s+status|\s+type|$)",
        r"([A-Za-z][A-Za-z .'-]{0,50}?)\s+(?:ka|ki|ke)\s+(?:number|phone|call|status|type)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            name = m.group(1).strip(" ,.-")
            name = re.sub(r"^(?:update|change|edit)\s+", "", name, flags=re.I).strip()
            if name:
                return name
    return None


def _is_update_message(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(r"\b(update|change|edit)\b", low)
        or re.search(r"\b(?:ka|ki|ke)\s+(?:number|phone|call|status|type)\b", low)
    ) and bool(_PHONE_RE.search(text) or re.search(r"\b(?:discovery|follow\s*up|demo|closing)\b", low))


async def handle_possible_sales_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message.text or "").strip()
    if not text or not _is_update_message(text):
        return

    if update.effective_user.id != settings.TELEGRAM_USER_ID:
        return

    name = _extract_name(text)
    if not name:
        return

    db = get_client()
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=365)
    result = (
        db.table("sales_calls")
        .select("*")
        .eq("user_id", _get_user_id(db, update.effective_user.id))
        .eq("outcome", "Booked")
        .gte("booked_date", now.date().isoformat())
        .lte("booked_date", end.date().isoformat())
        .order("booked_date")
        .order("booked_time")
        .execute()
    )

    matches = [
        row for row in (result.data or [])
        if name.lower() in (row.get("lead_name") or "").lower()
    ]
    if not matches:
        await message.reply_text(f"I couldn't find a booked call for {name}.")
        return

    target = matches[0]
    updates = {}
    phone = _PHONE_RE.search(text)
    if phone:
        updates["phone_number"] = phone.group(0).replace(" ", "").replace("-", "")

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
