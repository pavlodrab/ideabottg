from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration loaded from environment variables.

    Field names map to env vars case-insensitively, so:
      bot_token   -> BOT_TOKEN
      owner_id    -> OWNER_ID
      database_url-> DATABASE_URL
      tz          -> TZ
      log_level   -> LOG_LEVEL

    A local `.env` file is loaded if present, but is not required —
    on Railway / production the env vars come straight from the service.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    owner_id: int
    database_url: str
    tz: str = "Europe/Kyiv"
    log_level: str = "INFO"

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
