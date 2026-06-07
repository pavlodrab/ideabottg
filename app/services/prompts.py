import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.prompt import prompt_keyboard
from app.models import Chat
from app.services.ideas import DEFAULT_PROMPT

log = logging.getLogger(__name__)


async def send_prompt_to_chat(
    bot: Bot, session: AsyncSession, chat: Chat
) -> bool:
    """Send the idea-collection prompt to a chat and persist tracking fields.

    Returns True on success, False on failure (chat inactive, telegram error, etc).
    """
    if not chat.is_active:
        return False

    me = await bot.get_me()
    text = chat.prompt_text or DEFAULT_PROMPT
    keyboard = prompt_keyboard(me.username, chat.chat_id)

    try:
        sent = await bot.send_message(chat.chat_id, text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001
        log.warning("send_prompt chat_id=%s failed: %s", chat.chat_id, exc)
        return False

    chat.last_prompt_message_id = sent.message_id
    chat.last_sent_at = datetime.now(timezone.utc)
    await session.commit()
    return True
