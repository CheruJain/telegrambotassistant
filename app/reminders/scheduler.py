"""
Internal reminder + daily digest + weekly-report scheduler.

Runs entirely inside this process using APScheduler:
  1. A polling job every SCHEDULER_POLL_SECONDS checks the `reminders` table
     for anything due and sends it via Telegram.
  2. Every day at 6 AM, sends today's newly-added-after-9-PM tasks/entries.
  3. Every day at 9 PM, sends today's activity summary plus tomorrow's tasks.
  4. A daily 6 PM job sends the next day's booked sales calls as one
     consolidated confirmation list.
  5. A weekly cron job generates and sends the automatic weekly report.

This keeps running as long as the process is alive, independent of whether
the user is actively chatting.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from telegram.error import TelegramError

from app.config.settings import settings
from app.database import repository as repo
from app.ai.parser import next_recurrence_time
from app.reports.weekly import format_weekly_report

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, bot: Bot, chat_id: int, user_id: str, tz_name: str):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.tz = pytz.timezone(tz_name)
        self.scheduler = AsyncIOScheduler(timezone=self.tz)

    def start(self):
        self.scheduler.add_job(
            self._check_due_reminders,
            "interval",
            seconds=settings.SCHEDULER_POLL_SECONDS,
            id="reminder_poll",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._send_morning_digest,
            CronTrigger(hour=6, minute=0, timezone=self.tz),
            id="morning_digest",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._send_evening_digest,
            CronTrigger(hour=21, minute=0, timezone=self.tz),
            id="evening_digest",
            replace_existing=True,
        )

        # Every day at 6 PM, send one consolidated list of tomorrow's booked calls.
        self.scheduler.add_job(
            self._send_tomorrow_booked_calls,
            CronTrigger(hour=18, minute=0, timezone=self.tz),
            id="tomorrow_booked_calls",
            replace_existing=True,
        )

        day_map = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
                   "fri": "fri", "sat": "sat", "sun": "sun"}
        day = day_map.get(settings.WEEKLY_REPORT_DAY.lower(), "sun")
        self.scheduler.add_job(
            self._send_weekly_report,
            CronTrigger(
                day_of_week=day,
                hour=settings.WEEKLY_REPORT_HOUR,
                minute=settings.WEEKLY_REPORT_MINUTE,
                timezone=self.tz,
            ),
            id="weekly_report",
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(
            "Scheduler started: polling every %ss, morning digest 06:00 %s, evening digest 21:00 %s, tomorrow booked calls 18:00 %s, weekly report on %s %02d:%02d %s",
            settings.SCHEDULER_POLL_SECONDS,
            self.tz,
            self.tz,
            self.tz,
            day,
            settings.WEEKLY_REPORT_HOUR,
            settings.WEEKLY_REPORT_MINUTE,
            self.tz,
        )

    async def _check_due_reminders(self):
        now = dt.datetime.now(self.tz)
        try:
            due = repo.get_due_reminders(now.isoformat())
        except Exception as e:
            logger.error("Failed to fetch due reminders: %s", e)
            return

        for reminder in due:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id, text=f"Reminder: {reminder['reminder_text']}"
                )
                repo.mark_reminder_sent(reminder["id"])

                recurrence = reminder.get("recurrence")
                if recurrence and recurrence != "none":
                    trigger = dt.datetime.fromisoformat(reminder["trigger_time"])
                    next_time = next_recurrence_time(trigger, recurrence)
                    repo.reschedule_recurring(reminder, next_time.isoformat())
            except TelegramError as e:
                logger.error("Failed to send reminder %s: %s", reminder.get("id"), e)
            except Exception as e:
                logger.error("Error processing reminder %s: %s", reminder.get("id"), e)

    @staticmethod
    def _format_time(value) -> str:
        if not value:
            return "Time not provided"
        text = str(value)
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                return dt.datetime.strptime(text[:8], fmt).strftime("%I:%M %p").lstrip("0")
            except ValueError:
                continue
        return text

    @staticmethod
    def _format_digest_section(title: str, rows: list[dict], formatter) -> list[str]:
        lines = [title]
        if not rows:
            lines.append("  None")
            return lines
        for index, row in enumerate(rows, 1):
            lines.append(f"  {index}. {formatter(row)}")
        return lines

    def _build_digest_text(self, target_date: dt.date, data: dict, heading: str) -> str:
        lines = [heading, f"Date: {target_date.strftime('%A, %d %b %Y')}", ""]

        activity = data.get("activity", [])
        if activity:
            lines.append("Work / Activity:")
            for row in activity:
                platform = row.get("platform") or "Other"
                activity_type = row.get("activity_type") or "Work"
                qty = row.get("quantity") or 1
                notes = row.get("notes")
                detail = f"{platform} — {activity_type} — {qty}"
                if notes:
                    detail += f" — {notes}"
                lines.append(f"  • {detail}")
            lines.append("")

        content = data.get("content", [])
        if content:
            lines.append("Content:")
            for row in content:
                platform = row.get("platform") or "Other"
                content_type = row.get("content_type") or "Content"
                title = row.get("title") or row.get("post_title")
                detail = f"{platform} — {content_type}"
                if title:
                    detail += f" — {title}"
                lines.append(f"  • {detail}")
            lines.append("")

        sales = data.get("sales", [])
        if sales:
            lines.append("Sales:")
            for row in sales:
                name = row.get("lead_name") or "Unnamed lead"
                outcome = row.get("outcome") or row.get("call_status") or "Logged"
                phone = row.get("phone_number")
                call_type = row.get("call_type")
                detail = f"{name} — {outcome}"
                if call_type:
                    detail += f" — {call_type}"
                if phone:
                    detail += f" — {phone}"
                lines.append(f"  • {detail}")
            lines.append("")

        meetings = data.get("meetings", [])
        if meetings:
            lines.append("Meetings:")
            for row in meetings:
                start = row.get("start_time")
                try:
                    start_dt = dt.datetime.fromisoformat(str(start))
                    time_label = start_dt.strftime("%I:%M %p").lstrip("0")
                except (TypeError, ValueError):
                    time_label = "Time not provided"
                detail = f"{time_label} — {row.get('title') or 'Meeting'}"
                if row.get("person"):
                    detail += f" — {row['person']}"
                lines.append(f"  • {detail}")
            lines.append("")

        tasks = data.get("tasks", [])
        if tasks:
            lines.append("Tasks:")
            for row in tasks:
                task = row.get("task") or "Task"
                status = row.get("status") or "pending"
                deadline = row.get("deadline")
                target_count = row.get("target_count")
                detail = f"{task} — {status}"
                if deadline:
                    detail += f" — deadline {deadline}"
                if target_count is not None:
                    detail += f" — target {target_count}"
                lines.append(f"  • {detail}")
            lines.append("")

        if len(lines) <= 3:
            lines.append("No entries found.")

        return "\n".join(lines).rstrip()

    async def _send_morning_digest(self):
        try:
            today = dt.datetime.now(self.tz).date()
            yesterday = today - dt.timedelta(days=1)
            cutoff = self.tz.localize(
                dt.datetime.combine(yesterday, dt.time(hour=21, minute=0))
            ).isoformat()
            data = repo.get_daily_digest_data(
                self.user_id,
                today.isoformat(),
                created_after=cutoff,
            )

            # The 6 AM digest is intentionally silent when nothing new was
            # added after 9 PM yesterday. Do not send a "No entries found"
            # message in that case.
            has_new_entries = any(
                data.get(key)
                for key in ("activity", "content", "sales", "meetings", "tasks")
            )
            if not has_new_entries:
                logger.info(
                    "Skipping morning digest for %s: no new entries after 21:00",
                    today.isoformat(),
                )
                return

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=self._build_digest_text(
                    today,
                    data,
                    "Morning task summary",
                ),
            )
        except TelegramError as e:
            logger.error("Failed to send morning digest: %s", e)
        except Exception as e:
            logger.error("Error generating morning digest: %s", e)

    async def _send_evening_digest(self):
        try:
            today = dt.datetime.now(self.tz).date()
            tomorrow = today + dt.timedelta(days=1)
            today_data = repo.get_daily_digest_data(self.user_id, today.isoformat())
            tomorrow_data = repo.get_daily_digest_data(self.user_id, tomorrow.isoformat())

            today_text = self._build_digest_text(
                today,
                today_data,
                "Today's summary",
            )
            tomorrow_tasks = tomorrow_data.get("tasks", [])
            tomorrow_lines = [
                "Tomorrow's tasks",
                f"Date: {tomorrow.strftime('%A, %d %b %Y')}",
                "",
            ]
            if tomorrow_tasks:
                for index, task in enumerate(tomorrow_tasks, 1):
                    detail = task.get("task") or "Task"
                    status = task.get("status") or "pending"
                    deadline = task.get("deadline")
                    target_count = task.get("target_count")
                    detail += f" — {status}"
                    if deadline:
                        detail += f" — deadline {deadline}"
                    if target_count is not None:
                        detail += f" — target {target_count}"
                    tomorrow_lines.append(f"  {index}. {detail}")
            else:
                tomorrow_lines.append("  None")

            await self.bot.send_message(
                chat_id=self.chat_id,
                text=today_text + "\n\n" + "\n".join(tomorrow_lines),
            )
        except TelegramError as e:
            logger.error("Failed to send evening digest: %s", e)
        except Exception as e:
            logger.error("Error generating evening digest: %s", e)

    async def _send_tomorrow_booked_calls(self):
        try:
            tomorrow = dt.datetime.now(self.tz).date() + dt.timedelta(days=1)
            booked = repo.get_booked_sales_calls_for_date(
                self.user_id, tomorrow.isoformat()
            )

            if not booked:
                return

            lines = [f"Tomorrow's booked calls — {tomorrow.strftime('%A, %d %b')}", ""]
            for index, call in enumerate(booked, 1):
                name = call.get("lead_name") or "Unknown"
                phone = call.get("phone_number") or "Number not provided"
                call_type = call.get("call_type") or "Sales call"
                time_value = call.get("booked_time")
                time_label = self._format_time(time_value)
                lines.extend([
                    f"{index}. {name}",
                    f"   {phone}",
                    f"   {call_type} — {time_label}",
                    "",
                ])

            await self.bot.send_message(chat_id=self.chat_id, text="\n".join(lines).rstrip())
        except TelegramError as e:
            logger.error("Failed to send tomorrow's booked calls: %s", e)
        except Exception as e:
            logger.error("Error generating tomorrow's booked calls: %s", e)

    async def _send_weekly_report(self):
        try:
            today = dt.datetime.now(self.tz).date()
            text = await asyncio.to_thread(format_weekly_report, self.user_id, today)
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            logger.error("Failed to send weekly report: %s", e)
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text="I couldn't generate the weekly report this time. Try /summary manually.",
                )
            except TelegramError:
                pass
