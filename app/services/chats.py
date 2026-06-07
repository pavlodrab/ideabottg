from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chat


async def upsert_chat(
    session: AsyncSession,
    chat_id: int,
    title: str | None,
    is_active: bool,
) -> tuple[Chat, bool]:
    """Insert or update a chat. Returns (chat, created)."""
    chat = await session.get(Chat, chat_id)
    created = False
    if chat is None:
        chat = Chat(chat_id=chat_id, title=title, is_active=is_active)
        session.add(chat)
        created = True
    else:
        if title is not None:
            chat.title = title
        chat.is_active = is_active
    await session.commit()
    return chat, created


async def set_chat_active(
    session: AsyncSession, chat_id: int, is_active: bool
) -> Chat | None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        return None
    chat.is_active = is_active
    await session.commit()
    return chat


async def list_chats(session: AsyncSession) -> list[Chat]:
    result = await session.execute(select(Chat).order_by(Chat.created_at.desc()))
    return list(result.scalars().all())


async def list_active_chats(session: AsyncSession) -> list[Chat]:
    result = await session.execute(
        select(Chat).where(Chat.is_active.is_(True)).order_by(Chat.created_at.desc())
    )
    return list(result.scalars().all())
