from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(alias="BOT_TOKEN")
    owner_id: int = Field(alias="OWNER_ID")
    database_url: str = Field(alias="DATABASE_URL")
    tz: str = Field(default="Europe/Kyiv", alias="TZ")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Quiet hours / night mode. Bot suppresses *proactive* messages
    # (scheduled prompts, broadcasts, reminders) during this window.
    # Times are HH:MM in the timezone above; window may wrap midnight.
    quiet_hours_enabled: bool = Field(default=True, alias="QUIET_HOURS_ENABLED")
    quiet_hours_start: str = Field(default="23:00", alias="QUIET_HOURS_START")
    quiet_hours_end: str = Field(default="08:00", alias="QUIET_HOURS_END")

    @property
    def async_database_url(self) -> str:
        # Railway gives postgres://, SQLAlchemy async needs postgresql+asyncpg://
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
