"""DB helpers for ``chat_messages``.

Insertion is idempotent — if the same ``(chat_id, tg_message_id)`` is
captured twice (rare, but possible on Telegram retries), the duplicate
is silently dropped instead of raising. The unique constraint that
enforces this lives in the model / migration.

Retention: ``delete_older_than`` is invoked hourly by the scheduler job
in ``app/scheduler.py`` (default cutoff: 2 days). Songs are intentionally
NOT touched by retention.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage

# Default retention window in days. Bumping this here is enough — the
# scheduler-job uses ``cutoff_for_retention()`` and the DB has no
# embedded "ttl" concept.
RETENTION_DAYS = 2

# Hard cap on a single message body. Telegram caps text at 4096; we keep
# the same ceiling so nothing said in chat is silently truncated before
# it reaches the LLM-summarizer. Captions ride the same limit.
MAX_TEXT_LEN = 4096


async def insert_message(
    session: AsyncSession,
    *,
    chat_id: int,
    tg_message_id: int,
    user_id: int,
    username: str | None,
    full_name: str | None,
    text: str,
) -> ChatMessage | None:
    """Insert a captured message. On unique-constraint violation (the
    same Telegram message coming through twice) returns None and
    silently rolls back — duplicates aren't an error from our side."""
    msg = ChatMessage(
        chat_id=chat_id,
        tg_message_id=tg_message_id,
        user_id=user_id,
        username=username,
        full_name=full_name,
        text=text[:MAX_TEXT_LEN],
    )
    session.add(msg)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return msg


async def count_messages(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    since: datetime | None = None,
) -> int:
    stmt = select(func.count(ChatMessage.id))
    if chat_id is not None:
        stmt = stmt.where(ChatMessage.chat_id == chat_id)
    if since is not None:
        stmt = stmt.where(ChatMessage.created_at >= since)
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def fetch_messages_since(
    session: AsyncSession,
    *,
    chat_id: int,
    since: datetime,
    limit: int = 5000,
) -> list[ChatMessage]:
    """Get messages for a chat newer than ``since``, oldest-first.

    Used by the daily-song summarizer when it lands. ``limit`` is a
    safety cap — a hot chat can produce thousands of msgs/day; the
    summarizer chunks beyond that limit anyway."""
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.created_at >= since,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def oldest_message_at(
    session: AsyncSession, *, chat_id: int | None = None
) -> datetime | None:
    """Timestamp of the oldest captured message, optionally per-chat.

    Used by ``/captured`` to surface "messages start from <date>" so
    admins can verify the retention sweep is actually running and tell
    at a glance how much history the daily-song pipeline has to work
    with right now.
    """
    stmt = select(func.min(ChatMessage.created_at))
    if chat_id is not None:
        stmt = stmt.where(ChatMessage.chat_id == chat_id)
    result = await session.execute(stmt)
    return result.scalar()


async def delete_older_than(
    session: AsyncSession, cutoff: datetime
) -> int:
    """Delete all messages older than ``cutoff``. Returns rows deleted.

    Computed in Python (not a SQL ``INTERVAL``) so the same query works
    on both PostgreSQL and SQLite.
    """
    stmt = delete(ChatMessage).where(ChatMessage.created_at < cutoff)
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


def cutoff_for_retention(days: int = RETENTION_DAYS) -> datetime:
    """UTC timestamp ``days`` days ago. Used by the scheduler job."""
    return datetime.now(timezone.utc) - timedelta(days=days)
