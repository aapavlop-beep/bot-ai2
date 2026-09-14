import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import dotenv_values


class Settings(BaseSettings):
    bot_token: str = ""
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    log_level: str = "INFO"
    app_env: str = "development"
    database_path: str = "data/bot.db"
    timezone: str = "Asia/Yekaterinburg"
    monitoring_interval_seconds: int = Field(default=30, ge=10, le=3600)
    analysis_concurrency: int = Field(default=2, ge=1, le=6)
    live_batch_size: int = Field(default=6, ge=1, le=30)
    ai_timeout_seconds: float = Field(default=90, gt=0, le=600)
    ai_live_timeout_seconds: float = Field(default=20, gt=0, le=120)
    ai_max_retries: int = Field(default=1, ge=0, le=3)
    odds_max_age_seconds: int = Field(default=600, ge=30, le=3600)
    live_odds_max_age_seconds: int = Field(default=90, ge=10, le=300)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )


def recover_openai_key(process_key: str | None, file_key: str | None, bot_token: str) -> str | None:
    """Ignore only a process-level AI key that is exactly the bot token."""
    if (
        process_key
        and process_key.strip() == bot_token.strip()
        and file_key
        and file_key.strip() != bot_token.strip()
    ):
        return file_key.strip()
    return process_key.strip() if process_key else file_key.strip() if file_key else None


settings = Settings()

# pydantic-settings correctly gives real process environment variables
# precedence over `.env`. A common Windows setup mistake in this project was
# persisting the Telegram token as OPENAI_API_KEY, which then masked the valid
# key in `.env` and produced an opaque HTTP 401. Only for this exact collision,
# recover the file value; all other environment overrides remain authoritative.
_dotenv_path = Path(".env")
_dotenv_values = dotenv_values(_dotenv_path) if _dotenv_path.is_file() else {}
_process_ai_key = os.getenv("OPENAI_API_KEY")
_file_ai_key = _dotenv_values.get("OPENAI_API_KEY")
settings.openai_api_key = recover_openai_key(_process_ai_key, _file_ai_key, settings.bot_token)


def configuration_warnings() -> list[str]:
    """Return actionable, non-secret configuration warnings for operators."""
    warnings: list[str] = []
    if settings.openai_api_key and settings.openai_api_key.strip() == settings.bot_token.strip():
        warnings.append(
            "OPENAI_API_KEY совпадает с BOT_TOKEN; проверьте переменные окружения Windows."
        )
    process_key = os.getenv("OPENAI_API_KEY")
    if process_key and process_key.strip() == settings.bot_token.strip() and settings.openai_api_key != process_key:
        warnings.append(
            "OPENAI_API_KEY из окружения совпадал с BOT_TOKEN и был проигнорирован; использовано значение из .env."
        )
    elif process_key and process_key != settings.openai_api_key:
        warnings.append(
            "OPENAI_API_KEY задан в окружении процесса и имеет приоритет над .env."
        )
    if settings.openai_base_url and "api.openai.com" not in settings.openai_base_url:
        warnings.append(
            "Используется сторонний OPENAI_BASE_URL; ключ и модель должны поддерживаться этим endpoint."
        )
    return warnings
