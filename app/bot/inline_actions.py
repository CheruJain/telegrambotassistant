"""Inline Telegram actions for reminders, scheduling conflicts and social check-ins."""
from __future__ import annotations
import datetime as dt
import re
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from app.config.settings import settings
from app.database import repository as repo
from app.bot.social_tracking import handle_social_callback
PENDING_CONFLICTS: dict[str, dict] = {}
def _phone(value: object) -> str:
    raw=re.sub(r"\D","",str(value or ""));return "91"+raw if len(raw)==10 else raw
def action_keyboard(phone:str|None=None,reminder_id:str|None=None)->InlineKeyboardMarkup|None:
    rows=[]
    if phone:
        number=_phone(phone)
        if number: rows.append([InlineKeyboardButton("📞 Call",url=f"tel:+{number}"),InlineKeyboardButton("💬 WhatsApp",url=f"https://wa.me/{number}")])
    if reminder_id: rows.append([InlineKeyboardButton("✅ Done",callback_data=f"reminder_done:{reminder_id}"),InlineKeyboardButton("⏰ +15m",callback_data=f"reminder_snooze:{reminder_id}:15")])
    return InlineKeyboardMarkup(rows) if rows else None
def conflict_keyboard()->InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Override",callback_data="conflict:override"),InlineKeyboardButton("Move to 4:30",callback_data="conflict:move430")]])
async def callback_handler(update:Update,context:ContextTypes.DEFAULT_TYPE):
    query=update.callback_query;data=query.data or ""
    if await handle_social_callback(update,context): return
    await query.answer();user=repo.get_or_create_user(update.effective_user.id,update.effective_user.first_name)
    if data.startswith("reminder_done:"):
        repo.cancel_reminder(data.split(":",1)[1]);await query.edit_message_reply_markup(reply_markup=None);await query.message.reply_text("Reminder marked done.");return
    if data.startswith("reminder_snooze:"):
        _,rid,minutes=data.split(":",2);target=next((r for r in repo.get_pending_reminders(user["id"]) if str(r.get("id"))==rid),None)
        if not target: await query.message.reply_text("That reminder is no longer pending.");return
        tz=pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE);trigger=dt.datetime.fromisoformat(target["trigger_time"]);trigger=pytz.utc.localize(trigger) if trigger.tzinfo is None else trigger;trigger=trigger.astimezone(tz)+dt.timedelta(minutes=int(minutes));repo.update_reminder(rid,trigger_time=trigger.isoformat());await query.edit_message_reply_markup(reply_markup=None);await query.message.reply_text(f"Snoozed by {minutes} minutes.");return
    if data.startswith("conflict:"):
        pending=PENDING_CONFLICTS.get(str(user["id"]))
        if not pending: await query.message.reply_text("That scheduling request has expired.");return
        tz=pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
        if data=="conflict:move430": pending["start"]=tz.localize(dt.datetime.combine(pending["start"].date(),dt.time(16,30)))
        repo.create_meeting(user["id"],pending["title"],pending["start"].isoformat(),person=pending.get("person"),meeting_type=pending.get("meeting_type") or "other");PENDING_CONFLICTS.pop(str(user["id"]),None);await query.edit_message_reply_markup(reply_markup=None);await query.message.reply_text("Meeting scheduled successfully.")
