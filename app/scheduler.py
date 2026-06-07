import logging

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from aiogram import Bot

from app.config import settings
from app.db import SessionLocal
from app.models import Chat
from app.services.prompts import send_prompt_to_chat

log = logging.getLogger(__name__)

JOB_PREFIX = "prompt:"


def _job_id(chat_id: int) -> str:
    return f"{JOB_PREFIX}{chat_id}"


class IdeaScheduler:
    """APScheduler wrapper that posts idea prompts to chats on a cron schedule.

    Source of truth: chats.schedule_cron in the database. On startup, all active
    chats with a schedule_cron are loaded and registered as jobs. Admin commands
    that change a chat's schedule must call sync_chat() to apply the change.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def start(self) -> None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Chat).where(
                    Chat.is_active.is_(True),
                    Chat.schedule_cron.is_not(None),
                )
            )
            chats = list(result.scalars().all())

        for chat in chats:
            self._schedule_job(chat.chat_id, chat.schedule_cron)

        self._scheduler.start()
        log.info("scheduler started with %d job(s)", len(chats))

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")

    async def sync_chat(self, chat_id: int) -> None:
        """Re-read a chat from DB and (re)schedule or unschedule its job."""
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)

        if chat is None or not chat.is_active or not chat.schedule_cron:
            self._remove_job(chat_id)
            return

        self._schedule_job(chat.chat_id, chat.schedule_cron)

    def _schedule_job(self, chat_id: int, cron: str | None) -> None:
        if not cron:
            self._remove_job(chat_id)
            return
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=settings.tz)
        except ValueError as exc:
            log.warning("invalid cron for chat_id=%s (%r): %s", chat_id, cron, exc)
            self._remove_job(chat_id)
            return

        self._scheduler.add_job(
            self._send,
            trigger=trigger,
            id=_job_id(chat_id),
            args=[chat_id],
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )
        log.info("scheduled chat_id=%s with cron=%r", chat_id, cron)

    def _remove_job(self, chat_id: int) -> None:
        try:
            self._scheduler.remove_job(_job_id(chat_id))
            log.info("unscheduled chat_id=%s", chat_id)
        except JobLookupError:
            pass

    async def _send(self, chat_id: int) -> None:
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is None:
                self._remove_job(chat_id)
                return
            if not chat.is_active or not chat.schedule_cron:
                self._remove_job(chat_id)
                return
            await send_prompt_to_chat(self.bot, session, chat)
