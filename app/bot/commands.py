"""
Deterministic slash-commands. These never call the AI.
"""
from __future__ import annotations

import datetime as dt
import re

import pytz
from telegram import Update
from telegram.ext import ContextTypes

from app.database import repository as repo
from app.analytics.metrics import daily_summary_data, week_bounds
from app.reports.weekly import format_weekly_report
from app.config.settings import settings


def _get_user(update: Update):
    return repo.get_or_create_user(update.effective_user.id, update.effective_user.first_name)


def _tz(user: dict) -> pytz.BaseTzInfo:
    return pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)


def _local_dt(value: str, tz):
    start = dt.datetime.fromisoformat(value)
    if start.tzinfo is None:
        start = pytz.utc.localize(start)
    return start.astimezone(tz)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    await update.message.reply_text(
        f"Hey {user.get('name') or ''}! I'm your personal work & sales assistant.\n\n"
        "Just talk to me naturally, e.g.:\n"
        "\"Aaj 40 sales calls hui, 7 interested\"\n"
        "\"Kal 4 baje Rahul ke saath call hai\"\n"
        "\"LinkedIn pe ek post kari\"\n\n"
        "Or use /help to see all commands."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/today - today's summary\n"
        "/summary - this week's performance + AI insights\n"
        "/meetings - upcoming meetings\n"
        "/reminders - pending reminders\n"
        "/pending - pending follow-ups\n"
        "/followups - same as /pending\n"
        "/stats - quick stats for this week"
    )


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    data = daily_summary_data(user["id"], today)
    await update.message.reply_text(_format_daily(data, today))


def _format_daily(data: dict, today: dt.date) -> str:
    c, s = data["content"], data["sales"]
    lines = [f"TODAY ({today.strftime('%d-%b-%Y')})", ""]
    lines.append("Content:")
    if c["by_platform_account"]:
        for k, v in c["by_platform_account"].items():
            lines.append(f"  • {k}: {v}")
    else:
        lines.append("  • Nothing logged yet")
    lines.append("")
    lines.append("Sales:")
    lines.append(f"  • Calls: {s['total_calls']}  Interested: {s['interested']}  Follow-ups: {s['follow_ups']}  Won: {s['won']}")
    lines.append("")
    lines.append("Meetings:")
    if data["meetings"]:
        for m in data["meetings"]:
            start = dt.datetime.fromisoformat(m["start_time"])
            if start.tzinfo is None:
                start = pytz.utc.localize(start)
            start = start.astimezone(pytz.timezone(settings.DEFAULT_TIMEZONE))
            t = start.strftime("%I:%M %p").lstrip("0")
            lines.append(f"  • {t} — {m['title']}")
    else:
        lines.append("  • None remaining today")
    lines.append("")
    lines.append("Pending:")
    lines.append(f"  • Follow-ups: {len(data['pending_followups'])}")
    lines.append(f"  • Reminders queued: {len(data['pending_reminders'])}")
    return "\n".join(lines)


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    await update.message.reply_text("Crunching this week's numbers…")
    await update.message.reply_text(format_weekly_report(user["id"], today))


async def meetings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    now = dt.datetime.now(tz)
    end = now + dt.timedelta(days=7)
    meetings = repo.get_meetings_in_range(user["id"], now.isoformat(), end.isoformat())
    if not meetings:
        await update.message.reply_text("No upcoming meetings in the next 7 days.")
        return

    booked_by_date = {}
    for call in repo.get_sales_calls(user["id"], "2000-01-01", "2100-12-31"):
        if call.get("outcome") != "Booked" or not call.get("lead_name") or not call.get("phone_number"):
            continue
        key = (call.get("booked_date"), str(call.get("booked_time") or "")[:5])
        booked_by_date.setdefault(key, []).append(call)

    lines = ["UPCOMING MEETINGS (next 7 days)", ""]
    current_day = None
    for m in meetings:
        start = _local_dt(m["start_time"], tz)
        day_label = start.strftime("%d-%b-%Y")
        if day_label != current_day:
            lines.append(f"\n{day_label}")
            current_day = day_label
        time_label = start.strftime("%I:%M %p").lstrip("0")
        title = m.get("title") or "Meeting"
        person = (m.get("person") or "").strip()
        if person and person.lower() in title.lower():
            display = title
        elif person:
            display = f"{title} with {person}"
        else:
            display = title
        lines.append(f"  {time_label} — {display}")

        matches = booked_by_date.get((start.date().isoformat(), start.strftime("%H:%M")), [])
        phones = []
        for call in matches:
            lead = (call.get("lead_name") or "").lower()
            if person and (person.lower() in lead or lead in person.lower()):
                phone = str(call.get("phone_number") or "").strip()
                if phone and phone not in phones:
                    phones.append(phone)
        if phones:
            lines.append(f"  Phone: {', '.join(phones)}")
    await update.message.reply_text("\n".join(lines))


async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    repo.ensure_booking_confirmation_reminders(user["id"])
    reminders = repo.get_pending_reminders(user["id"])
    if not reminders:
        await update.message.reply_text("No pending reminders.")
        return

    booked_calls = repo.get_sales_calls(user["id"], "2000-01-01", "2100-12-31")
    booked_calls = [
        c for c in booked_calls
        if c.get("outcome") == "Booked" and c.get("lead_name") and c.get("phone_number")
    ]

    lines = ["PENDING REMINDERS", ""]
    for reminder in reminders:
        trigger = _local_dt(reminder["trigger_time"], tz)
        reminder_text = reminder.get("reminder_text") or "Reminder"

        if "phone:" not in reminder_text.lower():
            lead_matches = [
                c for c in booked_calls
                if c["lead_name"].lower() in reminder_text.lower()
                and c.get("booked_date") == trigger.date().isoformat()
            ]
            time_match = re.search(r"sales call at (\d{1,2}:\d{2})\s*(AM|PM)", reminder_text, re.I)
            if time_match:
                try:
                    parsed_time = dt.datetime.strptime(f"{time_match.group(1)} {time_match.group(2).upper()}", "%I:%M %p").time()
                    exact_matches = [
                        c for c in lead_matches
                        if c.get("booked_time") and str(c["booked_time"])[:5] == parsed_time.strftime("%H:%M")
                    ]
                    if exact_matches:
                        lead_matches = exact_matches
                except ValueError:
                    pass
            if len(lead_matches) == 1:
                reminder_text = f"{reminder_text}\nPhone: {lead_matches[0]['phone_number']}"

        lines.append(f"• {trigger.strftime('%d-%b-%Y')} at {trigger.strftime('%I:%M %p').lstrip('0')} — {reminder_text}")
    await update.message.reply_text("\n".join(lines))


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    followups = repo.get_pending_followups(user["id"])
    if not followups:
        await update.message.reply_text("No pending follow-ups.")
        return
    lines = ["PENDING FOLLOW-UPS", ""]
    for f in followups:
        lines.append(f"  • {f.get('lead_name') or 'Unknown lead'} — follow-up on {f.get('follow_up_date') or '?'} (source: {f.get('source') or '?'}, status: {f.get('outcome') or '?'})")
    await update.message.reply_text("\n".join(lines))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    start, end = week_bounds(today)
    from app.analytics.metrics import sales_summary, content_summary
    s = sales_summary(user["id"], start, end)
    c = content_summary(user["id"], start, end)
    lines = [f"STATS ({start.strftime('%d-%b-%Y')} → {end.strftime('%d-%b-%Y')})", "", f"Calls: {s['total_calls']} | Interested: {s['interested']} | Won: {s['won']}", f"Conversion: {s['conversion_rate_pct']}% | Revenue: ₹{s['revenue_won']:,.0f}", f"Content posted: {sum(c['by_platform_account'].values())} | Reach: {c['total_reach']}"]
    await update.message.reply_text("\n".join(lines))
