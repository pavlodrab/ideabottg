"""Capture text messages from registered group chats into ``chat_messages``.

Runs after ``DbSessionMiddleware`` so ``data["session"]`` is already
populated. The capture path NEVER raises — any failure is logged and
swallowed so it can't break the user-facing handler chain.

A message is captured iff ALL of the following are true:

- Chat type is ``group`` or ``supergroup``.
- The author exists and is not a bot.
- The message has text (or caption — we capture both).
- The text is not a slash-command (``/foo``).
- The chat is registered in ``chats`` AND ``is_active`` is True
  (paused chats are skipped to save writes).

Records older than the retention window (``RETENTION_DAYS`` in
``app/services/chat_messages.py``) are pruned hourly by the scheduler.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject

from app.models import Chat
from app.services.chat_messages import insert_message

log = logging.getLogger(__name__)


class CaptureMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Capture is best-effort — we never want it to interfere with
        # the actual handler chain. Errors get logged and suppressed.
        if isinstance(event, Message):
            try:
                await self._maybe_capture(event, data)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "capture middleware swallowed exception: %s", exc
                )
        return await handler(event, data)

    async def _maybe_capture(
        self, message: Message, data: dict[str, Any]
    ) -> None:
        # Only group / supergroup chats.
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return

        user = message.from_user
        if user is None or user.is_bot:
            return

        # Capture both plain text and image/video captions (still useful
        # context for the daily-song summarizer).
        text = message.text or message.caption
        if not text:
            return

        # Skip slash-commands. They're noise for the LLM and they may
        # contain sensitive args (e.g. /setcron).
        if text.startswith("/"):
            return

        session = data.get("session")
        if session is None:
            return

        # Skip unregistered or paused chats — save writes and respect
        # the user's pause toggle.
        chat = await session.get(Chat, message.chat.id)
        if chat is None or not chat.is_active:
            return

        await insert_message(
            session,
            chat_id=message.chat.id,
            tg_message_id=message.message_id,
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            text=text,
        )
