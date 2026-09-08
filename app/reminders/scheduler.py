"""
Internal reminder + weekly-report scheduler.

Runs entirely inside this process using APScheduler:
  1. A polling job every SCHEDULER_POLL_SECONDS checks the `reminders` table
     for anything due and sends it via Telegram.
  2. A daily 6 PM job sends the next day's booked sales calls as one
     consolidated confirmation list.
  3. A weekly cron job generates and sends the automatic weekly report.

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
            "Scheduler started: polling every %ss, tomorrow booked calls at 18:00 %s, weekly report on %s %02d:%02d %s",
            settings.SCHEDULER_POLL_SECONDS,
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
                if time_value:
                    try:
                        time_label = dt.datetime.strptime(
                            str(time_value)[:8], "%H:%M:%S"
                        ).strftime("%I:%M %p").lstrip("0")
                    except ValueError:
                        time_label = str(time_value)
                else:
                    time_label = "Time not provided"

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
