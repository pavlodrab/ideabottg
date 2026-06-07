import html
import logging
from typing import NamedTuple, Optional

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.prompt import owner_card_keyboard
from app.models import Idea
from app.services.admins import get_stream_recipients

log = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "💡 <b>Время делиться идеями!</b>\n\n"
    "Что бы ты хотел улучшить?\n"
    "Какая боль не даёт покоя?\n\n"
    "Жми кнопку — идея уйдёт напрямую."
)


class TagInfo(NamedTuple):
    key: str
    label: str
    icon: str


TAGS: list[TagInfo] = [
    TagInfo("feature", "Идея", "💡"),
    TagInfo("bug", "Баг", "🐛"),
    TagInfo("feedback", "Фидбек", "💬"),
    TagInfo("other", "Другое", "🎁"),
]
TAGS_BY_KEY: dict[str, TagInfo] = {t.key: t for t in TAGS}
DEFAULT_TAG = "other"


def tag_label(key: str | None) -> str:
    if not key:
        return f"{TAGS_BY_KEY[DEFAULT_TAG].icon} {TAGS_BY_KEY[DEFAULT_TAG].label}"
    info = TAGS_BY_KEY.get(key)
    if info is None:
        return key
    return f"{info.icon} {info.label}"


# ---------- create / update ----------

async def create_idea(
    session: AsyncSession,
    *,
    chat_id: Optional[int],
    from_user_id: int,
    from_username: Optional[str],
    text: str,
    is_anonymous: bool,
    tag: str = DEFAULT_TAG,
) -> Idea:
    idea = Idea(
        chat_id=chat_id,
        from_user_id=from_user_id,
        from_username=from_username,
        text=text,
        is_anonymous=is_anonymous,
        tag=tag if tag in TAGS_BY_KEY else DEFAULT_TAG,
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


# ---------- read / list ----------

STATUS_FILTERS: dict[str, list[str] | None] = {
    "all": None,
    "new": ["new"],
    "starred": ["starred"],
    "read": ["read"],
    "archived": ["archived"],
}


async def list_ideas(
    session: AsyncSession,
    *,
    status_filter: str = "new",
    page: int = 0,
    page_size: int = 8,
) -> list[Idea]:
    stmt = select(Idea).order_by(Idea.created_at.desc())
    statuses = STATUS_FILTERS.get(status_filter)
    if statuses is not None:
        stmt = stmt.where(Idea.status.in_(statuses))
    stmt = stmt.offset(page * page_size).limit(page_size)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_ideas(
    session: AsyncSession, *, status_filter: str = "new"
) -> int:
    stmt = select(func.count(Idea.id))
    statuses = STATUS_FILTERS.get(status_filter)
    if statuses is not None:
        stmt = stmt.where(Idea.status.in_(statuses))
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


# ---------- formatting ----------

def _format_author(idea: Idea) -> str:
    if idea.is_anonymous:
        return "🙈 Аноним"
    if idea.from_username:
        return f"@{idea.from_username}"
    return f"<a href='tg://user?id={idea.from_user_id}'>пользователь</a>"


def format_idea_card(idea: Idea, chat_title: str | None) -> str:
    author = _format_author(idea)
    location = chat_title or "ЛС"
    body = html.escape(idea.text)
    badge = tag_label(idea.tag)
    return (
        f"💡 <b>Идея #{idea.id}</b>  ·  {badge}\n"
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
    recipients = await get_stream_recipients(session)
    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, text, reply_markup=keyboard)
        except Exception as exc:  # noqa: BLE001
            log.warning("send idea %s to admin %s failed: %s", idea.id, admin_id, exc)
