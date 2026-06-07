import asyncio
import logging

from app.bot import build_bot, build_dispatcher
from app.config import settings
from app.db import SessionLocal
from app.handlers import register_handlers
from app.middlewares import register_middlewares
from app.scheduler import IdeaScheduler
from app.services.admins import ensure_owner


async def on_startup() -> None:
    """One-time bootstrap on bot start."""
    async with SessionLocal() as session:
        await ensure_owner(session, settings.owner_id)


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    log = logging.getLogger("ideabottg")

    bot = build_bot()
    dp = build_dispatcher()
    register_middlewares(dp)
    register_handlers(dp)

    await on_startup()

    scheduler = IdeaScheduler(bot)
    await scheduler.start()

    me = await bot.get_me()
    log.info("Starting bot @%s (id=%s)", me.username, me.id)

    try:
        await dp.start_polling(
            bot,
            scheduler=scheduler,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
