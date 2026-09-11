"""Dedicated scheduler for social-media check-ins and follower audits."""
from __future__ import annotations
import logging
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot
from app.bot.social_tracking import send_daily_checkin, follower_prompt
logger=logging.getLogger(__name__)
class SocialScheduler:
    def __init__(self,bot:Bot,chat_id:int,tz_name:str):
        self.bot=bot;self.chat_id=chat_id;self.tz=pytz.timezone(tz_name);self.scheduler=AsyncIOScheduler(timezone=self.tz)
    def start(self):
        self.scheduler.add_job(self._daily,CronTrigger(hour=21,minute=0,timezone=self.tz),id="social_daily_checkin",replace_existing=True)
        self.scheduler.add_job(self._weekly,CronTrigger(day_of_week="sun",hour=11,minute=0,timezone=self.tz),id="social_weekly_followers",replace_existing=True)
        self.scheduler.start()
    async def _daily(self):
        try: await send_daily_checkin(self.bot,self.chat_id)
        except Exception: logger.exception("Social daily check-in failed")
    async def _weekly(self):
        try: await self.bot.send_message(chat_id=self.chat_id,text=follower_prompt(),parse_mode="HTML")
        except Exception: logger.exception("Social follower audit failed")
