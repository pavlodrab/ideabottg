from urllib.parse import urlparse

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

    # Optional Redis URL for persistent FSM storage. When unset (e.g. in
    # local dev), the bot falls back to in-memory storage which is fine
    # for testing but loses every active FSM state on each restart —
    # that breaks any "click button → bot asks for input → user replies"
    # flow whenever Railway redeploys between the click and the reply.
    redis_url: str | None = None

    # Quiet hours / night mode. These are *initial defaults only* —
    # admins can change them at runtime via /quiet, and the live values
    # are persisted in the `settings` key-value table. Times are HH:MM
    # in the timezone above; the window may wrap midnight.
    quiet_hours_enabled: bool = True
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "08:00"

    @property
    def async_database_url(self) -> str:
        # Railway gives postgres://, SQLAlchemy async needs postgresql+asyncpg://
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def database_url_masked(self) -> str:
        """A safe-to-log version of the database URL: scheme, host, port,
        dbname, masked username (no password)."""
        try:
            parsed = urlparse(self.async_database_url)
        except Exception:  # noqa: BLE001
            return "<unparseable>"
        host = parsed.hostname or "?"
        port = parsed.port or "?"
        dbname = (parsed.path or "/").lstrip("/") or "?"
        user_marker = "user" if parsed.username else "(no user)"
        scheme = parsed.scheme or "?"
        return f"{scheme}://{user_marker}:***@{host}:{port}/{dbname}"


settings = Settings()
