import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from aiogram import Bot

from app.config import settings
from app.db import SessionLocal
from app.models import Admin, Chat
from app.services.chat_messages import (
    RETENTION_DAYS,
    cutoff_for_retention,
    delete_older_than,
)
from app.services.digest import send_digest_to_admin
from app.services.prompts import send_prompt_to_chat
from app.services.quiet_hours import should_send_proactive

log = logging.getLogger(__name__)

PROMPT_PREFIX = "prompt:"
DIGEST_PREFIX = "digest:"
SONG_PREFIX = "song:"
RETENTION_JOB_ID = "retention:chat_messages"

# Cron expression for the chat-messages retention sweep. "5 * * * *"
# runs every hour at xx:05 — staggered slightly off the top of the
# hour so it doesn't collide with prompts that typically schedule on
# the hour exactly.
RETENTION_CRON = "5 * * * *"


def _prompt_job_id(chat_id: int) -> str:
    return f"{PROMPT_PREFIX}{chat_id}"


def _digest_job_id(user_id: int) -> str:
    return f"{DIGEST_PREFIX}{user_id}"


def _song_job_id(chat_id: int) -> str:
    return f"{SONG_PREFIX}{chat_id}"


class IdeaScheduler:
    """APScheduler wrapper that runs three kinds of jobs:

    - prompt jobs: post the idea-collection prompt to a chat on cron.
    - digest jobs: send a per-admin digest of recent ideas on cron.
    - retention sweep: delete chat_messages older than RETENTION_DAYS,
      hourly. Songs are NOT touched.

    Source of truth lives in DB (`chats` and `admins` tables); on startup
    every active row gets a job, and admin/chat mutations call sync_*().

    Quiet hours gate `_send_prompt` and `_send_digest`: if the bot is
    in its night window, the fire is logged-and-skipped without
    advancing the digest watermark, so the next non-quiet fire still
    covers the missed window. The retention sweep ignores quiet hours
    — it's housekeeping, not a user-facing message.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def start(self) -> None:
        async with SessionLocal() as session:
            chat_rows = await session.execute(
                select(Chat).where(
                    Chat.is_active.is_(True),
                    Chat.schedule_cron.is_not(None),
                )
            )
            chats = list(chat_rows.scalars().all())

            admin_rows = await session.execute(
                select(Admin).where(
                    Admin.receive_ideas.is_(True),
                    Admin.delivery_mode == "digest",
                )
            )
            admins = list(admin_rows.scalars().all())

            song_rows = await session.execute(
                select(Chat).where(
                    Chat.is_active.is_(True),
                    Chat.song_enabled.is_(True),
                    Chat.song_cron.is_not(None),
                )
            )
            song_chats = list(song_rows.scalars().all())

        for chat in chats:
            self._schedule_prompt(chat.chat_id, chat.schedule_cron)
        for admin in admins:
            self._schedule_digest(admin.user_id, admin.digest_cron)
        for chat in song_chats:
            self._schedule_song(chat.chat_id, chat.song_cron)

        # F8.3: sweep daily_songs rows left mid-flight by a crash/restart
        # so today's run isn't blocked by a stuck 'generating' row.
        async with SessionLocal() as session:
            from app.services.daily_songs import sweep_stale

            try:
                swept = await sweep_stale(session)
                if swept:
                    log.info("swept %d stale daily_songs row(s)", swept)
            except Exception:  # noqa: BLE001
                log.exception("daily_songs stale sweep failed")

        # Always-on housekeeping: prune chat_messages older than the
        # retention window every hour.
        self._schedule_retention()

        self._scheduler.start()
        log.info(
            "scheduler started with %d prompt + %d digest + %d song "
            "job(s) + retention",
            len(chats),
            len(admins),
            len(song_chats),
        )

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")

    # ---------- chat prompt jobs ----------

    async def sync_chat(self, chat_id: int) -> None:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)

        # Prompt job side.
        if chat is None or not chat.is_active or not chat.schedule_cron:
            self._remove_job(_prompt_job_id(chat_id))
        else:
            self._schedule_prompt(chat.chat_id, chat.schedule_cron)

        # Daily-song job side — independent of the prompt schedule.
        if (
            chat is None
            or not chat.is_active
            or not chat.song_enabled
            or not chat.song_cron
        ):
            self._remove_job(_song_job_id(chat_id))
        else:
            self._schedule_song(chat.chat_id, chat.song_cron)

    def _schedule_prompt(self, chat_id: int, cron: str | None) -> None:
        if not cron:
            self._remove_job(_prompt_job_id(chat_id))
            return
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=settings.tz)
        except ValueError as exc:
            log.warning("invalid cron for chat_id=%s (%r): %s", chat_id, cron, exc)
            self._remove_job(_prompt_job_id(chat_id))
            return

        self._scheduler.add_job(
            self._send_prompt,
            trigger=trigger,
            id=_prompt_job_id(chat_id),
            args=[chat_id],
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled prompt chat_id=%s with cron=%r", chat_id, cron)

    async def _send_prompt(self, chat_id: int) -> None:
        if not should_send_proactive():
            log.info("quiet hours: skipping prompt chat_id=%s", chat_id)
            return
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None:
                self._remove_job(_prompt_job_id(chat_id))
                return
            if not chat.is_active or not chat.schedule_cron:
                self._remove_job(_prompt_job_id(chat_id))
                return
            await send_prompt_to_chat(self.bot, session, chat)

    # ---------- daily-song jobs ----------

    def _schedule_song(self, chat_id: int, cron: str | None) -> None:
        if not cron:
            self._remove_job(_song_job_id(chat_id))
            return
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=settings.tz)
        except ValueError as exc:
            log.warning(
                "invalid song cron for chat_id=%s (%r): %s",
                chat_id,
                cron,
                exc,
            )
            self._remove_job(_song_job_id(chat_id))
            return

        self._scheduler.add_job(
            self._run_song,
            trigger=trigger,
            id=_song_job_id(chat_id),
            args=[chat_id],
            replace_existing=True,
            # Songs take minutes to generate; a generous grace window
            # keeps a missed fire (deploy / restart) from being dropped
            # if it lands within 10 min of the scheduled time.
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled daily-song chat_id=%s with cron=%r", chat_id, cron)

    async def _run_song(self, chat_id: int) -> None:
        # Re-check enablement at fire time; the toggle may have flipped
        # since the job was scheduled. The pipeline also re-checks, but
        # bailing here avoids importing it for a disabled chat.
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None or not chat.is_active or not chat.song_enabled:
                self._remove_job(_song_job_id(chat_id))
                return

        # Quiet hours intentionally NOT applied — the daily song is an
        # explicitly-scheduled, opt-in event, like the spec's 21:00 post.
        from app.services.daily_song import run_daily_song_for_chat

        try:
            await run_daily_song_for_chat(self.bot, chat_id)
        except Exception:  # noqa: BLE001
            log.exception("daily-song job failed for chat_id=%s", chat_id)

    # ---------- digest jobs ----------

    async def sync_admin(self, user_id: int) -> None:
        async with SessionLocal() as session:
            admin = await session.get(Admin, user_id)

        if (
            admin is None
            or not admin.receive_ideas
            or admin.delivery_mode != "digest"
            or not admin.digest_cron
        ):
            self._remove_job(_digest_job_id(user_id))
            return

        self._schedule_digest(admin.user_id, admin.digest_cron)

    def _schedule_digest(self, user_id: int, cron: str | None) -> None:
        if not cron:
            self._remove_job(_digest_job_id(user_id))
            return
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=settings.tz)
        except ValueError as exc:
            log.warning(
                "invalid digest cron for user_id=%s (%r): %s", user_id, cron, exc
            )
            self._remove_job(_digest_job_id(user_id))
            return

        self._scheduler.add_job(
            self._send_digest,
            trigger=trigger,
            id=_digest_job_id(user_id),
            args=[user_id],
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled digest user_id=%s with cron=%r", user_id, cron)

    async def _send_digest(self, user_id: int) -> None:
        if not should_send_proactive():
            # Skip without advancing the watermark — the next fire that
            # lands outside quiet hours will cover the missed window.
            log.info("quiet hours: skipping digest user_id=%s", user_id)
            return
        async with SessionLocal() as session:
            admin = await session.get(Admin, user_id)
            if admin is None or admin.delivery_mode != "digest":
                self._remove_job(_digest_job_id(user_id))
                return
            if not admin.receive_ideas:
                return
            await send_digest_to_admin(self.bot, session, admin)

    # ---------- internals ----------

    def _remove_job(self, job_id: str) -> None:
        try:
            self._scheduler.remove_job(job_id)
            log.info("unscheduled job=%s", job_id)
        except JobLookupError:
            pass

    # ---------- chat-messages retention ----------

    def _schedule_retention(self) -> None:
        """Hourly sweep that deletes ``chat_messages`` older than the
        retention window. Idempotent — safe to call multiple times.

        Not gated by quiet hours: this is housekeeping, not a chat
        message; the bot stays silent either way.
        """
        try:
            trigger = CronTrigger.from_crontab(
                RETENTION_CRON, timezone=settings.tz
            )
        except ValueError as exc:
            log.error(
                "invalid retention cron %r: %s", RETENTION_CRON, exc
            )
            return

        self._scheduler.add_job(
            self._run_retention,
            trigger=trigger,
            id=RETENTION_JOB_ID,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        log.info(
            "scheduled chat_messages retention every hour (>%dd)",
            RETENTION_DAYS,
        )

    async def _run_retention(self) -> None:
        cutoff = cutoff_for_retention(RETENTION_DAYS)
        async with SessionLocal() as session:
            try:
                deleted = await delete_older_than(session, cutoff)
            except Exception:  # noqa: BLE001
                log.exception("retention sweep failed")
                return
        if deleted:
            log.info(
                "retention: deleted %d chat_messages older than %s",
                deleted,
                cutoff.isoformat(),
            )
