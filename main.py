"""
Entrypoint. Run with: python main.py

Starts:
  - The Telegram bot (long polling) with all command + message handlers.
  - The internal reminder/weekly-report scheduler (app/reminders/scheduler.py).

No Google Calendar, Notion, or Google Sheets are used anywhere in this file
or anything it imports.
"""
import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.config.settings import settings
from app.database import repository as repo
from app.bot import commands as cmd
from app.bot.handlers import handle_text_message, handle_voice_message
from app.bot.sales_update_handler import handle_possible_sales_update
from app.reminders.scheduler import ReminderScheduler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


async def _post_init(application: Application):
    user = repo.get_or_create_user(settings.TELEGRAM_USER_ID, settings.USER_NAME)
    tz_name = user.get("timezone") or settings.DEFAULT_TIMEZONE

    scheduler = ReminderScheduler(
        bot=application.bot,
        chat_id=settings.TELEGRAM_USER_ID,
        user_id=user["id"],
        tz_name=tz_name,
    )
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    logger.info("Assistant ready for user %s", settings.TELEGRAM_USER_ID)


def build_application() -> Application:
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).post_init(_post_init).build()

    application.add_handler(CommandHandler("start", cmd.start_cmd))
    application.add_handler(CommandHandler("help", cmd.help_cmd))
    application.add_handler(CommandHandler("today", cmd.today_cmd))
    application.add_handler(CommandHandler("summary", cmd.summary_cmd))
    application.add_handler(CommandHandler("meetings", cmd.meetings_cmd))
    application.add_handler(CommandHandler("reminders", cmd.reminders_cmd))
    application.add_handler(CommandHandler("pending", cmd.pending_cmd))
    application.add_handler(CommandHandler("followups", cmd.pending_cmd))
    application.add_handler(CommandHandler("stats", cmd.stats_cmd))

    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    # Let the sales update handler also receive follow-up selection replies such as "1".
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_possible_sales_update))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    return application


def main():
    app = build_application()
    logger.info("Starting Telegram AI Assistant (polling)...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
