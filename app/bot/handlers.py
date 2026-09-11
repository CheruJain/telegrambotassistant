"""Stable natural-language Telegram message handlers."""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import tempfile

import httpx
import pytz
from telegram import Update
from telegram.ext import ContextTypes

from app.config.settings import settings
from app.database import repository as repo
from app.ai.parser import parse_message, resolve_datetime
from app.ai.analyzer import answer_objection_question
from app.analytics.metrics import daily_summary_data, objections_list, week_bounds
from app.reports.weekly import format_weekly_report
from app.bot.commands import _format_daily

logger = logging.getLogger(__name__)


async def _reject_unauthorized(update: Update) -> bool:
    if not repo.is_authorized(update.effective_user.id):
        await update.message.reply_text("This is a private assistant. You're not authorized to use it.")
        return True
    return False


def _get_user_and_tz(update: Update):
    user = repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)
    return user, pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_unauthorized(update):
        return
    message = update.message
    text = (message.text or "").strip()
    if not text:
        return
    user, tz = _get_user_and_tz(update)
    try:
        if repo.already_processed(user["id"], message.chat_id, message.message_id):
            return
    except Exception as exc:
        logger.warning("Duplicate check failed: %s", exc)
    try:
        parsed = parse_message(text)
        await _dispatch(message, user, tz, parsed, text)
    except Exception as exc:
        logger.exception("Message handling failed: %s", exc)
        await message.reply_text("Something went wrong while saving that. Nothing was recorded - please try again.")
        return
    try:
        repo.mark_processed(user["id"], message.chat_id, message.message_id)
    except Exception:
        pass


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_unauthorized(update):
        return
    message = update.message
    voice = message.voice
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        await message.reply_text("Voice transcription is not configured yet.")
        return
    user, tz = _get_user_and_tz(update)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        path = tmp.name
    try:
        telegram_file = await context.bot.get_file(voice.file_id)
        await telegram_file.download_to_drive(path)
        if os.getenv("GROQ_API_KEY"):
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            headers = {"Authorization": f"Bearer {api_key}"}
            data = {"model": os.getenv("WHISPER_MODEL", "whisper-large-v3-turbo"), "language": "hi"}
        else:
            url = "https://api.openai.com/v1/audio/transcriptions"
            headers = {"Authorization": f"Bearer {api_key}"}
            data = {"model": os.getenv("WHISPER_MODEL", "whisper-1"), "language": "hi"}
        async with httpx.AsyncClient(timeout=90) as client:
            with open(path, "rb") as audio:
                response = await client.post(url, headers=headers, data=data, files={"file": ("voice.ogg", audio, "audio/ogg")})
            response.raise_for_status()
            text = response.json().get("text", "").strip()
        if not text:
            await message.reply_text("I couldn't hear the voice note clearly. Please try again.")
            return
        await message.reply_text(f"Heard: {text}")
        parsed = parse_message(text)
        await _dispatch(message, user, tz, parsed, text)
    except Exception as exc:
        logger.exception("Voice transcription failed: %s", exc)
        await message.reply_text("I couldn't transcribe that voice note. Please try again.")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def _dispatch(message, user, tz, parsed, raw_text):
    intent = parsed.get("intent", "general_question")
    if intent == "log_work":
        await _handle_log_work(message, user, tz, raw_text)
    elif intent == "log_content":
        await _handle_log_content(message, user, tz, parsed)
    elif intent == "log_sales":
        await _handle_log_sales(message, user, tz, parsed, raw_text)
    elif intent == "create_meeting":
        await _handle_create_meeting(message, user, tz, parsed)
    elif intent == "update_meeting":
        await _handle_update_meeting(message, user, tz, parsed)
    elif intent == "cancel_meeting":
        await _handle_cancel_meeting(message, user, parsed)
    elif intent == "create_reminder":
        await _handle_create_reminder(message, user, tz, parsed)
    elif intent == "update_reminder":
        await _handle_update_reminder(message, user, tz, parsed)
    elif intent == "cancel_reminder":
        await _handle_cancel_reminder(message, user, parsed)
    elif intent == "pending_followups":
        await _handle_pending_followups(message, user)
    elif intent == "upcoming_meetings":
        await _handle_upcoming_meetings(message, user, tz, parsed)
    elif intent == "query_stats":
        await _handle_query_stats(message, user, tz, parsed)
    elif intent == "daily_summary":
        today = dt.datetime.now(tz).date()
        await message.reply_text(_format_daily(daily_summary_data(user["id"], today), today), parse_mode="HTML")
    elif intent == "weekly_summary":
        await message.reply_text("Crunching this week's numbers…")
        await message.reply_text(format_weekly_report(user["id"], dt.datetime.now(tz).date()))
    else:
        await _handle_general_question(message, parsed)


def _shortcut_target(text: str):
    value = text.strip()
    m = re.match(r"^(.+?)\s+(kal|tomorrow|\+1d|\+2d|\+\d+d)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", value, re.I)
    if not m:
        return None
    person = m.group(1).strip()
    day = m.group(2).lower()
    hour = int(m.group(3))
    minute = int(m.group(4) or 0)
    mer = (m.group(5) or "").lower()
    if mer == "pm" and hour < 12:
        hour += 12
    elif mer == "am" and hour == 12:
        hour = 0
    if day in {"kal", "tomorrow", "+1d"}:
        days = 1
    elif day == "+2d":
        days = 2
    else:
        days = int(day[1:-1])
    return person, days, hour, minute


async def handle_reschedule_shortcut(message, user, tz, text: str) -> bool:
    target = _shortcut_target(text)
    if not target:
        return False
    person, days, hour, minute = target
    matches = repo.find_meeting_by_person_or_title(user["id"], person)
    if len(matches) != 1:
        return False
    meeting = matches[0]
    base = dt.datetime.now(tz) + dt.timedelta(days=days)
    new_start = tz.localize(dt.datetime.combine(base.date(), dt.time(hour, minute)))
    repo.update_meeting(meeting["id"], start_time=new_start.isoformat())
    repo.cancel_reminders_for_meeting(meeting["id"])
    reminder_time = new_start - dt.timedelta(minutes=settings.DEFAULT_MEETING_REMINDER_MINUTES)
    if reminder_time > dt.datetime.now(tz):
        repo.create_reminder(user["id"], f"{meeting['title']} in {settings.DEFAULT_MEETING_REMINDER_MINUTES} minutes", reminder_time.isoformat(), related_meeting_id=meeting["id"])
    await message.reply_text(f"Updated: {meeting['title']} → {new_start.strftime('%d %b at %I:%M %p').lstrip('0').replace(' 0', ' ')}.")
    return True


# Existing deterministic handlers remain below this point.
