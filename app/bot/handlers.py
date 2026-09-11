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
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        await update.message.reply_text("Voice transcription is not configured yet.")
        return
    user, tz = _get_user_and_tz(update)
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            path = tmp.name
        telegram_file = await context.bot.get_file(update.message.voice.file_id)
        await telegram_file.download_to_drive(path)
        if os.getenv("GROQ_API_KEY"):
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            model = os.getenv("WHISPER_MODEL", "whisper-large-v3-turbo")
        else:
            url = "https://api.openai.com/v1/audio/transcriptions"
            model = os.getenv("WHISPER_MODEL", "whisper-1")
        async with httpx.AsyncClient(timeout=90) as client:
            with open(path, "rb") as audio:
                response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, data={"model": model, "language": "hi"}, files={"file": ("voice.ogg", audio, "audio/ogg")})
            response.raise_for_status()
            text = response.json().get("text", "").strip()
        if not text:
            await update.message.reply_text("I couldn't hear the voice note clearly. Please try again.")
            return
        await update.message.reply_text(f"Heard: {text}")
        await _dispatch(update.message, user, tz, parse_message(text), text)
    except Exception as exc:
        logger.exception("Voice transcription failed: %s", exc)
        await update.message.reply_text("I couldn't transcribe that voice note. Please try again.")
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def _dispatch(message, user, tz, parsed, raw_text):
    intent = parsed.get("intent", "general_question")
    if intent == "log_work": await _handle_log_work(message, user, tz, raw_text)
    elif intent == "log_content": await _handle_log_content(message, user, tz, parsed)
    elif intent == "log_sales": await _handle_log_sales(message, user, tz, parsed, raw_text)
    elif intent == "create_meeting": await _handle_create_meeting(message, user, tz, parsed)
    elif intent == "update_meeting": await _handle_update_meeting(message, user, tz, parsed)
    elif intent == "cancel_meeting": await _handle_cancel_meeting(message, user, parsed)
    elif intent == "create_reminder": await _handle_create_reminder(message, user, tz, parsed)
    elif intent == "update_reminder": await _handle_update_reminder(message, user, tz, parsed)
    elif intent == "cancel_reminder": await _handle_cancel_reminder(message, user, parsed)
    elif intent == "pending_followups": await _handle_pending_followups(message, user)
    elif intent == "upcoming_meetings": await _handle_upcoming_meetings(message, user, tz, parsed)
    elif intent == "query_stats": await _handle_query_stats(message, user, tz, parsed)
    elif intent == "daily_summary":
        today = dt.datetime.now(tz).date(); await message.reply_text(_format_daily(daily_summary_data(user["id"], today), today), parse_mode="HTML")
    elif intent == "weekly_summary":
        await message.reply_text("Crunching this week's numbers…"); await message.reply_text(format_weekly_report(user["id"], dt.datetime.now(tz).date()))
    else: await _handle_general_question(message, parsed)


async def handle_reschedule_shortcut(message, user, tz, text: str) -> bool:
    m = re.match(r"^(.+?)\s+(kal|tomorrow|\+1d|\+2d|\+\d+d)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", text.strip(), re.I)
    if not m:
        return False
    person, day, hour = m.group(1).strip(), m.group(2).lower(), int(m.group(3)); minute = int(m.group(4) or 0); mer = (m.group(5) or "").lower()
    if mer == "pm" and hour < 12: hour += 12
    elif mer == "am" and hour == 12: hour = 0
    days = 1 if day in {"kal", "tomorrow", "+1d"} else (2 if day == "+2d" else int(day[1:-1]))
    matches = repo.find_meeting_by_person_or_title(user["id"], person)
    if len(matches) != 1:
        return False
    base = dt.datetime.now(tz) + dt.timedelta(days=days)
    new_start = tz.localize(dt.datetime.combine(base.date(), dt.time(hour, minute)))
    target = matches[0]
    repo.update_meeting(target["id"], start_time=new_start.isoformat())
    repo.cancel_reminders_for_meeting(target["id"])
    reminder_time = new_start - dt.timedelta(minutes=settings.DEFAULT_MEETING_REMINDER_MINUTES)
    if reminder_time > dt.datetime.now(tz): repo.create_reminder(user["id"], f"{target['title']} in {settings.DEFAULT_MEETING_REMINDER_MINUTES} minutes", reminder_time.isoformat(), related_meeting_id=target["id"])
    await message.reply_text(f"Updated: {target['title']} → {new_start.strftime('%d %b at %I:%M %p').lstrip('0').replace(' 0', ' ')}.")
    return True


async def _handle_log_work(message, user, tz, raw_text):
    repo.log_activity(user["id"], dt.datetime.now(tz).date().isoformat(), "Other", "work_done", quantity=1, notes=raw_text[:200]); await message.reply_text("Noted - logged today's work.")


async def _handle_log_content(message, user, tz, parsed):
    items = parsed.get("content_items") or []
    if not items: await message.reply_text("I couldn't tell what content was posted - could you rephrase?"); return
    today = dt.datetime.now(tz).date().isoformat(); saved = []
    for item in items:
        qty = int(item.get("quantity") or 1); platform = item.get("platform") or "Other"; content_type = item.get("content_type") or "Other"; account = item.get("account_name")
        repo.log_activity(user["id"], today, platform, content_type.lower(), quantity=qty, account_name=account); saved.append(f"{qty} {content_type}{'s' if qty != 1 else ''} on {account or platform}")
    await message.reply_text("Added:\n" + "\n".join(f"• {x}" for x in saved))


async def _handle_log_sales(message, user, tz, parsed, raw_text):
    sales = parsed.get("sales") or {}; today = dt.datetime.now(tz).date().isoformat()
    if sales.get("lead_name"):
        follow_up_date = None
        if parsed.get("date_expression") and sales.get("outcome") in {"Follow Up", "Interested", "Rescheduled"}:
            resolved = resolve_datetime(parsed["date_expression"], tz.zone, now=dt.datetime.now(tz)); follow_up_date = resolved.date().isoformat() if resolved else None
        booked_date = None
        if sales.get("booked_date"):
            resolved = resolve_datetime(sales["booked_date"], tz.zone, now=dt.datetime.now(tz)); booked_date = resolved.date().isoformat() if resolved else None
        repo.log_sales_call(user["id"], today, lead_name=sales.get("lead_name"), source=sales.get("source"), outcome=sales.get("outcome"), objection=sales.get("objection"), follow_up_date=follow_up_date, deal_value=sales.get("deal_value"), phone_number=sales.get("phone_number"), booked_date=booked_date, booked_time=sales.get("booked_time"), call_type=sales.get("call_type")); await message.reply_text(f"Added: sales call with {sales['lead_name']} — {sales.get('outcome') or 'logged'}."); return
    parts = []
    for key, label, activity in (("total_calls", "sales calls", "calls"), ("interested", "interested", "interested"), ("follow_ups", "follow-ups", "follow_ups"), ("wins", "won", "wins")):
        value = sales.get(key)
        if value: repo.log_activity(user["id"], today, "Sales", activity, quantity=int(value), notes=raw_text[:200] if key == "follow_ups" else None); parts.append(f"{value} {label}")
    if not parts: await message.reply_text("I couldn't find any numbers in that - could you rephrase?"); return
    await message.reply_text("Added:\n" + "\n".join(f"• {x}" for x in parts))


async def _handle_create_meeting(message, user, tz, parsed):
    meeting = parsed.get("meeting") or {}; start = resolve_datetime(parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not start: await message.reply_text("What day/time should I schedule that for? (e.g. 'kal 4 baje' or 'Friday 11 AM')"); return
    meeting_type = meeting.get("meeting_type") or "other"; is_sales_call = meeting_type == "sales_call" and bool(meeting.get("person")); person = meeting.get("person"); phone = meeting.get("phone_number"); title = meeting.get("title") or (f"Sales call with {person}" if is_sales_call else f"Meeting with {person}" if person else "Meeting")
    created = repo.create_meeting(user["id"], title, start.isoformat(), person=person, meeting_type=meeting_type); now = dt.datetime.now(tz)
    if is_sales_call:
        repo.log_sales_call(user["id"], now.date().isoformat(), lead_name=person, outcome="Booked", phone_number=phone, booked_date=start.date().isoformat(), booked_time=start.strftime("%H:%M:%S"), call_type=meeting.get("call_type"), notes="Booked sales call via Telegram"); reminder_time = start - dt.timedelta(minutes=60); reminder_text = f"Confirm attendance: {person} — sales call at {start.strftime('%I:%M %p').lstrip('0')}"; reminder_text += f"\nPhone: {phone}" if phone else ""; reminder_prefix = "Confirmation reminder"
    else:
        offset = int((parsed.get("reminder") or {}).get("offset_before_meeting_minutes") or settings.DEFAULT_MEETING_REMINDER_MINUTES); reminder_time = start - dt.timedelta(minutes=offset); reminder_text = f"{title} in {offset} minutes"; reminder_prefix = "Reminder"
    if reminder_time > now: repo.create_reminder(user["id"], reminder_text, reminder_time.isoformat(), related_meeting_id=created["id"])
    await message.reply_text(f"Added:\n{start.strftime('%A, %d %b')}\n{start.strftime('%I:%M %p').lstrip('0')} — {title}\n\n{reminder_prefix}: {reminder_time.strftime('%I:%M %p').lstrip('0')}")


async def _meeting_target(message, matches):
    if not matches: await message.reply_text("I couldn't find an upcoming meeting matching that."); return None
    if len(matches) == 1: return matches[0]
    await message.reply_text("I found multiple matching meetings. Please be more specific."); return None


async def _handle_update_meeting(message, user, tz, parsed):
    meeting = parsed.get("meeting") or {}; search = meeting.get("search_text") or meeting.get("person") or meeting.get("title")
    if not search: await message.reply_text("Which meeting should I update? Please mention the person or title."); return
    target = await _meeting_target(message, repo.find_meeting_by_person_or_title(user["id"], search))
    if not target: return
    new_start = resolve_datetime(meeting.get("new_time_expression") or parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not new_start: await message.reply_text("What's the new time for that meeting?"); return
    repo.update_meeting(target["id"], start_time=new_start.isoformat()); repo.cancel_reminders_for_meeting(target["id"]); reminder_time = new_start - dt.timedelta(minutes=settings.DEFAULT_MEETING_REMINDER_MINUTES)
    if reminder_time > dt.datetime.now(tz): repo.create_reminder(user["id"], f"{target['title']} in {settings.DEFAULT_MEETING_REMINDER_MINUTES} minutes", reminder_time.isoformat(), related_meeting_id=target["id"])
    await message.reply_text(f"Updated: {target['title']} moved to {new_start.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_cancel_meeting(message, user, parsed):
    meeting = parsed.get("meeting") or {}; search = meeting.get("search_text") or meeting.get("person") or meeting.get("title")
    if not search: await message.reply_text("Which meeting should I cancel? Please mention the person or title."); return
    target = await _meeting_target(message, repo.find_meeting_by_person_or_title(user["id"], search))
    if not target: return
    repo.cancel_meeting(target["id"]); repo.cancel_reminders_for_meeting(target["id"]); await message.reply_text(f"Cancelled: {target['title']}.")


async def _handle_upcoming_meetings(message, user, tz, parsed):
    now = dt.datetime.now(tz); target = resolve_datetime(parsed.get("date_expression"), tz.zone, now=now) if parsed.get("date_expression") else None
    if target: start = dt.datetime.combine(target.date(), dt.time.min, tzinfo=tz); end = dt.datetime.combine(target.date(), dt.time.max, tzinfo=tz)
    else: start, end = now, now + dt.timedelta(days=7)
    rows = repo.get_meetings_in_range(user["id"], start.isoformat(), end.isoformat())
    if not rows: await message.reply_text("No meetings found in that range."); return
    lines = []
    for row in rows:
        value = dt.datetime.fromisoformat(row["start_time"]); value = pytz.utc.localize(value) if value.tzinfo is None else value; value = value.astimezone(tz); lines.append(f"{value.strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')} — {row.get('title') or 'Meeting'}")
    await message.reply_text("Upcoming:\n" + "\n".join(lines))


async def _handle_create_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}; trigger = resolve_datetime(parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not trigger: await message.reply_text("When should I remind you?"); return
    repo.create_reminder(user["id"], reminder.get("text") or "Reminder", trigger.isoformat(), recurrence=reminder.get("recurrence") or "none"); await message.reply_text(f"Reminder set for {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_update_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}; search = reminder.get("search_text") or reminder.get("text"); matches = repo.find_pending_reminder(user["id"], search) if search else []
    if not matches: await message.reply_text(f"I couldn't find a pending reminder matching '{search}'."); return
    if len(matches) > 1: await message.reply_text("I found multiple matching reminders. Please make the reminder text more specific."); return
    trigger = resolve_datetime(reminder.get("new_time_expression") or parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not trigger: await message.reply_text("What's the new reminder time?"); return
    repo.update_reminder(matches[0]["id"], trigger_time=trigger.isoformat()); await message.reply_text(f"Updated reminder to {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_cancel_reminder(message, user, parsed):
    reminder = parsed.get("reminder") or {}; search = reminder.get("search_text") or reminder.get("text"); matches = repo.find_pending_reminder(user["id"], search) if search else []
    if not matches: await message.reply_text(f"I couldn't find a pending reminder matching '{search}'."); return
    if len(matches) > 1: await message.reply_text("I found multiple matching reminders. Please make the reminder text more specific."); return
    repo.cancel_reminder(matches[0]["id"]); await message.reply_text(f"Cancelled reminder: {matches[0]['reminder_text']}.")


async def _handle_pending_followups(message, user):
    rows = repo.get_pending_followups(user["id"])
    if not rows: await message.reply_text("No pending follow-ups."); return
    await message.reply_text("Pending follow-ups:\n" + "\n".join(f"• {r.get('lead_name') or 'Unknown'} — follow up: {r.get('follow_up_date') or 'No date'}" for r in rows))


async def _handle_query_stats(message, user, tz, parsed):
    query = parsed.get("query") or {}; metric = query.get("metric"); now = dt.datetime.now(tz); start = resolve_datetime(query.get("start_date"), tz.zone, now=now) if query.get("start_date") else None; end = resolve_datetime(query.get("end_date"), tz.zone, now=now) if query.get("end_date") else None
    if start and end: start_date, end_date = start.date(), end.date()
    elif start: start_date, end_date = start.date(), now.date()
    elif query.get("period") == "today": start_date = end_date = now.date()
    elif query.get("period") == "yesterday": start_date = end_date = now.date() - dt.timedelta(days=1)
    else: start_date, end_date = week_bounds(now.date())
    if metric == "sales_calls":
        rows = repo.get_sales_calls(user["id"], start_date.isoformat(), end_date.isoformat()); await message.reply_text(f"Sales calls: {len(rows)} ({start_date} to {end_date})")
    elif metric == "content":
        rows = repo.get_content(user["id"], start_date.isoformat(), end_date.isoformat()); await message.reply_text(f"Content entries: {len(rows)} ({start_date} to {end_date})")
    elif metric == "meetings":
        rows = repo.get_meetings_in_range(user["id"], dt.datetime.combine(start_date, dt.time.min, tzinfo=tz).isoformat(), dt.datetime.combine(end_date, dt.time.max, tzinfo=tz).isoformat()); await message.reply_text(f"Meetings: {len(rows)} ({start_date} to {end_date})")
    elif metric == "objections":
        rows = objections_list(user["id"], start_date, end_date); await message.reply_text(answer_objection_question(query.get("text") or "What objection patterns do you see?", [r.get("objection") or "Unknown" for r in rows]))
    else:
        today = now.date(); await message.reply_text(_format_daily(daily_summary_data(user["id"], today), today), parse_mode="HTML")


async def _handle_general_question(message, parsed):
    query = parsed.get("query") or {}; question = query.get("text") or parsed.get("notes") or ""
    await message.reply_text(answer_objection_question(question, [])) if question else await message.reply_text("I can log work, content, sales, meetings and reminders, or show your summaries. Try rephrasing that.")
