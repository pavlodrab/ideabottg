"""DB helpers for ``songs``.

The "list" helpers feed ``/musiclist`` (open to everyone in groups,
admin-friendly in DM) and the future daily-song UI. Visibility rules:

- In a group chat: only that chat's songs (``chat_id`` matches).
- In DM, regular user: only songs the user personally requested
  (``requested_by`` matches).
- In DM, bot admin: everything (cross-chat archive).

Songs are write-once-then-mutable on the same ``suno_task_id``: Suno
emits multiple status callbacks (``text`` → ``first`` → ``complete``)
and we only know the final mp3 URL on the last one. ``insert_song`` is
idempotent on ``suno_task_id`` so it can be called from the poller as
soon as we get a stable handle.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Song

# Pagination constant for /musiclist.
PAGE_SIZE = 5

# Songs are filtered to ``status='success'`` in list views — we don't
# want to expose half-finished/failed ones. Stored anyway for debug.
VISIBLE_STATUS = "success"


# ---------- writes ----------

async def upsert_song(
    session: AsyncSession,
    *,
    suno_task_id: str,
    model: str,
    chat_id: int | None = None,
    suno_audio_id: str | None = None,
    title: str | None = None,
    style: str | None = None,
    prompt: str | None = None,
    lyrics: str | None = None,
    audio_url: str | None = None,
    stream_url: str | None = None,
    image_url: str | None = None,
    duration: float | None = None,
    requested_by: int | None = None,
    status: str = "success",
) -> Song:
    """Insert or update a song row by ``suno_task_id``.

    Mutable fields (title / urls / duration / status / etc.) are
    overwritten on every call so the poller can refresh the row as
    Suno's response evolves through ``text`` → ``first`` → ``complete``.
    Identity fields (``model``, ``chat_id``, ``requested_by``) are kept
    on first write only — they don't change between callbacks.
    """
    existing = await session.execute(
        select(Song).where(Song.suno_task_id == suno_task_id)
    )
    song = existing.scalar_one_or_none()

    if song is None:
        song = Song(
            suno_task_id=suno_task_id,
            model=model,
            chat_id=chat_id,
            requested_by=requested_by,
        )
        session.add(song)

    if suno_audio_id is not None:
        song.suno_audio_id = suno_audio_id
    if title is not None:
        song.title = title
    if style is not None:
        song.style = style
    if prompt is not None:
        song.prompt = prompt
    if lyrics is not None:
        song.lyrics = lyrics
    if audio_url is not None:
        song.audio_url = audio_url
    if stream_url is not None:
        song.stream_url = stream_url
    if image_url is not None:
        song.image_url = image_url
    if duration is not None:
        song.duration = duration
    song.status = status

    await session.commit()
    await session.refresh(song)
    return song


async def set_tg_file_id(
    session: AsyncSession, song_id: int, tg_audio_file_id: str
) -> bool:
    """Capture Telegram's permanent ``file_id`` after the first
    ``send_audio``. After this, /musiclist can re-deliver the track
    without re-fetching from Suno (whose mp3 URLs expire in 15 days).
    """
    song = await session.get(Song, song_id)
    if song is None:
        return False
    song.tg_audio_file_id = tg_audio_file_id
    await session.commit()
    return True


# ---------- reads ----------

async def list_songs_for_chat(
    session: AsyncSession,
    chat_id: int,
    *,
    page: int = 0,
    page_size: int = PAGE_SIZE,
) -> list[Song]:
    stmt = (
        select(Song)
        .where(
            Song.chat_id == chat_id,
            Song.status == VISIBLE_STATUS,
        )
        .order_by(Song.created_at.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_songs_for_chat(session: AsyncSession, chat_id: int) -> int:
    stmt = select(func.count(Song.id)).where(
        Song.chat_id == chat_id,
        Song.status == VISIBLE_STATUS,
    )
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def list_songs_for_user(
    session: AsyncSession,
    user_id: int,
    *,
    is_admin: bool,
    page: int = 0,
    page_size: int = PAGE_SIZE,
) -> list[Song]:
    """Songs visible to ``user_id`` in DM.

    Admins see all songs (cross-chat archive); regular users see only
    songs they personally requested via ``/suno`` test-gen.
    """
    base = select(Song).where(Song.status == VISIBLE_STATUS)
    if not is_admin:
        base = base.where(Song.requested_by == user_id)
    stmt = (
        base
        .order_by(Song.created_at.desc())
        .offset(page * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_songs_for_user(
    session: AsyncSession,
    user_id: int,
    *,
    is_admin: bool,
) -> int:
    base = select(func.count(Song.id)).where(Song.status == VISIBLE_STATUS)
    if not is_admin:
        base = base.where(Song.requested_by == user_id)
    result = await session.execute(base)
    return int(result.scalar() or 0)


async def get_song(session: AsyncSession, song_id: int) -> Song | None:
    return await session.get(Song, song_id)
