"""
Deterministic slash-commands. These never call the AI (spec section 28) -
they just query the DB and format a response.
"""
from __future__ import annotations

import datetime as dt

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


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    await update.message.reply_text(
        f"Hey {user.get('name') or ''}! I'm your personal work & sales assistant.\n\n"
        "Just talk to me naturally, e.g.:\n"
        "• \"Aaj 40 sales calls hui, 7 interested\"\n"
        "• \"Kal 4 baje Rahul ke saath call hai\"\n"
        "• \"LinkedIn pe ek post kari\"\n\n"
        "Or use /help to see all commands."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Commands:\n"
        "/today - today's summary\n"
        "/summary - this week's performance + AI insights\n"
        "/meetings - upcoming meetings\n"
        "/pending - pending follow-ups\n"
        "/followups - same as /pending\n"
        "/stats - quick stats for this week\n\n"
        "Otherwise, just type naturally in English/Hindi/Hinglish - I'll understand:\n"
        "logging work, content, sales calls, creating/updating/cancelling meetings and "
        "reminders, and asking questions about your performance."
    )


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    data = daily_summary_data(user["id"], today)
    await update.message.reply_text(_format_daily(data, today))


def _format_daily(data: dict, today: dt.date) -> str:
    c, s = data["content"], data["sales"]
    lines = [f"📅 TODAY ({today.isoformat()})", ""]
    lines.append("Content:")
    if c["by_platform_account"]:
        for k, v in c["by_platform_account"].items():
            lines.append(f"  • {k}: {v}")
    else:
        lines.append("  • Nothing logged yet")
    lines.append("")
    lines.append("Sales:")
    lines.append(f"  • Calls: {s['total_calls']}  Interested: {s['interested']}  "
                  f"Follow-ups: {s['follow_ups']}  Won: {s['won']}")
    lines.append("")
    lines.append("Meetings:")
    if data["meetings"]:
        for m in data["meetings"]:
            t = dt.datetime.fromisoformat(m["start_time"]).strftime("%I:%M %p").lstrip("0")
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
    text = format_weekly_report(user["id"], today)
    await update.message.reply_text(text)


async def meetings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    now = dt.datetime.now(tz)
    end = now + dt.timedelta(days=7)
    meetings = repo.get_meetings_in_range(user["id"], now.isoformat(), end.isoformat())
    if not meetings:
        await update.message.reply_text("No upcoming meetings in the next 7 days.")
        return
    lines = ["📅 UPCOMING MEETINGS (next 7 days)", ""]
    current_day = None
    for m in meetings:
        start = dt.datetime.fromisoformat(m["start_time"])
        if start.tzinfo is None:
            start = pytz.utc.localize(start).astimezone(tz)
        day_label = start.strftime("%A, %d %b")
        if day_label != current_day:
            lines.append(f"\n{day_label}")
            current_day = day_label
        time_label = start.strftime("%I:%M %p").lstrip("0")
        who = f" with {m['person']}" if m.get("person") else ""
        lines.append(f"  {time_label} — {m['title']}{who}")
    await update.message.reply_text("\n".join(lines))


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    followups = repo.get_pending_followups(user["id"])
    if not followups:
        await update.message.reply_text("No pending follow-ups. 🎉")
        return
    lines = ["📋 PENDING FOLLOW-UPS", ""]
    for f in followups:
        lines.append(
            f"  • {f.get('lead_name') or 'Unknown lead'} — "
            f"follow-up on {f.get('follow_up_date') or '?'} "
            f"(source: {f.get('source') or '?'}, status: {f.get('outcome') or '?'}"
            f"{', ₹%s' % f['deal_value'] if f.get('deal_value') else ''})"
        )
    await update.message.reply_text("\n".join(lines))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update)
    tz = _tz(user)
    today = dt.datetime.now(tz).date()
    start, end = week_bounds(today)
    from app.analytics.metrics import sales_summary, content_summary

    s = sales_summary(user["id"], start, end)
    c = content_summary(user["id"], start, end)
    lines = [
        f"📈 STATS ({start.isoformat()} → {end.isoformat()})",
        "",
        f"Calls: {s['total_calls']}  |  Interested: {s['interested']}  |  Won: {s['won']}",
        f"Conversion: {s['conversion_rate_pct']}%  |  Revenue: ₹{s['revenue_won']:,.0f}",
        f"Content posted: {sum(c['by_platform_account'].values())}  |  Reach: {c['total_reach']}",
    ]
    await update.message.reply_text("\n".join(lines))
