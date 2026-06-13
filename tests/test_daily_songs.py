"""Tests for the daily_songs ledger service."""
from datetime import date, datetime, timedelta

import pytest

from app.models import Chat, DailySong
from app.services.daily_songs import (
    get_or_create_run,
    mark,
    sweep_stale,
)


async def _add_chat(session, chat_id):
    session.add(Chat(chat_id=chat_id, title="c", is_active=True))
    await session.commit()


@pytest.mark.asyncio
async def test_get_or_create_run_is_idempotent(session):
    await _add_chat(session, 1)
    d = date(2026, 6, 13)
    run1, created1 = await get_or_create_run(session, chat_id=1, date_local=d)
    assert created1 is True
    assert run1.status == "queued"

    run2, created2 = await get_or_create_run(session, chat_id=1, date_local=d)
    assert created2 is False
    assert run2.id == run1.id


@pytest.mark.asyncio
async def test_mark_transitions_and_sets_finished(session):
    await _add_chat(session, 2)
    run, _ = await get_or_create_run(
        session, chat_id=2, date_local=date(2026, 6, 13)
    )
    await mark(session, run, "generating", suno_task_id="t1", n_messages=42)
    assert run.status == "generating"
    assert run.suno_task_id == "t1"
    assert run.n_messages == 42
    assert run.finished_at is None  # generating isn't terminal

    await mark(session, run, "done", song_id=7)
    assert run.status == "done"
    assert run.song_id == 7
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_sweep_stale_marks_only_old_inflight(session):
    await _add_chat(session, 3)
    # stale generating row (very old)
    session.add(
        DailySong(
            chat_id=3,
            date_local=date(2020, 1, 1),
            status="generating",
            created_at=datetime(2020, 1, 1, 0, 0, 0),
        )
    )
    # fresh queued row (now) — must survive
    session.add(
        DailySong(
            chat_id=3,
            date_local=date(2026, 6, 13),
            status="queued",
            created_at=datetime.utcnow(),
        )
    )
    # already-done old row — must not be touched
    session.add(
        DailySong(
            chat_id=3,
            date_local=date(2019, 1, 1),
            status="done",
            created_at=datetime(2019, 1, 1, 0, 0, 0),
        )
    )
    await session.commit()

    swept = await sweep_stale(session, older_than_hours=24)
    assert swept == 1

    from sqlalchemy import select

    rows = (await session.execute(select(DailySong))).scalars().all()
    by_date = {r.date_local: r for r in rows}
    assert by_date[date(2020, 1, 1)].status == "failed"
    assert by_date[date(2020, 1, 1)].error == "stale_on_restart"
    assert by_date[date(2026, 6, 13)].status == "queued"
    assert by_date[date(2019, 1, 1)].status == "done"
