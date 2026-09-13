from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    log_level: str = "INFO"
    app_env: str = "development"
    database_path: str = "data/bot.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
