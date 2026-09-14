"""Entrypoint. Run with: python main.py"""
import asyncio
import logging
import re
import time
import pytz
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from app.config.settings import settings
from app.database import repository as repo
from app.bot import commands as cmd
from app.bot.handlers import handle_text_message, handle_voice_message, handle_reschedule_shortcut
from app.bot.history_handler import handle_history_question
from app.bot.contact_handler import handle_contact_lookup
from app.bot.date_details_handler import handle_date_details
from app.bot.natural_reminder_handler import handle_natural_call_reminder
from app.bot.sales_update_handler import (
    _handle_selection,
    _extract_name,
    _is_cancel_message,
    _is_update_message,
    handle_possible_sales_update,
)
from app.bot.inline_actions import callback_handler
from app.bot.pipeline import pipeline_cmd
from app.bot.social_tracking import backlog_cmd, handle_social_state
from app.reminders.scheduler import ReminderScheduler
from app.reminders.social_scheduler import SocialScheduler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    force=True,
)
logger = logging.getLogger(__name__)


async def _initialize_services(application: Application):
    """Initialize database-backed services with retry instead of crashing startup."""
    if application.bot_data.get("services_initialized"):
        return

    while not application.bot_data.get("services_initialized"):
        try:
            user = await asyncio.to_thread(
                repo.get_or_create_user,
                settings.TELEGRAM_USER_ID,
                settings.USER_NAME,
            )
            tz_name = user.get("timezone") or settings.DEFAULT_TIMEZONE

            if not application.bot_data.get("social_scheduler"):
                social_scheduler = SocialScheduler(
                    application.bot,
                    settings.TELEGRAM_USER_ID,
                    tz_name,
                )
                social_scheduler.start()
                application.bot_data["social_scheduler"] = social_scheduler

            if not application.bot_data.get("scheduler"):
                scheduler = ReminderScheduler(
                    bot=application.bot,
                    chat_id=settings.TELEGRAM_USER_ID,
                    user_id=user["id"],
                    tz_name=tz_name,
                )
                scheduler.start()
                application.bot_data["scheduler"] = scheduler

            application.bot_data["services_initialized"] = True
            logger.info("Database and reminder services initialized successfully")
        except Exception:
            logger.exception(
                "Service initialization failed; retrying in 10 seconds"
            )
            await asyncio.sleep(10)


async def _post_init(application: Application):
    application.create_task(_initialize_services(application), name="initialize_services")


async def _handle_all_text(update, context):
    if await _handle_selection(update, context):
        return
    if await handle_social_state(update, context):
        return
    text = (update.message.text or "").strip()
    if text and update.effective_user.id == settings.TELEGRAM_USER_ID:
        user = repo.get_or_create_user(
            update.effective_user.id,
            update.effective_user.first_name,
        )
        tz = pytz.timezone(user.get("timezone") or settings.DEFAULT_TIMEZONE)
        if await handle_reschedule_shortcut(update.message, user, tz, text):
            return
        if await handle_date_details(update.message, user, text):
            return
        if await handle_contact_lookup(update.message, user, text):
            return
        if await handle_history_question(update.message, user, tz, text):
            return
        if await handle_natural_call_reminder(update.message, user, text):
            return
        name = _extract_name(text)
        is_meeting = bool(re.search(r"\bmeeting\b", text, re.I))
        sales_cancel = _is_cancel_message(text) and not is_meeting
        sales_update = _is_update_message(text)
        if name and (sales_cancel or sales_update):
            await handle_possible_sales_update(update, context)
            return
    await handle_text_message(update, context)


def build_application() -> Application:
    application = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", cmd.start_cmd))
    application.add_handler(CommandHandler("help", cmd.help_cmd))
    application.add_handler(CommandHandler("today", cmd.today_cmd))
    application.add_handler(CommandHandler("summary", cmd.summary_cmd))
    application.add_handler(CommandHandler("meetings", cmd.meetings_cmd))
    application.add_handler(CommandHandler("reminders", cmd.reminders_cmd))
    application.add_handler(CommandHandler("pending", cmd.pending_cmd))
    application.add_handler(CommandHandler("followups", cmd.pending_cmd))
    application.add_handler(CommandHandler("stats", cmd.stats_cmd))
    application.add_handler(CommandHandler("pipeline", pipeline_cmd))
    application.add_handler(CommandHandler("backlog_social", backlog_cmd))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_all_text)
    )
    return application


def main():
    """Run the worker continuously and recover from transient polling failures."""
    restart_delay = 5
    while True:
        app = None
        try:
            logger.info("Starting Telegram AI Assistant (polling)...")
            app = build_application()
            logger.info("Telegram application built successfully; entering polling")
            app.run_polling(allowed_updates=["message", "callback_query"])
            logger.warning("Polling stopped without an exception; restarting")
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
            return
        except Exception:
            logger.exception(
                "Telegram worker stopped unexpectedly; restarting in %s seconds",
                restart_delay,
            )
        finally:
            if app is not None:
                try:
                    app.stop_running()
                except Exception:
                    pass
        time.sleep(restart_delay)
        restart_delay = min(restart_delay * 2, 60)


if __name__ == "__main__":
    main()
