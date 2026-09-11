"""Social tracking: backlog import, daily performance check-ins and follower audits."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.ai.parser import parse_message, resolve_datetime
from app.database import repository as repo
from app.config.settings import settings

SOCIAL_ACCOUNTS = {
    "ig1": "Instagram 1",
    "ig2": "Instagram 2",
    "instagram1": "Instagram 1",
    "instagram2": "Instagram 2",
    "linkedin": "LinkedIn",
    "li": "LinkedIn",
}


def _tz(user: dict):
    return pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)


def _account(value: str | None) -> str | None:
    if not value:
        return None
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    return SOCIAL_ACCOUNTS.get(key)


def _number(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "")
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([km])?", text)
    if not m:
        return None
    number = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k": number *= 1_000
    elif suffix == "m": number *= 1_000_000
    return int(number)


def parse_follower_audit(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    patterns = [
        ("Instagram 1", r"(?:ig1|instagram\s*1|instagram1)\s*[:=-]\s*([\d.,]+\s*[km]?)"),
        ("Instagram 2", r"(?:ig2|instagram\s*2|instagram2)\s*[:=-]\s*([\d.,]+\s*[km]?)"),
        ("LinkedIn", r"(?:li|linkedin)\s*[:=-]\s*([\d.,]+\s*[km]?)"),
    ]
    for account, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = _number(match.group(1))
            if value is not None:
                result[account] = value
    return result


def parse_performance_update(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"reach": None, "views": None, "leads": None, "notes": text.strip()}
    for key, aliases in {
        "reach": r"reach|reached",
        "views": r"views?|impressions?|impressions",
        "leads": r"leads?|inquiries|enquiries",
    }.items():
        m = re.search(rf"(?:{aliases})\s*[:=-]?\s*([\d,.]+\s*[km]?)", text, re.I)
        if m:
            result[key] = _number(m.group(1))
    return result


def _platform_from_text(text: str) -> tuple[str | None, str | None]:
    value = text.lower()
    account = None
    if re.search(r"\b(?:ig1|instagram\s*1|instagram1)\b", value): account = "Instagram 1"
    elif re.search(r"\b(?:ig2|instagram\s*2|instagram2)\b", value): account = "Instagram 2"
    elif re.search(r"\b(?:linkedin|li)\b", value): account = "LinkedIn"
    if account:
        return ("Instagram" if account.startswith("Instagram") else "LinkedIn", account)
    if "instagram" in value or "insta" in value: return "Instagram", "Instagram 1"
    if "linkedin" in value: return "LinkedIn", "LinkedIn"
    return None, None


def _extract_date(text: str, tz) -> str:
    now = dt.datetime.now(tz)
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else now.year
        if year < 100: year += 2000
        try: return dt.date(year, month, day).isoformat()
        except ValueError: pass
    resolved = resolve_datetime(text, tz.zone, now=now)
    return resolved.date().isoformat() if resolved else now.date().isoformat()


def import_backlog(user_id: str, text: str, tz) -> int:
    """Parse each useful line/paragraph and insert content rows; return inserted count."""
    chunks = [x.strip() for x in re.split(r"\n\s*\n|\n", text) if x.strip()]
    inserted = 0
    for chunk in chunks:
        platform, account = _platform_from_text(chunk)
        try:
            parsed = parse_message(chunk)
        except Exception:
            parsed = {}
        items = parsed.get("content_items") or []
        if not items:
            items = [{}]
        for item in items:
            item_platform = item.get("platform") or platform
            item_account = item.get("account_name") or account
            if not item_platform and not item_account:
                continue
            item_platform = "Instagram" if str(item_platform).lower().startswith("insta") else ("LinkedIn" if str(item_platform).lower().startswith("link") else str(item_platform))
            content_type = item.get("content_type") or "social_update"
            title = item.get("title") or item.get("post_title") or chunk[:180]
            post_date = _extract_date(str(item.get("date") or chunk), tz)
            quantity = int(item.get("quantity") or 1)
            payload = {
                "platform": item_platform,
                "account_name": item_account,
                "content_type": content_type,
                "title": title,
                "post_date": post_date,
                "quantity": quantity,
                "notes": chunk[:1000],
            }
            try:
                repo.log_content(user_id, item_platform, content_type, **{k: v for k, v in payload.items() if k not in {"platform", "content_type"}})
                inserted += 1
            except Exception:
                # Some older content schemas do not expose title/quantity/notes; retry with core columns.
                repo.log_content(user_id, item_platform, content_type, post_date=post_date, account_name=item_account)
                inserted += 1
    return inserted


def store_follower_audit(user_id: str, values: dict[str, int], captured_at: dt.datetime) -> int:
    rows = 0
    for account, followers in values.items():
        platform = "Instagram" if account.startswith("Instagram") else "LinkedIn"
        repo.log_social_metric(user_id, platform=platform, account_name=account, followers=followers, metric_date=captured_at.date().isoformat(), notes="Weekly follower audit")
        rows += 1
    return rows


def store_performance_update(user_id: str, text: str, captured_at: dt.datetime) -> int:
    data = parse_performance_update(text)
    inserted = 0
    platform, account = _platform_from_text(text)
    targets = [(platform, account)] if platform else [("Instagram", "Instagram 1"), ("Instagram", "Instagram 2"), ("LinkedIn", "LinkedIn")]
    for target_platform, target_account in targets:
        repo.log_social_metric(user_id, platform=target_platform, account_name=target_account, reach=data["reach"], views=data["views"], leads=data["leads"], metric_date=captured_at.date().isoformat(), notes=data["notes"][:1000])
        inserted += 1
    return inserted


def checkin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📝 Send Quick Update", callback_data="social_checkin:quick"), InlineKeyboardButton("⏭ Nothing Today", callback_data="social_checkin:none")]])


def follower_prompt() -> str:
    return ("📈  <b>WEEKLY AUDIT • FOLLOWER COUNTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Naye week ke liye current follower numbers update kar do:\n\n"
            "1. Instagram Account 1:\n"
            "2. Instagram Account 2:\n"
            "3. LinkedIn:\n\n"
            "Example: <code>ig1: 12.4k, ig2: 4.5k, li: 3200</code>\n"
            "━━━━━━━━━━━━━━━━━━━━")


async def backlog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)
    context.user_data["social_state"] = "backlog"
    await update.message.reply_text("📥  <b>PAST SOCIAL MEDIA BACKLOG</b>\n━━━━━━━━━━━━━━━━━━━━\nPuraane jo bhi updates, posts ya activities log karni hain, sab ek saath neeche text mein bhej do.\n\n(Format matter nahi karta, main parse karke Supabase mein record kar doonga)\n━━━━━━━━━━━━━━━━━━━━", parse_mode="HTML")


async def handle_social_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get("social_state")
    if not state or not update.message or not update.message.text:
        return False
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)
    tz = _tz(user)
    text = update.message.text.strip()
    if state == "backlog":
        context.user_data.pop("social_state", None)
        count = import_backlog(user["id"], text, tz)
        await update.message.reply_text(f"Social backlog imported: {count} records saved.")
        return True
    if state == "checkin":
        context.user_data.pop("social_state", None)
        count = store_performance_update(user["id"], text, dt.datetime.now(tz))
        await update.message.reply_text(f"24-hour social update saved: {count} metric entries.")
        return True
    if state == "followers":
        values = parse_follower_audit(text)
        if len(values) < 3:
            await update.message.reply_text("Please send all three values, e.g. ig1: 12.4k, ig2: 4.5k, li: 3200")
            return True
        context.user_data.pop("social_state", None)
        count = store_follower_audit(user["id"], values, dt.datetime.now(tz))
        await update.message.reply_text(f"Weekly follower audit saved: {count}/3 accounts updated.")
        return True
    return False


async def handle_social_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    data = query.data or ""
    if data == "social_checkin:quick":
        await query.answer()
        context.user_data["social_state"] = "checkin"
        await query.message.reply_text("Send the 24-hour performance numbers and any important internal note. Example: reach 12k, views 8k, leads 3 — LinkedIn performed better than Instagram.")
        return True
    if data == "social_checkin:none":
        await query.answer("No update recorded.")
        context.user_data.pop("social_state", None)
        return True
    return False


async def send_daily_checkin(bot, chat_id: int):
    await bot.send_message(chat_id=chat_id, text=("📊  <b>24-HOUR SOCIAL CHECK-IN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Last 24 hours mein kaisa performance raha?\n\n"
        "• Koi specific reach, views ya leads?\n"
        "• Content ke baare mein koi important observation ya update jo record karni hai?\n\n"
        "(Directly yahan reply kar do ya neeche button tap karo)\n"
        "━━━━━━━━━━━━━━━━━━━━"), parse_mode="HTML", reply_markup=checkin_keyboard())
