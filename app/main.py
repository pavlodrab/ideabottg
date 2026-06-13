import asyncio
import logging
import signal
import sys
import traceback

# Loud, unbuffered prints — these run before logging is configured so we see
# every step in the deploy log even if logging.basicConfig is a no-op.
print(">>> app.main: module imported", flush=True)

from app.bot import build_bot, build_dispatcher
from app.config import settings
from app.db import SessionLocal
from app.handlers import register_handlers
from app.middlewares import register_middlewares
from app.scheduler import IdeaScheduler
from app.services.admins import ensure_owner

print(">>> app.main: imports complete", flush=True)


async def on_startup() -> None:
    """One-time bootstrap on bot start."""
    # Local import to keep on_startup self-contained at module level.
    from app.services.quiet_hours import load_from_db as load_quiet_hours

    async with SessionLocal() as session:
        await ensure_owner(session, settings.owner_id)
        await load_quiet_hours(session)


def _install_signal_logging() -> None:
    """Log any signals we receive — helps catch Railway killing the container."""
    loop = asyncio.get_running_loop()

    def _handler(sig_name: str) -> None:
        print(f">>> app.main: got {sig_name}, shutting down", flush=True)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            loop.add_signal_handler(sig, _handler, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows or restricted env — ignore.
            pass


async def main() -> None:
    print(">>> app.main: main() entered", flush=True)
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        force=True,  # override any earlier logging setup so our handlers win
    )
    log = logging.getLogger("ideabottg")

    log.info("DB target: %s", settings.database_url_masked)

    _install_signal_logging()

    print(">>> app.main: building bot/dispatcher", flush=True)
    bot = build_bot()
    dp = build_dispatcher()
    register_middlewares(dp)
    register_handlers(dp)

    print(">>> app.main: running on_startup (ensure_owner)", flush=True)
    await on_startup()

    print(">>> app.main: starting scheduler", flush=True)
    scheduler = IdeaScheduler(bot)
    await scheduler.start()

    print(">>> app.main: calling bot.get_me", flush=True)
    me = await bot.get_me()
    log.info("Starting bot @%s (id=%s)", me.username, me.id)
    print(f">>> app.main: bot ready @{me.username} ({me.id}); polling now", flush=True)

    try:
        await dp.start_polling(
            bot,
            scheduler=scheduler,
            allowed_updates=dp.resolve_used_update_types(),
        )
        print(">>> app.main: start_polling returned (this is unexpected)", flush=True)
    finally:
        print(">>> app.main: cleanup — scheduler.shutdown + session.close", flush=True)
        await scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    print(">>> app.main: __main__ — entering asyncio.run", flush=True)
    try:
        asyncio.run(main())
        # If we get here, polling exited cleanly — usually means SIGTERM.
        print(">>> app.main: asyncio.run returned (clean exit)", flush=True)
    except Exception:
        print(">>> app.main: FATAL — asyncio.run raised:", flush=True)
        traceback.print_exc()
        sys.exit(1)
