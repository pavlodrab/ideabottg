import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from app.config import settings

log = logging.getLogger(__name__)


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    storage = _build_storage()
    return Dispatcher(storage=storage)


def _build_storage() -> BaseStorage:
    """Pick FSM storage based on whether Redis is configured.

    On Railway the Redis plugin injects ``REDIS_URL`` into the service
    env, so production picks up the persistent backend automatically.
    For local dev without Redis we fall back to :class:`MemoryStorage`
    — convenient for one-off testing, but state is lost on every
    process restart, which makes multi-step admin flows (e.g. setting
    custom prompt text or quiet-hours window) flaky if the bot
    redeploys mid-flow.
    """
    if settings.redis_url:
        log.info("FSM storage: Redis (%s)", _mask_redis_url(settings.redis_url))
        return RedisStorage.from_url(settings.redis_url)

    log.warning(
        "FSM storage: in-memory (REDIS_URL not set). State will be lost "
        "on every restart — admin flows that span more than one message "
        "may break unexpectedly."
    )
    return MemoryStorage()


def _mask_redis_url(url: str) -> str:
    """Strip credentials from a redis:// URL so it's safe to log."""
    # rediss?://[user:pass@]host[:port][/db]
    try:
        scheme, rest = url.split("://", 1)
    except ValueError:
        return "<unparseable>"
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{scheme}://***@{rest}"
