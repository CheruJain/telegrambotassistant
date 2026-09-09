"""Natural-language Telegram message handlers."""
from __future__ import annotations

import datetime as dt
import logging

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
        await _dispatch(update, context, user, tz, parsed, text)
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
    await update.message.reply_text("Voice transcription isn't wired up in this deployment yet - please type your message for now.")


async def _dispatch(update, context, user, tz, parsed, raw_text):
    intent = parsed.get("intent", "general_question")
    message = update.message
    handlers = {
        "log_work": lambda: _handle_log_work(message, user, tz, raw_text),
        "log_content": lambda: _handle_log_content(message, user, tz, parsed),
        "log_sales": lambda: _handle_log_sales(message, user, tz, parsed, raw_text),
        "create_meeting": lambda: _handle_create_meeting(message, user, tz, parsed),
        "update_meeting": lambda: _handle_update_meeting(message, user, tz, parsed),
        "cancel_meeting": lambda: _handle_cancel_meeting(message, user, parsed),
        "create_reminder": lambda: _handle_create_reminder(message, user, tz, parsed),
        "update_reminder": lambda: _handle_update_reminder(message, user, tz, parsed),
        "cancel_reminder": lambda: _handle_cancel_reminder(message, user, parsed),
        "pending_followups": lambda: _handle_pending_followups(message, user),
        "upcoming_meetings": lambda: _handle_upcoming_meetings(message, user, tz, parsed),
        "query_stats": lambda: _handle_query_stats(message, user, tz, parsed),
        "daily_summary": lambda: _handle_daily_summary(message, user, tz),
        "weekly_summary": lambda: _handle_weekly_summary(message, user, tz),
        "general_question": lambda: _handle_general_question(message, user, tz, parsed),
    }
    handler = handlers.get(intent)
    if handler:
        await handler()
    else:
        await _handle_general_question(message, user, tz, parsed)


async def _handle_log_work(message, user, tz, raw_text):
    today = dt.datetime.now(tz).date().isoformat()
    repo.log_activity(user["id"], today, "Other", "work_done", quantity=1, notes=raw_text[:200])
    await message.reply_text("Noted - logged today's work.")


async def _handle_log_content(message, user, tz, parsed):
    items = parsed.get("content_items") or []
    if not items:
        await message.reply_text("I couldn't tell what content was posted - could you rephrase?")
        return
    today = dt.datetime.now(tz).date().isoformat()
    saved = []
    for item in items:
        qty = int(item.get("quantity") or 1)
        platform = item.get("platform") or "Other"
        content_type = item.get("content_type") or "Other"
        account = item.get("account_name")
        repo.log_activity(user["id"], today, platform, content_type.lower(), quantity=qty, account_name=account)
        saved.append(f"{qty} {content_type}{'s' if qty != 1 else ''} on {account or platform}")
    await message.reply_text("Added:\n" + "\n".join(f"• {x}" for x in saved))


async def _handle_log_sales(message, user, tz, parsed, raw_text):
    sales = parsed.get("sales") or {}
    today = dt.datetime.now(tz).date().isoformat()
    if sales.get("lead_name"):
        follow_up_date = None
        if parsed.get("date_expression") and sales.get("outcome") in {"Follow Up", "Interested", "Rescheduled"}:
            resolved = resolve_datetime(parsed["date_expression"], tz.zone, now=dt.datetime.now(tz))
            if resolved:
                follow_up_date = resolved.date().isoformat()
        booked_date = None
        if sales.get("booked_date"):
            resolved = resolve_datetime(sales["booked_date"], tz.zone, now=dt.datetime.now(tz))
            if resolved:
                booked_date = resolved.date().isoformat()
        repo.log_sales_call(user["id"], today, lead_name=sales.get("lead_name"), source=sales.get("source"), outcome=sales.get("outcome"), objection=sales.get("objection"), follow_up_date=follow_up_date, deal_value=sales.get("deal_value"), phone_number=sales.get("phone_number"), booked_date=booked_date, booked_time=sales.get("booked_time"), call_type=sales.get("call_type"))
        await message.reply_text(f"Added: sales call with {sales['lead_name']} — {sales.get('outcome') or 'logged'}.")
        return

    parts = []
    for key, label, activity in (("total_calls", "sales calls", "calls"), ("interested", "interested", "interested"), ("follow_ups", "follow-ups", "follow_ups"), ("wins", "won", "wins")):
        value = sales.get(key)
        if value:
            notes = raw_text[:200] if key == "follow_ups" else None
            repo.log_activity(user["id"], today, "Sales", activity, quantity=int(value), notes=notes)
            parts.append(f"{value} {label}")
    if not parts:
        await message.reply_text("I couldn't find any numbers in that - could you rephrase?")
        return
    await message.reply_text("Added:\n" + "\n".join(f"• {x}" for x in parts))


async def _handle_create_meeting(message, user, tz, parsed):
    meeting = parsed.get("meeting") or {}
    start = resolve_datetime(parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not start:
        await message.reply_text("What day/time should I schedule that for? (e.g. 'kal 4 baje' or 'Friday 11 AM')")
        return
    meeting_type = meeting.get("meeting_type") or "other"
    is_sales_call = meeting_type == "sales_call" and bool(meeting.get("person"))
    person = meeting.get("person")
    title = meeting.get("title") or (f"Sales call with {person}" if is_sales_call else f"Meeting with {person}" if person else "Meeting")
    created = repo.create_meeting(user["id"], title, start.isoformat(), person=person, meeting_type=meeting_type)
    now = dt.datetime.now(tz)
    if is_sales_call:
        phone = meeting.get("phone_number")
        repo.log_sales_call(user["id"], now.date().isoformat(), lead_name=person, outcome="Booked", phone_number=phone, booked_date=start.date().isoformat(), booked_time=start.strftime("%H:%M:%S"), call_type=meeting.get("call_type"), notes="Booked sales call via Telegram")
        reminder_time = start - dt.timedelta(minutes=60)
        reminder_text = f"Confirm attendance: {person} — sales call at {start.strftime('%I:%M %p').lstrip('0')}"
        if phone:
            reminder_text += f"\nPhone: {phone}"
        reminder_prefix = "Confirmation reminder"
    else:
        reminder_field = parsed.get("reminder") or {}
        offset = int(reminder_field.get("offset_before_meeting_minutes") or settings.DEFAULT_MEETING_REMINDER_MINUTES)
        reminder_time = start - dt.timedelta(minutes=offset)
        reminder_text = f"{title} in {offset} minutes"
        reminder_prefix = "Reminder"
    if reminder_time > now:
        repo.create_reminder(user["id"], reminder_text, reminder_time.isoformat(), related_meeting_id=created["id"])
    await message.reply_text(f"Added:\n{start.strftime('%A, %d %b')}\n{start.strftime('%I:%M %p').lstrip('0')} — {title}{f'\nPhone: {meeting.get("phone_number")}' if is_sales_call and meeting.get("phone_number") else ''}\n\n{reminder_prefix}: {reminder_time.strftime('%I:%M %p').lstrip('0')}")


async def _meeting_matches_or_ask(message, matches):
    if not matches:
        await message.reply_text("I couldn't find an upcoming meeting matching that.")
        return None
    if len(matches) == 1:
        return matches[0]
    lines = [f"I found {len(matches)} matching meetings. Please be more specific:"]
    for row in matches[:5]:
        start = str(row.get("start_time") or "")[:16].replace("T", " ")
        lines.append(f"• {start} — {row.get('title') or 'Meeting'}")
    await message.reply_text("\n".join(lines))
    return None


async def _handle_update_meeting(message, user, tz, parsed):
    meeting = parsed.get("meeting") or {}
    search = meeting.get("search_text") or meeting.get("person") or meeting.get("title")
    if not search:
        await message.reply_text("Which meeting should I update? Please mention the person or title.")
        return
    target = await _meeting_matches_or_ask(message, repo.find_meeting_by_person_or_title(user["id"], search))
    if not target:
        return
    new_start = resolve_datetime(meeting.get("new_time_expression") or parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not new_start:
        await message.reply_text("What's the new time for that meeting?")
        return
    repo.update_meeting(target["id"], start_time=new_start.isoformat())
    repo.cancel_reminders_for_meeting(target["id"])
    reminder_time = new_start - dt.timedelta(minutes=settings.DEFAULT_MEETING_REMINDER_MINUTES)
    if reminder_time > dt.datetime.now(tz):
        repo.create_reminder(user["id"], f"{target['title']} in {settings.DEFAULT_MEETING_REMINDER_MINUTES} minutes", reminder_time.isoformat(), related_meeting_id=target["id"])
    await message.reply_text(f"Updated: {target['title']} moved to {new_start.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_cancel_meeting(message, user, parsed):
    meeting = parsed.get("meeting") or {}
    search = meeting.get("search_text") or meeting.get("person") or meeting.get("title")
    if not search:
        await message.reply_text("Which meeting should I cancel? Please mention the person or title.")
        return
    target = await _meeting_matches_or_ask(message, repo.find_meeting_by_person_or_title(user["id"], search))
    if not target:
        return
    repo.cancel_meeting(target["id"])
    repo.cancel_reminders_for_meeting(target["id"])
    await message.reply_text(f"Cancelled: {target['title']}.")


async def _handle_upcoming_meetings(message, user, tz, parsed):
    now = dt.datetime.now(tz)
    target = resolve_datetime(parsed.get("date_expression"), tz.zone, now=now) if parsed.get("date_expression") else None
    if target:
        start = dt.datetime.combine(target.date(), dt.time.min, tzinfo=tz)
        end = dt.datetime.combine(target.date(), dt.time.max, tzinfo=tz)
    else:
        start, end = now, now + dt.timedelta(days=7)
    meetings = repo.get_meetings_in_range(user["id"], start.isoformat(), end.isoformat())
    if not meetings:
        await message.reply_text("No meetings found in that range.")
        return
    lines = []
    for row in meetings:
        value = dt.datetime.fromisoformat(row["start_time"])
        if value.tzinfo is None:
            value = pytz.utc.localize(value)
        value = value.astimezone(tz)
        lines.append(f"{value.strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')} — {row.get('title') or 'Meeting'}")
    await message.reply_text("Upcoming:\n" + "\n".join(lines))


async def _handle_create_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}
    trigger = resolve_datetime(parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not trigger:
        await message.reply_text("When should I remind you?")
        return
    repo.create_reminder(user["id"], reminder.get("text") or "Reminder", trigger.isoformat(), recurrence=reminder.get("recurrence") or "none")
    await message.reply_text(f"Reminder set for {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_update_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}
    search = reminder.get("search_text") or reminder.get("text")
    if not search:
        await message.reply_text("Which reminder should I update? Please mention its text.")
        return
    matches = repo.find_pending_reminder(user["id"], search)
    if not matches:
        await message.reply_text(f"I couldn't find a pending reminder matching '{search}'.")
        return
    if len(matches) > 1:
        await message.reply_text("I found multiple matching reminders. Please make the reminder text more specific.")
        return
    trigger = resolve_datetime(reminder.get("new_time_expression") or parsed.get("date_expression"), tz.zone, now=dt.datetime.now(tz))
    if not trigger:
        await message.reply_text("What's the new reminder time?")
        return
    repo.update_reminder(matches[0]["id"], trigger_time=trigger.isoformat())
    await message.reply_text(f"Updated reminder to {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_cancel_reminder(message, user, parsed):
    reminder = parsed.get("reminder") or {}
    search = reminder.get("search_text") or reminder.get("text")
    if not search:
        await message.reply_text("Which reminder should I cancel? Please mention its text.")
        return
    matches = repo.find_pending_reminder(user["id"], search)
    if not matches:
        await message.reply_text(f"I couldn't find a pending reminder matching '{search}'.")
        return
    if len(matches) > 1:
        await message.reply_text("I found multiple matching reminders. Please make the reminder text more specific.")
        return
    repo.cancel_reminder(matches[0]["id"])
    await message.reply_text(f"Cancelled reminder: {matches[0]['reminder_text']}.")


async def _handle_pending_followups(message, user):
    rows = repo.get_pending_followups(user["id"])
    if not rows:
        await message.reply_text("No pending follow-ups.")
        return
    await message.reply_text("Pending follow-ups:\n" + "\n".join(f"• {r.get('lead_name') or 'Unknown'} — follow up: {r.get('follow_up_date') or 'No date'}" for r in rows))


async def _handle_query_stats(message, user, tz, parsed):
    query = parsed.get("query") or {}
    metric = query.get("metric")
    now = dt.datetime.now(tz)
    start = resolve_datetime(query.get("start_date"), tz.zone, now=now) if query.get("start_date") else None
    end = resolve_datetime(query.get("end_date"), tz.zone, now=now) if query.get("end_date") else None
    period = query.get("period")
    if start and end:
        start_date, end_date = start.date(), end.date()
    elif start:
        start_date, end_date = start.date(), now.date()
    elif period == "today":
        start_date = end_date = now.date()
    elif period == "yesterday":
        start_date = end_date = now.date() - dt.timedelta(days=1)
    else:
        start_date, end_date = week_bounds(now.date())

    if metric == "sales_calls":
        rows = repo.get_sales_calls(user["id"], start_date.isoformat(), end_date.isoformat())
        await message.reply_text(f"Sales calls: {len(rows)} ({start_date} to {end_date})")
    elif metric == "content":
        rows = repo.get_content(user["id"], start_date.isoformat(), end_date.isoformat())
        await message.reply_text(f"Content entries: {len(rows)} ({start_date} to {end_date})")
    elif metric == "meetings":
        rows = repo.get_meetings_in_range(user["id"], dt.datetime.combine(start_date, dt.time.min, tzinfo=tz).isoformat(), dt.datetime.combine(end_date, dt.time.max, tzinfo=tz).isoformat())
        await message.reply_text(f"Meetings: {len(rows)} ({start_date} to {end_date})")
    elif metric == "objections":
        rows = objections_list(user["id"], start_date, end_date)
        if not rows:
            await message.reply_text("No objections logged in that period.")
        else:
            question = query.get("text") or "What objection patterns do you see?"
            objections = [r.get("objection") or "Unknown" for r in rows]
            await message.reply_text(await _answer_objection(question, objections))
    else:
        await _handle_daily_summary(message, user, tz)


async def _answer_objection(question, objections):
    return answer_objection_question(question, objections)


async def _handle_daily_summary(message, user, tz):
    today = dt.datetime.now(tz).date()
    await message.reply_text(_format_daily(daily_summary_data(user["id"], today), today))


async def _handle_weekly_summary(message, user, tz):
    await message.reply_text("Crunching this week's numbers…")
    await message.reply_text(format_weekly_report(user["id"], dt.datetime.now(tz).date()))


async def _handle_general_question(message, user, tz, parsed):
    query = parsed.get("query") or {}
    question = query.get("text") or parsed.get("notes") or ""
    if question:
        await message.reply_text(answer_objection_question(question, []))
    else:
        await message.reply_text("I can log work, content, sales, meetings and reminders, or show your summaries. Try rephrasing that.")
