import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from aiogram import Bot

from app.config import settings
from app.db import SessionLocal
from app.models import Admin, Chat
from app.services.digest import send_digest_to_admin
from app.services.prompts import send_prompt_to_chat
from app.services.quiet_hours import should_send_proactive

log = logging.getLogger(__name__)

PROMPT_PREFIX = "prompt:"
DIGEST_PREFIX = "digest:"


def _prompt_job_id(chat_id: int) -> str:
    return f"{PROMPT_PREFIX}{chat_id}"


def _digest_job_id(user_id: int) -> str:
    return f"{DIGEST_PREFIX}{user_id}"


class IdeaScheduler:
    """APScheduler wrapper that runs two kinds of jobs:

    - prompt jobs: post the idea-collection prompt to a chat on cron.
    - digest jobs: send a per-admin digest of recent ideas on cron.

    Source of truth lives in DB (`chats` and `admins` tables); on startup
    every active row gets a job, and admin/chat mutations call sync_*().
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

        for chat in chats:
            self._schedule_prompt(chat.chat_id, chat.schedule_cron)
        for admin in admins:
            self._schedule_digest(admin.user_id, admin.digest_cron)

        self._scheduler.start()
        log.info(
            "scheduler started with %d prompt + %d digest job(s)",
            len(chats),
            len(admins),
        )

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")

    # ---------- chat prompt jobs ----------

    async def sync_chat(self, chat_id: int) -> None:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)

        if chat is None or not chat.is_active or not chat.schedule_cron:
            self._remove_job(_prompt_job_id(chat_id))
            return

        self._schedule_prompt(chat.chat_id, chat.schedule_cron)

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
