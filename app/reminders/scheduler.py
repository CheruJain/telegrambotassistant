"""Internal reminder + daily digest + weekly-report scheduler."""
from __future__ import annotations
import asyncio
import datetime as dt
import json
import logging
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from telegram.error import TelegramError
from app.config.settings import settings
from app.database import repository as repo
from app.ai.parser import next_recurrence_time
from app.integrations.whatsapp import whatsapp
from app.reminders.whatsapp_call_reminders import ensure_whatsapp_call_reminders
from app.reports.weekly import format_weekly_report
from app.bot.social_tracking import send_daily_checkin, follower_prompt
logger=logging.getLogger(__name__)
class ReminderScheduler:
    def __init__(self,bot:Bot,chat_id:int,user_id:str,tz_name:str):
        self.bot=bot;self.chat_id=chat_id;self.user_id=user_id;self.tz=pytz.timezone(tz_name);self.scheduler=AsyncIOScheduler(timezone=self.tz)
    def start(self):
        self.scheduler.add_job(self._check_due_reminders,"interval",seconds=settings.SCHEDULER_POLL_SECONDS,id="reminder_poll",replace_existing=True)
        self.scheduler.add_job(self._send_morning_digest,CronTrigger(hour=6,minute=0,timezone=self.tz),id="morning_digest",replace_existing=True)
        self.scheduler.add_job(self._send_social_checkin,CronTrigger(hour=21,minute=0,timezone=self.tz),id="social_checkin",replace_existing=True)
        self.scheduler.add_job(self._send_evening_digest,CronTrigger(hour=21,minute=30,timezone=self.tz),id="evening_digest",replace_existing=True)
        self.scheduler.add_job(self._send_follower_audit,CronTrigger(day_of_week="sun",hour=11,minute=0,timezone=self.tz),id="social_follower_audit",replace_existing=True)
        day={"mon":"mon","tue":"tue","wed":"wed","thu":"thu","fri":"fri","sat":"sat","sun":"sun"}.get(settings.WEEKLY_REPORT_DAY.lower(),"sun")
        self.scheduler.add_job(self._send_weekly_report,CronTrigger(day_of_week=day,hour=settings.WEEKLY_REPORT_HOUR,minute=settings.WEEKLY_REPORT_MINUTE,timezone=self.tz),id="weekly_report",replace_existing=True)
        self.scheduler.start()
    async def _send_social_checkin(self):
        try: await send_daily_checkin(self.bot,self.chat_id)
        except Exception as e: logger.error("Social check-in failed: %s",e)
    async def _send_follower_audit(self):
        try: await self.bot.send_message(chat_id=self.chat_id,text=follower_prompt(),parse_mode="HTML")
        except Exception as e: logger.error("Follower audit failed: %s",e)
    async def _check_due_reminders(self):
        now=dt.datetime.now(self.tz)
        try:
            repo.ensure_booking_confirmation_reminders(self.user_id,now=now);ensure_whatsapp_call_reminders(self.user_id,now=now);due=repo.get_due_reminders(now.isoformat())
        except Exception as e: logger.error("Failed to fetch due reminders: %s",e);return
        for reminder in due:
            try:
                text=str(reminder.get("reminder_text") or "")
                if text.startswith("__WHATSAPP_TEMPLATE__"):
                    p=json.loads(text[len("__WHATSAPP_TEMPLATE__"):]);sent=whatsapp.send_template(p["recipient"],p["template"],p.get("parameters",[]))
                    if sent: repo.mark_reminder_sent(reminder["id"])
                    continue
                await self.bot.send_message(chat_id=self.chat_id,text=f"Reminder: {text}");repo.mark_reminder_sent(reminder["id"])
                recurrence=reminder.get("recurrence")
                if recurrence and recurrence!="none":
                    trigger=dt.datetime.fromisoformat(reminder["trigger_time"]);repo.reschedule_recurring(reminder,next_recurrence_time(trigger,recurrence).isoformat())
            except TelegramError as e: logger.error("Reminder Telegram error: %s",e)
            except Exception as e: logger.error("Reminder error: %s",e)
    async def _send_morning_digest(self):
        try:
            today=dt.datetime.now(self.tz).date();data=repo.get_daily_digest_data(self.user_id,today.isoformat());await self.bot.send_message(chat_id=self.chat_id,text="GOOD MORNING\n"+str(data))
        except Exception as e: logger.error("Morning digest failed: %s",e)
    async def _send_evening_digest(self):
        try:
            today=dt.datetime.now(self.tz).date();tomorrow=today+dt.timedelta(days=1);data=repo.get_daily_digest_data(self.user_id,today.isoformat());await self.bot.send_message(chat_id=self.chat_id,text=format_weekly_report(self.user_id,today) if False else f"TODAY'S SUMMARY\n{data.get('content',[])}\nTomorrow: {tomorrow}")
        except Exception as e: logger.error("Evening digest failed: %s",e)
    async def _send_weekly_report(self):
        try:
            today=dt.datetime.now(self.tz).date();text=await asyncio.to_thread(format_weekly_report,self.user_id,today);await self.bot.send_message(chat_id=self.chat_id,text=text)
        except Exception as e: logger.error("Weekly report failed: %s",e)
