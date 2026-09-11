"""Inline Telegram actions for reminders and scheduling conflicts."""
from __future__ import annotations

import datetime as dt
import re

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database import repository as repo


def _phone(value: object) -> str:
    raw = re.sub(r"\D", "", str(value or ""))
    if len(raw) == 10:
        return "91" + raw
    if raw.startswith("91"):
        return raw
    return raw


def action_keyboard(phone: str | None = None, reminder_id: str | None = None) -> InlineKeyboardMarkup | None:
    rows = []
    if phone:
        number = _phone(phone)
        if number:
            rows.append([
                InlineKeyboardButton("📞 Call", url=f"tel:+{number}"),
                InlineKeyboardButton("💬 WhatsApp", url=f"https://wa.me/{number}"),
            ])
    if reminder_id:
        rows.append([
            InlineKeyboardButton("✅ Done", callback_data=f"reminder_done:{reminder_id}"),
            InlineKeyboardButton("⏰ +15m", callback_data=f"reminder_snooze:{reminder_id}:15"),
        ])
    return InlineKeyboardMarkup(rows) if rows else None


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)

    if data.startswith("reminder_done:"):
        reminder_id = data.split(":", 1)[1]
        repo.cancel_reminder(reminder_id)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Reminder marked done.")
        return

    if data.startswith("reminder_snooze:"):
        _, reminder_id, minutes = data.split(":", 2)
        rows = repo.get_pending_reminders(user["id"])
        target = next((r for r in rows if str(r.get("id")) == reminder_id), None)
        if not target:
            await query.message.reply_text("That reminder is no longer pending.")
            return
        tz = pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
        trigger = dt.datetime.fromisoformat(target["trigger_time"])
        if trigger.tzinfo is None:
            trigger = pytz.utc.localize(trigger)
        trigger = trigger.astimezone(tz) + dt.timedelta(minutes=int(minutes))
        repo.update_reminder(reminder_id, trigger_time=trigger.isoformat())
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(f"Snoozed by {minutes} minutes.")
        return

    if data.startswith("conflict_override:"):
        meeting_id = data.split(":", 1)[1]
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Slot conflict overridden. The new meeting can now be scheduled.")
        context.user_data["conflict_override_meeting_id"] = meeting_id
        return

    if data.startswith("conflict_move:"):
        payload = data.split(":", 1)[1]
        try:
            meeting_id, date_text, time_text = payload.split("|", 2)
            tz = pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
            start = tz.localize(dt.datetime.fromisoformat(f"{date_text} {time_text}"))
            repo.update_meeting(meeting_id, start_time=start.isoformat())
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(f"Moved to {start.strftime('%d %b at %I:%M %p').lstrip('0').replace(' 0', ' ')}.")
        except Exception:
            await query.message.reply_text("I couldn't move that meeting. Please try again.")
