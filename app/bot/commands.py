"""
Deterministic slash-commands with clean Telegram HTML output and inline actions.
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
from app.bot.inline_actions import action_keyboard


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


def _date_group_label(value: dt.date, today: dt.date) -> str:
    if value == today:
        return f"📅  TODAY • {value.strftime('%d %b')}"
    if value == today + dt.timedelta(days=1):
        return f"📅  TOMORROW • {value.strftime('%d %b')}"
    return f"📅  {value.strftime('%d %b %Y')}"


def _clean_reminder(text: str):
    value = re.sub(r"^Confirm attendance:\s*|^Reminder:\s*", "", text or "", flags=re.I).strip()
    value = re.sub(r"\nPhone:\s*\+?[\d\s()-]+\s*$", "", value, flags=re.I).strip()
    match = re.search(r"sales call at\s+(\d{1,2}:\d{2}\s*(?:AM|PM))", value, re.I)
    if match:
        name = re.split(r"\s*[—-]?\s*sales call at\s+", value, flags=re.I)[0].strip(" —-")
        return name or value, f"Sales call at {match.group(1).upper()}"
    return value, None


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    await update.message.reply_text(f"Hey {user.get('name') or ''}! I'm your personal work & sales assistant.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Commands:\n/start\n/help\n/today\n/summary\n/meetings\n/reminders\n/pending\n/followups\n/stats")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    data = daily_summary_data(user["id"], today)
    await update.message.reply_text(_format_daily(data, today), parse_mode="HTML")


def _format_daily(data: dict, today: dt.date) -> str:
    c, s = data["content"], data["sales"]
    lines = [f"<b>⚡️ DAILY SNAPSHOT • {today.strftime('%d %b')}</b>", "━━━━━━━━━━━━━━━━━━━━", "", "<b>🤝  MEETINGS</b>"]
    if data["meetings"]:
        tz = pytz.timezone(settings.DEFAULT_TIMEZONE)
        for m in data["meetings"]:
            start = _local_dt(m["start_time"], tz)
            lines.append(f"• <b>{_fmt_time(start)}</b> ── {_safe(m.get('title') or 'Meeting')}")
    else:
        lines.append("• No meetings today")
    lines.extend(["", "<b>📊  SALES PIPELINE</b>", f"• Calls: {s['total_calls']}  |  Won: {s['won']}  |  Follow-ups: {s['follow_ups']}", "", "<b>📝  CONTENT</b>"])
    if c["by_platform_account"]:
        lines.extend(f"• {_safe(k)}: {v}" for k, v in c["by_platform_account"].items())
    else:
        lines.append("• No logs today")
    lines.extend(["", "<b>⏳  PENDING</b>", f"• {len(data['pending_reminders'])} Reminders in queue", "━━━━━━━━━━━━━━━━━━━━"])
    return "\n".join(lines)


async def summary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    await update.message.reply_text("Crunching this week's numbers…")
    await update.message.reply_text(format_weekly_report(user["id"], dt.datetime.now(tz).date()))


async def meetings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    now = dt.datetime.now(tz)
    meetings = repo.get_meetings_in_range(user["id"], now.isoformat(), (now + dt.timedelta(days=7)).isoformat())
    if not meetings:
        await update.message.reply_text("No upcoming meetings in the next 7 days.")
        return
    booked = {}
    for call in repo.get_sales_calls(user["id"], "2000-01-01", "2100-12-31"):
        if call.get("outcome") == "Booked" and call.get("lead_name") and call.get("phone_number"):
            booked.setdefault((call.get("booked_date"), str(call.get("booked_time") or "")[:5]), []).append(call)
    groups = {}
    for meeting in meetings:
        start = _local_dt(meeting["start_time"], tz)
        title = (meeting.get("title") or "Meeting").strip()
        person = (meeting.get("person") or "").strip()
        display = title if not person or person.lower() in title.lower() else f"{title} with {person}"
        phones = []
        for call in booked.get((start.date().isoformat(), start.strftime("%H:%M")), []):
            lead = (call.get("lead_name") or "").lower()
            if not person or person.lower() in lead or lead in person.lower():
                phone = str(call.get("phone_number") or "").strip()
                if phone and phone not in phones:
                    phones.append(phone)
        groups.setdefault(start.date(), []).append((start, display, phones))
    lines = ["<b>🤝  SCHEDULED MEETINGS</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    for date_value in sorted(groups):
        lines.append(f"<b>{_date_group_label(date_value, now.date())}</b>")
        for start, display, phones in sorted(groups[date_value], key=lambda x: x[0]):
            lines.append(f"• <b>{_fmt_time(start)}</b> ── {_safe(display)}")
            if phones:
                lines.append("  📞 " + " | ".join(f"<code>{_safe(p)}</code>" for p in phones))
            lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    for date_value in sorted(groups):
        for start, display, phones in sorted(groups[date_value], key=lambda x: x[0]):
            if phones:
                await update.message.reply_text(f"<b>{_safe(_fmt_time(start))} ── {_safe(display)}</b>", parse_mode="HTML", reply_markup=action_keyboard(phones[0]))


async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    repo.ensure_booking_confirmation_reminders(user["id"])
    reminders = repo.get_pending_reminders(user["id"])
    if not reminders:
        await update.message.reply_text("No pending reminders.")
        return
    booked = [c for c in repo.get_sales_calls(user["id"], "2000-01-01", "2100-12-31") if c.get("outcome") == "Booked" and c.get("lead_name") and c.get("phone_number")]
    now = dt.datetime.now(tz)
    grouped = {}
    for reminder in reminders:
        trigger = _local_dt(reminder["trigger_time"], tz)
        raw = reminder.get("reminder_text") or "Reminder"
        phone = None
        lead_matches = [c for c in booked if c["lead_name"].lower() in raw.lower() and c.get("booked_date") == trigger.date().isoformat()]
        if len(lead_matches) == 1:
            phone = str(lead_matches[0].get("phone_number") or "").strip()
        action, sub = _clean_reminder(raw)
        grouped.setdefault(trigger.date(), []).append((trigger, action, sub, phone, reminder["id"]))
    lines = ["<b>🔔  PENDING REMINDERS</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    action_items = []
    for date_value in sorted(grouped):
        lines.append(f"<b>{_date_group_label(date_value, now.date())}</b>")
        for trigger, action, sub, phone, reminder_id in sorted(grouped[date_value], key=lambda x: x[0]):
            lines.append(f"• <b>{_fmt_time(trigger)}</b> ── {_safe(action)}")
            if phone:
                lines.append(f"  📞 <code>{_safe(phone)}</code>")
            if sub:
                lines.append(f"  ↳ {_safe(sub)}")
            action_items.append((trigger, action, phone, reminder_id))
        lines.append("")
    lines.extend(["━━━━━━━━━━━━━━━━━━━━", f"<b>Total: {len(reminders)} reminders queued</b>"])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    for trigger, action, phone, reminder_id in action_items:
        await update.message.reply_text(f"<b>{_fmt_time(trigger)} ── {_safe(action)}</b>", parse_mode="HTML", reply_markup=action_keyboard(phone, reminder_id))


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    rows = repo.get_pending_followups(user["id"])
    if not rows:
        await update.message.reply_text("No pending follow-ups.")
        return
    await update.message.reply_text("<b>PENDING FOLLOW-UPS</b>\n\n" + "\n".join(f"• {_safe(r.get('lead_name') or 'Unknown lead')} — {_safe(r.get('follow_up_date') or '?')}" for r in rows), parse_mode="HTML")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    start, end = week_bounds(today)
    from app.analytics.metrics import sales_summary, content_summary
    s = sales_summary(user["id"], start, end)
    c = content_summary(user["id"], start, end)
    await update.message.reply_text(f"STATS ({start:%d-%b-%Y} → {end:%d-%b-%Y})\n\nCalls: {s['total_calls']} | Interested: {s['interested']} | Won: {s['won']}\nConversion: {s['conversion_rate_pct']}% | Revenue: ₹{s['revenue_won']:,.0f}\nContent posted: {sum(c['by_platform_account'].values())} | Reach: {c['total_reach']}")
