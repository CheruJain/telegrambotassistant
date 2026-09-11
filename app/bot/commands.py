"""
Deterministic slash-commands. These never call the AI.
"""
from __future__ import annotations

import datetime as dt
import html
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


def _fmt_time(value: dt.datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _safe(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


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
    await update.message.reply_text(_format_daily(data, today), parse_mode="HTML")


def _format_daily(data: dict, today: dt.date) -> str:
    c, s = data["content"], data["sales"]
    lines = [
        f"<b>⚡️ DAILY SNAPSHOT • {today.strftime('%d %b')}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>🤝  MEETINGS</b>",
    ]

    if data["meetings"]:
        tz = pytz.timezone(settings.DEFAULT_TIMEZONE)
        for m in data["meetings"]:
            start = dt.datetime.fromisoformat(m["start_time"])
            if start.tzinfo is None:
                start = pytz.utc.localize(start)
            start = start.astimezone(tz)
            lines.append(f"• <b>{_fmt_time(start)}</b> ── {_safe(m.get('title') or 'Meeting')}")
    else:
        lines.append("• No meetings today")

    lines.extend(["", "<b>📊  SALES PIPELINE</b>"])
    lines.append(
        f"• Calls: {s['total_calls']}  |  Won: {s['won']}  |  Follow-ups: {s['follow_ups']}"
    )

    lines.extend(["", "<b>📝  CONTENT</b>"])
    if c["by_platform_account"]:
        for k, v in c["by_platform_account"].items():
            lines.append(f"• {_safe(k)}: {v}")
    else:
        lines.append("• No logs today")

    lines.extend([
        "",
        "<b>⏳  PENDING</b>",
        f"• {len(data['pending_reminders'])} Reminders in queue",
        "━━━━━━━━━━━━━━━━━━━━",
    ])
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
        time_label = _fmt_time(start)
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


def _clean_reminder_text(text: str) -> tuple[str, str | None, str | None]:
    """Return action text, optional lead/name, and optional sales-call time."""
    value = re.sub(r"^Confirm attendance:\s*", "", text, flags=re.I).strip()
    value = re.sub(r"^Reminder:\s*", "", value, flags=re.I).strip()
    value = re.sub(r"\nPhone:\s*\+?[\d\s()-]+\s*$", "", value, flags=re.I).strip()

    call_match = re.search(r"sales call at\s+(\d{1,2}:\d{2}\s*(?:AM|PM))", value, re.I)
    call_time = call_match.group(1).upper() if call_match else None

    name = None
    if "sales call at" in value.lower():
        prefix = re.split(r"\s*[—-]?\s*sales call at\s+", value, flags=re.I)[0].strip(" —-")
        if prefix:
            name = prefix
            action = f"Sales call at {call_time}" if call_time else "Sales call"
        else:
            action = value
    else:
        action = value

    return action, name, call_time


def _reminder_group_label(date_value: dt.date, today: dt.date) -> str:
    if date_value == today:
        return f"📅  TODAY • {date_value.strftime('%d %b')}"
    if date_value == today + dt.timedelta(days=1):
        return f"📅  TOMORROW • {date_value.strftime('%d %b')}"
    return f"📅  {date_value.strftime('%d %b %Y')}"


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

    now = dt.datetime.now(tz)
    today = now.date()
    grouped: dict[dt.date, list[str]] = {}

    for reminder in reminders:
        trigger = _local_dt(reminder["trigger_time"], tz)
        raw_text = reminder.get("reminder_text") or "Reminder"
        reminder_text = raw_text
        phone = None

        if "phone:" not in reminder_text.lower():
            lead_matches = [
                c for c in booked_calls
                if c["lead_name"].lower() in reminder_text.lower()
                and c.get("booked_date") == trigger.date().isoformat()
            ]
            time_match = re.search(r"sales call at (\d{1,2}:\d{2})\s*(AM|PM)", reminder_text, re.I)
            if time_match:
                try:
                    parsed_time = dt.datetime.strptime(
                        f"{time_match.group(1)} {time_match.group(2).upper()}", "%I:%M %p"
                    ).time()
                    exact_matches = [
                        c for c in lead_matches
                        if c.get("booked_time") and str(c["booked_time"])[:5] == parsed_time.strftime("%H:%M")
                    ]
                    if exact_matches:
                        lead_matches = exact_matches
                except ValueError:
                    pass
            if len(lead_matches) == 1:
                phone = str(lead_matches[0]["phone_number"]).strip()
        else:
            phone_match = re.search(r"phone:\s*(\+?[\d\s()-]+)", reminder_text, re.I)
            if phone_match:
                phone = phone_match.group(1).strip()

        action, name, call_time = _clean_reminder_text(reminder_text)
        if name:
            display_name = name
        else:
            display_name = action

        entry = f"• <b>{_fmt_time(trigger)}</b> ── {_safe(display_name)}"
        if phone:
            entry += f"\n  📞 <code>{_safe(phone)}</code>"
        if name and call_time:
            entry += f"\n  ↳ Sales call at {_safe(call_time)}"
        elif action and action != display_name:
            entry += f"\n  ↳ {_safe(action)}"
        elif action and not name:
            entry = f"• <b>{_fmt_time(trigger)}</b> ── {_safe(action)}"

        grouped.setdefault(trigger.date(), []).append(entry)

    lines = [
        "<b>🔔  PENDING REMINDERS</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for date_value in sorted(grouped):
        lines.append(f"<b>{_reminder_group_label(date_value, today)}</b>")
        lines.extend(grouped[date_value])
        lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        f"<b>Total: {len(reminders)} reminders queued</b>",
    ])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


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
