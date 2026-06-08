from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.db import SessionLocal
from app.middlewares.db import DbSessionMiddleware


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # `update.middleware` covers messages, callback queries and other
    # update types in one place — every handler receives `session`.
    dp.update.middleware(DbSessionMiddleware(SessionLocal))
    return dp
