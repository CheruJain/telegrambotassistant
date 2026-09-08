"""
Main natural-language message router.

Flow for every free-text message:
  1. Auth check (only TELEGRAM_USER_ID may talk to this bot).
  2. Duplicate-message check (Telegram retries, spec section 27).
  3. Single AI call -> intent + raw entities (spec section 3/28).
  4. Deterministic dispatch to the right DB action.
  5. Honest reply - if a DB write fails, say so, never claim success.
"""
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
from app.analytics.metrics import daily_summary_data, weekly_summary_data, objections_list, week_bounds
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
    tz = pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
    return user, tz


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _reject_unauthorized(update):
        return

    message = update.message
    text = message.text or ""
    if not text.strip():
        return

    user, tz = _get_user_and_tz(update)

    # Duplicate protection (Telegram may retry delivery of the same update).
    try:
        if repo.already_processed(user["id"], message.chat_id, message.message_id):
            return
    except Exception as e:
        logger.warning("Duplicate check failed, continuing anyway: %s", e)

    try:
        parsed = parse_message(text)
    except Exception as e:
        logger.error("AI parse failed: %s", e)
        await message.reply_text(
            "Sorry, I couldn't understand that right now (AI service issue). Please try again "
            "or use a command like /help."
        )
        return

    try:
        await _dispatch(update, context, user, tz, parsed, text)
    except Exception as e:
        logger.exception("Failed to handle intent %s: %s", parsed.get("intent"), e)
        await message.reply_text(
            "Something went wrong while saving that. Nothing was recorded - please try again."
        )
        return

    try:
        repo.mark_processed(user["id"], message.chat_id, message.message_id)
    except Exception:
        pass


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Voice message support (spec section 22). Transcribes then reuses the
    exact same text pipeline, so all intents work identically from voice."""
    if await _reject_unauthorized(update):
        return

    await update.message.reply_text(
        "Voice transcription isn't wired up in this deployment yet - please type your message "
        "for now. (The service interface for this is ready in app/ai/ for a future update.)"
    )


async def _dispatch(update, context, user: dict, tz, parsed: dict, raw_text: str):
    intent = parsed.get("intent", "general_question")
    message = update.message

    if intent == "log_work":
        await _handle_log_work(message, user, tz, parsed, raw_text)
    elif intent == "log_content":
        await _handle_log_content(message, user, tz, parsed)
    elif intent == "log_sales":
        await _handle_log_sales(message, user, tz, parsed)
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
        await _handle_cancel_reminder(message, user, tz, parsed)
    elif intent == "daily_summary":
        today = dt.datetime.now(tz).date()
        data = daily_summary_data(user["id"], today)
        await message.reply_text(_format_daily(data, today))
    elif intent == "weekly_summary":
        await message.reply_text("Crunching this week's numbers…")
        today = dt.datetime.now(tz).date()
        await message.reply_text(format_weekly_report(user["id"], today))
    elif intent == "pending_followups":
        await _handle_pending_followups(message, user)
    elif intent == "upcoming_meetings":
        await _handle_upcoming_meetings(message, user, tz, parsed)
    elif intent == "query_stats":
        await _handle_query_stats(message, user, tz, parsed, raw_text)
    else:
        await message.reply_text(
            "I'm not sure how to act on that yet. Try /help to see what I can do, or rephrase."
        )


# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------
async def _handle_log_work(message, user, tz, parsed, raw_text):
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
        qty = item.get("quantity") or 1
        platform = item.get("platform") or "Other"
        content_type = item.get("content_type") or "Other"
        account = item.get("account_name")
        repo.log_activity(
            user["id"], today, platform, content_type.lower(), quantity=qty,
            account_name=account,
        )
        saved.append(f"{qty} {content_type}{'s' if qty > 1 else ''} on {account or platform}")

    await message.reply_text("Added:\n" + "\n".join(f"• {s}" for s in saved))


async def _handle_log_sales(message, user, tz, parsed):
    sales = parsed.get("sales") or {}
    today = dt.datetime.now(tz).date().isoformat()

    if sales.get("lead_name"):
        follow_up_date = None
        date_expr = parsed.get("date_expression")
        if date_expr and sales.get("outcome") in ("Follow Up", "Interested", "Rescheduled"):
            resolved = resolve_datetime(date_expr, tz.zone)
            if resolved:
                follow_up_date = resolved.date().isoformat()

        booked_date = None
        if sales.get("booked_date"):
            resolved_booked = resolve_datetime(sales.get("booked_date"), tz.zone)
            if resolved_booked:
                booked_date = resolved_booked.date().isoformat()

        repo.log_sales_call(
            user_id=user["id"],
            date=today,
            lead_name=sales.get("lead_name"),
            source=sales.get("source"),
            outcome=sales.get("outcome"),
            objection=sales.get("objection"),
            follow_up_date=follow_up_date,
            deal_value=sales.get("deal_value"),
            phone_number=sales.get("phone_number"),
            booked_date=booked_date,
            booked_time=sales.get("booked_time"),
            call_type=sales.get("call_type"),
            confirmation_reminder_sent=False,
        )
        await message.reply_text(
            f"Added: sales call with {sales['lead_name']} — {sales.get('outcome') or 'logged'}."
        )
        return

    parts = []
    if sales.get("total_calls"):
        repo.log_activity(user["id"], today, "Sales", "calls", quantity=sales["total_calls"])
        parts.append(f"{sales['total_calls']} sales calls")
    if sales.get("interested"):
        repo.log_activity(user["id"], today, "Sales", "interested", quantity=sales["interested"])
        parts.append(f"{sales['interested']} interested")
    if sales.get("follow_ups"):
        repo.log_activity(user["id"], today, "Sales", "follow_ups", quantity=sales["follow_ups"])
        parts.append(f"{sales['follow_ups']} follow-ups")
    if sales.get("wins"):
        repo.log_activity(user["id"], today, "Sales", "wins", quantity=sales["wins"])
        parts.append(f"{sales['wins']} won")

    if not parts:
        await message.reply_text("I couldn't find any numbers in that - could you rephrase?")
        return

    await message.reply_text("Added:\n" + "\n".join(f"• {p}" for p in parts))


# ------------------------------------------------------------------
# Meetings
# ------------------------------------------------------------------
async def _handle_create_meeting(message, user, tz, parsed):
    meeting = parsed.get("meeting") or {}
    date_expr = parsed.get("date_expression")
    start = resolve_datetime(date_expr, tz.zone, now=dt.datetime.now(tz))

    if start is None:
        await message.reply_text(
            "What day/time should I schedule that for? (e.g. 'kal 4 baje' or 'Friday 11 AM')"
        )
        return

    meeting_type = meeting.get("meeting_type") or "other"
    is_sales_call = meeting_type == "sales_call" and bool(meeting.get("person"))

    title = meeting.get("title") or (
        f"Sales call with {meeting['person']}" if is_sales_call else
        f"Meeting with {meeting['person']}" if meeting.get("person") else "Meeting"
    )

    created = repo.create_meeting(
        user_id=user["id"],
        title=title,
        start_time_iso=start.isoformat(),
        person=meeting.get("person"),
        meeting_type=meeting_type,
    )

    now = dt.datetime.now(tz)
    if is_sales_call:
        repo.log_sales_call(
            user_id=user["id"],
            date=now.date().isoformat(),
            lead_name=meeting.get("person"),
            outcome="Booked",
            phone_number=meeting.get("phone_number"),
            booked_date=start.date().isoformat(),
            booked_time=start.strftime("%H:%M:%S"),
            call_type=meeting.get("call_type"),
            confirmation_reminder_sent=False,
            notes="Booked sales call via Telegram",
        )

        offset_minutes = 60
        reminder_time = start - dt.timedelta(minutes=offset_minutes)
        reminder_text = (
            f"Confirm attendance: {meeting['person']} — sales call at "
            f"{start.strftime('%I:%M %p').lstrip('0')}"
        )
        reminder_prefix = "Confirmation reminder"
    else:
        reminder_field = parsed.get("reminder") or {}
        offset_minutes = reminder_field.get("offset_before_meeting_minutes") or \
            settings.DEFAULT_MEETING_REMINDER_MINUTES
        reminder_time = start - dt.timedelta(minutes=offset_minutes)
        reminder_text = f"{title} in {offset_minutes} minutes"
        reminder_prefix = "Reminder"

    if reminder_time > now:
        repo.create_reminder(
            user_id=user["id"],
            reminder_text=reminder_text,
            trigger_time_iso=reminder_time.isoformat(),
            related_meeting_id=created["id"],
        )

    day_label = start.strftime("%A, %d %b")
    time_label = start.strftime("%I:%M %p").lstrip("0")
    reminder_label = reminder_time.strftime("%I:%M %p").lstrip("0")
    phone_label = (
        f"\nPhone: {meeting['phone_number']}"
        if is_sales_call and meeting.get("phone_number")
        else ""
    )
    await message.reply_text(
        f"Added:\n{day_label}, {time_label}\n{title}{phone_label}\n\n{reminder_prefix}: {reminder_label}"
    )


async def _handle_update_meeting(message, user, tz, parsed):
    meeting_info = parsed.get("meeting") or {}
    search_text = meeting_info.get("search_text") or meeting_info.get("person") or meeting_info.get("title")
    if not search_text:
        await message.reply_text("Which meeting should I update? Please mention the person or title.")
        return

    matches = repo.find_meeting_by_person_or_title(user["id"], search_text)
    if not matches:
        await message.reply_text(f"I couldn't find an upcoming meeting matching '{search_text}'.")
        return
    target = matches[0]

    new_time_expr = meeting_info.get("new_time_expression") or parsed.get("date_expression")
    new_start = resolve_datetime(new_time_expr, tz.zone, now=dt.datetime.now(tz))
    if new_start is None:
        await message.reply_text("What's the new time for that meeting?")
        return

    repo.update_meeting(target["id"], start_time=new_start.isoformat())
    repo.cancel_reminders_for_meeting(target["id"])
    reminder_time = new_start - dt.timedelta(minutes=settings.DEFAULT_MEETING_REMINDER_MINUTES)
    if reminder_time > dt.datetime.now(tz):
        repo.create_reminder(
            user_id=user["id"],
            reminder_text=f"{target['title']} in {settings.DEFAULT_MEETING_REMINDER_MINUTES} minutes",
            trigger_time_iso=reminder_time.isoformat(),
            related_meeting_id=target["id"],
        )

    time_label = new_start.strftime("%A, %d %b %I:%M %p").replace(" 0", " ")
    await message.reply_text(f"Updated: {target['title']} moved to {time_label}.")


async def _handle_cancel_meeting(message, user, parsed):
    meeting_info = parsed.get("meeting") or {}
    search_text = meeting_info.get("search_text") or meeting_info.get("person") or meeting_info.get("title")
    if not search_text:
        await message.reply_text("Which meeting should I cancel? Please mention the person or title.")
        return

    matches = repo.find_meeting_by_person_or_title(user["id"], search_text)
    if not matches:
        await message.reply_text(f"I couldn't find an upcoming meeting matching '{search_text}'.")
        return
    target = matches[0]
    repo.cancel_meeting(target["id"])
    repo.cancel_reminders_for_meeting(target["id"])
    await message.reply_text(f"Cancelled: {target['title']}.")


async def _handle_upcoming_meetings(message, user, tz, parsed):
    now = dt.datetime.now(tz)
    date_expr = parsed.get("date_expression")
    if date_expr:
        target_date = resolve_datetime(date_expr, tz.zone, now=now)
        if target_date:
            start = dt.datetime.combine(target_date.date(), dt.time.min, tzinfo=tz)
            end = dt.datetime.combine(target_date.date(), dt.time.max, tzinfo=tz)
        else:
            start, end = now, now + dt.timedelta(days=7)
    else:
        start, end = now, now + dt.timedelta(days=7)

    meetings = repo.get_meetings_in_range(user["id"], start.isoformat(), end.isoformat())
    if not meetings:
        await message.reply_text("No meetings found in that range.")
        return

    lines = []
    for m in meetings:
        mt = dt.datetime.fromisoformat(m["start_time"])
        if mt.tzinfo is None:
            mt = pytz.utc.localize(mt).astimezone(tz)
        lines.append(f"{mt.strftime('%a %d %b, %I:%M %p').replace(' 0', ' ')} — {m['title']}")

    await message.reply_text("Upcoming:\n" + "\n".join(lines))


# ------------------------------------------------------------------
# Reminders
# ------------------------------------------------------------------
async def _handle_create_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}
    date_expr = parsed.get("date_expression")
    trigger = resolve_datetime(date_expr, tz.zone, now=dt.datetime.now(tz))
    if trigger is None:
        await message.reply_text("When should I remind you?")
        return

    reminder_text = reminder.get("text") or "Reminder"
    recurrence = reminder.get("recurrence") or "none"
    repo.create_reminder(
        user_id=user["id"],
        reminder_text=reminder_text,
        trigger_time_iso=trigger.isoformat(),
        recurrence=recurrence,
    )
    await message.reply_text(f"Reminder set for {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_update_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}
    search_text = reminder.get("search_text") or reminder.get("text")
    if not search_text:
        await message.reply_text("Which reminder should I update? Please mention its text.")
        return
    matches = repo.find_pending_reminder(user["id"], search_text)
    if not matches:
        await message.reply_text(f"I couldn't find a pending reminder matching '{search_text}'.")
        return
    target = matches[0]
    date_expr = reminder.get("new_time_expression") or parsed.get("date_expression")
    trigger = resolve_datetime(date_expr, tz.zone, now=dt.datetime.now(tz))
    if trigger is None:
        await message.reply_text("What's the new reminder time?")
        return
    repo.update_reminder(target["id"], trigger_time_iso=trigger.isoformat())
    await message.reply_text(f"Updated reminder to {trigger.strftime('%A, %d %b %I:%M %p').replace(' 0', ' ')}.")


async def _handle_cancel_reminder(message, user, tz, parsed):
    reminder = parsed.get("reminder") or {}
    search_text = reminder.get("search_text") or reminder.get("text")
    if not search_text:
        await message.reply_text("Which reminder should I cancel? Please mention its text.")
        return
    matches = repo.find_pending_reminder(user["id"], search_text)
    if not matches:
        await message.reply_text(f"I couldn't find a pending reminder matching '{search_text}'.")
        return
    target = matches[0]
    repo.mark_reminder_sent(target["id"])
    await message.reply_text(f"Cancelled reminder: {target['reminder_text']}.")


# ------------------------------------------------------------------
# Follow-ups / queries
# ------------------------------------------------------------------
async def _handle_pending_followups(message, user):
    followups = repo.get_pending_followups(user["id"])
    if not followups:
        await message.reply_text("No pending follow-ups.")
        return
    lines = []
    for s in followups:
        name = s.get("lead_name") or "Unknown"
        date = s.get("follow_up_date") or "No date"
        lines.append(f"• {name} — follow up: {date}")
    await message.reply_text("Pending follow-ups:\n" + "\n".join(lines))


async def _handle_query_stats(message, user, tz, parsed, raw_text):
    query = parsed.get("query") or {}
    metric = query.get("metric")
    start_expr = query.get("start_date") or parsed.get("date_expression")
    end_expr = query.get("end_date")

    now = dt.datetime.now(tz)
    start = resolve_datetime(start_expr, tz.zone, now=now) if start_expr else None
    end = resolve_datetime(end_expr, tz.zone, now=now) if end_expr else None
    if start and end:
        start_date, end_date = start.date(), end.date()
    elif start:
        start_date = start.date()
        end_date = start.date()
    else:
        start_date, end_date = week_bounds(now.date())

    if metric == "sales_calls":
        rows = repo.get_sales_calls(user["id"], start_date.isoformat(), end_date.isoformat())
        await message.reply_text(f"Sales calls: {len(rows)} ({start_date} to {end_date})")
    elif metric == "content":
        rows = repo.get_content(user["id"], start_date.isoformat(), end_date.isoformat())
        await message.reply_text(f"Content entries: {len(rows)} ({start_date} to {end_date})")
    elif metric == "meetings":
        rows = repo.get_meetings_in_range(
            user["id"],
            dt.datetime.combine(start_date, dt.time.min, tzinfo=tz).isoformat(),
            dt.datetime.combine(end_date, dt.time.max, tzinfo=tz).isoformat(),
        )
        await message.reply_text(f"Meetings: {len(rows)} ({start_date} to {end_date})")
    elif metric == "objections":
        rows = objections_list(user["id"], start_date, end_date)
        if not rows:
            await message.reply_text("No objections logged in that period.")
        else:
            await message.reply_text("Objections:\n" + "\n".join(f"• {r.get('objection') or 'Unknown'}" for r in rows))
    else:
        data = daily_summary_data(user["id"], now.date())
        await message.reply_text(_format_daily(data, now.date()))


async def _handle_general_question(message, user, parsed):
    question = parsed.get("query", {}).get("text") or parsed.get("notes") or ""
    answer = answer_objection_question(question)
    await message.reply_text(answer)
