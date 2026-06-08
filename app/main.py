import asyncio
import logging

from app.bot import build_bot, build_dispatcher
from app.config import settings
from app.db import SessionLocal
from app.handlers import register_handlers
from app.services.admins import ensure_owner
from app.services.quiet_hours import load_from_db as load_quiet_hours


async def on_startup() -> None:
    """One-time bootstrap before polling starts.

    Seeds the configured owner into the admins table and primes the
    quiet-hours cache from DB so the first scheduled fire (when
    scheduling is wired) sees the user-configured values rather than
    falling back to env defaults.
    """
    async with SessionLocal() as session:
        await ensure_owner(session, settings.owner_id)
        await load_quiet_hours(session)


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    log = logging.getLogger("ideabottg")

    bot = build_bot()
    dp = build_dispatcher()
    register_handlers(dp)

    await on_startup()

    me = await bot.get_me()
    log.info("Starting bot @%s (id=%s)", me.username, me.id)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
