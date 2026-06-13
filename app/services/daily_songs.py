"""DB helpers for the ``daily_songs`` run-ledger.

One row per ``(chat_id, date_local)`` — the unique constraint is the
dedup mechanism for the scheduled song-of-the-day. The orchestrator
(:mod:`app.services.daily_song`) transitions a row through
``queued → generating → done | skipped | failed`` and records the Suno
task id / produced song / error along the way.
"""
from __future__ import annotations

from datetime import date as date_, datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DailySong

# Non-retryable terminal states: a run already here this day is a no-op.
DONE_STATUSES = {"done", "generating"}


async def get_or_create_run(
    session: AsyncSession, *, chat_id: int, date_local: date_
) -> tuple[DailySong, bool]:
    """Fetch the run for ``(chat_id, date_local)`` or create it queued.

    Returns ``(run, created)``. Concurrency-safe: on a unique-constraint
    race the existing row is re-fetched and returned with
    ``created=False``.
    """
    existing = (
        await session.execute(
            select(DailySong).where(
                DailySong.chat_id == chat_id,
                DailySong.date_local == date_local,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    run = DailySong(chat_id=chat_id, date_local=date_local, status="queued")
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(DailySong).where(
                    DailySong.chat_id == chat_id,
                    DailySong.date_local == date_local,
                )
            )
        ).scalar_one()
        return existing, False
    await session.refresh(run)
    return run, True


async def mark(
    session: AsyncSession,
    run: DailySong,
    status: str,
    *,
    error: str | None = None,
    provider: str | None = None,
    suno_task_id: str | None = None,
    song_id: int | None = None,
    n_messages: int | None = None,
    title: str | None = None,
    style: str | None = None,
    finished: bool = False,
) -> DailySong:
    """Update a run's status + any provided fields. Only non-None
    fields are written, so partial updates across phases compose."""
    run.status = status
    if error is not None:
        run.error = error
    if provider is not None:
        run.provider = provider
    if suno_task_id is not None:
        run.suno_task_id = suno_task_id
    if song_id is not None:
        run.song_id = song_id
    if n_messages is not None:
        run.n_messages = n_messages
    if title is not None:
        run.title = title
    if style is not None:
        run.style = style
    if finished or status in ("done", "skipped", "failed"):
        run.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)
    return run


async def sweep_stale(
    session: AsyncSession, *, older_than_hours: int = 24
) -> int:
    """Mark runs stuck in queued/generating older than the cutoff as
    failed (F8.3). Returns the number of rows updated.

    Called on scheduler start: a crash mid-generation would otherwise
    leave a row in ``generating`` forever, blocking that day's retry.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    stmt = (
        update(DailySong)
        .where(
            DailySong.status.in_(("queued", "generating")),
            DailySong.created_at < cutoff,
        )
        .values(status="failed", error="stale_on_restart")
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)
