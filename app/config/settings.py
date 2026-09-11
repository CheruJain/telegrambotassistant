"""
Central configuration loader.

All secrets come from environment variables (.env). Non-secret, user-tunable
settings (timezone, weekly report day/time, default reminder offsets, etc.)
have env-var defaults but can be overridden at runtime via the `user_config`
table (see database/repository.py -> get_config/set_config), so the user can
change them from Telegram without touching code.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


class Settings:
    # --- Secrets / required ---
    TELEGRAM_BOT_TOKEN: str = _get_required("TELEGRAM_BOT_TOKEN")
    TELEGRAM_USER_ID: int = int(_get_required("TELEGRAM_USER_ID"))
    AI_API_KEY: str = _get_required("AI_API_KEY")
    SUPABASE_URL: str = _get_required("SUPABASE_URL")
    SUPABASE_KEY: str = _get_required("SUPABASE_KEY")

    # --- Optional WhatsApp Business Cloud API ---
    WHATSAPP_ENABLED: bool = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_GRAPH_API_VERSION: str = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v23.0")

    # --- Tunable defaults (overridable per-user in DB) ---
    AI_MODEL: str = os.getenv("AI_MODEL", "gemini-2.5-flash")
    DEFAULT_TIMEZONE: str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")
    USER_NAME: str = os.getenv("USER_NAME", "there")
    WEEKLY_REPORT_DAY: str = os.getenv("WEEKLY_REPORT_DAY", "sun")  # mon..sun
    WEEKLY_REPORT_HOUR: int = int(os.getenv("WEEKLY_REPORT_HOUR", "19"))
    WEEKLY_REPORT_MINUTE: int = int(os.getenv("WEEKLY_REPORT_MINUTE", "0"))
    DEFAULT_MEETING_REMINDER_MINUTES: int = int(
        os.getenv("DEFAULT_MEETING_REMINDER_MINUTES", "30")
    )
    SCHEDULER_POLL_SECONDS: int = int(os.getenv("SCHEDULER_POLL_SECONDS", "30"))


settings = Settings()
