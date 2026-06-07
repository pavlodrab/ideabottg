import html
import logging
from typing import Optional

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.prompt import owner_card_keyboard
from app.models import Idea
from app.services.admins import get_idea_recipients

log = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "💡 <b>Время делиться идеями!</b>\n\n"
    "Что бы ты хотел улучшить?\n"
    "Какая боль не даёт покоя?\n\n"
    "Жми кнопку — идея уйдёт напрямую."
)


async def create_idea(
    session: AsyncSession,
    *,
    chat_id: Optional[int],
    from_user_id: int,
    from_username: Optional[str],
    text: str,
    is_anonymous: bool,
) -> Idea:
    idea = Idea(
        chat_id=chat_id,
        from_user_id=from_user_id,
        from_username=from_username,
        text=text,
        is_anonymous=is_anonymous,
    )
    session.add(idea)
    await session.commit()
    await session.refresh(idea)
    return idea


async def set_idea_status(
    session: AsyncSession, idea_id: int, status: str
) -> Idea | None:
    idea = await session.get(Idea, idea_id)
    if idea is None:
        return None
    idea.status = status
    await session.commit()
    return idea


def _format_author(idea: Idea) -> str:
    if idea.is_anonymous:
        return "🙈 Аноним"
    if idea.from_username:
        return f"@{idea.from_username}"
    return (
        f"<a href='tg://user?id={idea.from_user_id}'>пользователь</a>"
    )


def format_idea_card(idea: Idea, chat_title: str | None) -> str:
    author = _format_author(idea)
    location = chat_title or "ЛС"
    body = html.escape(idea.text)
    return (
        f"💡 <b>Идея #{idea.id}</b>\n"
        f"📍 <b>{html.escape(location)}</b>\n"
        f"👤 {author}\n"
        f"━━━━━━━━━━━━\n"
        f"{body}"
    )


async def dispatch_idea_to_admins(
    bot: Bot,
    session: AsyncSession,
    idea: Idea,
    chat_title: str | None,
) -> None:
    text = format_idea_card(idea, chat_title)
    keyboard = owner_card_keyboard(idea.id)
    recipients = await get_idea_recipients(session)
    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, text, reply_markup=keyboard)
        except Exception as exc:  # noqa: BLE001
            log.warning("send idea %s to admin %s failed: %s", idea.id, admin_id, exc)
