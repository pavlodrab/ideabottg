"""Scheduled song-of-the-day orchestrator (spec design §3.7).

This is the cron entry point (``IdeaScheduler._run_song`` →
``run_daily_song_for_chat``). It wraps the existing pieces:

- LLM draft via :func:`app.services.song_pipeline.generate_song_draft`
  (shared with the manual ``/song_now`` flow).
- audio via a :class:`app.services.song_provider.SongProvider`
  (``sunoapi_org`` by default, ``lyrics_only`` as opt-in / fallback).
- a :class:`app.models.DailySong` ledger row for dedup + status +
  error tracking (one row per chat per local date).

Differences from the manual flow: no admin DM placeholder (status +
mp3 both go into the target group), ``requested_by`` is ``None``, quiet
days are skipped silently, and on Suno failure/timeout it falls back to
posting the lyrics as text so the chat still gets its song-of-the-day.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.config import settings
from app.db import SessionLocal
from app.models import Chat, DailySong
from app.services.daily_songs import DONE_STATUSES, get_or_create_run, mark
from app.services.song_pipeline import (
    SongDraft,
    SongPipelineError,
    TASK_POLL_INTERVAL_SEC,
    TASK_TIMEOUT_SEC,
    deliver_song,
    generate_song_draft,
)
from app.services.song_provider import (
    SongProviderError,
    SongResult,
    get_song_provider,
)
from app.services.songs import set_tg_file_id, upsert_song

log = logging.getLogger(__name__)


async def run_daily_song_for_chat(bot: Bot, chat_id: int) -> None:
    """Full scheduled pipeline for one chat. Idempotent per local day."""
    tz = ZoneInfo(settings.tz)
    today = datetime.now(tz).date()

    # --- Phase 1: ledger + draft + provider (single session) ---
    async with SessionLocal() as session:
        run, _created = await get_or_create_run(
            session, chat_id=chat_id, date_local=today
        )
        if run.status in DONE_STATUSES:
            log.info(
                "daily-song: chat %s already %s for %s, skipping",
                chat_id,
                run.status,
                today,
            )
            return

        chat = await session.get(Chat, chat_id)
        if chat is None or not chat.is_active or not chat.song_enabled:
            await mark(session, run, "skipped", error="not_enabled")
            log.info("daily-song: chat %s not enabled, skipping", chat_id)
            return

        try:
            bundle = await generate_song_draft(
                session=session, chat_id=chat_id, requested_by=None
            )
        except SongPipelineError as exc:
            status = "skipped" if exc.code == "too_few_messages" else "failed"
            await mark(session, run, status, error=exc.code)
            log.info(
                "daily-song: chat %s draft refused code=%s -> %s",
                chat_id,
                exc.code,
                status,
            )
            return
        except Exception as exc:  # noqa: BLE001
            await mark(session, run, "failed", error=f"draft:{exc}")
            log.exception("daily-song: chat %s draft crashed", chat_id)
            return

        try:
            provider = await get_song_provider(session)
        except SongProviderError as exc:
            await mark(session, run, "failed", error=f"provider:{exc}")
            log.warning("daily-song: chat %s no provider: %s", chat_id, exc)
            return

        await mark(
            session,
            run,
            "generating",
            provider=provider.name,
            n_messages=bundle.n_messages,
            title=bundle.draft.title,
            style=bundle.draft.style,
        )
        run_id = run.id

    draft = bundle.draft

    # --- Phase 2: submit (no DB session held during network I/O) ---
    try:
        task_id = await provider.submit(draft)
    except Exception as exc:  # noqa: BLE001  (SunoApiError / SongProviderError)
        log.warning("daily-song: chat %s submit failed: %s", chat_id, exc)
        await _fallback_lyrics(bot, chat_id, draft)
        await _finalize(run_id, "failed", error=f"submit:{exc}")
        return

    await _finalize(run_id, "generating", suno_task_id=task_id)

    # Tell the group something's coming (only now that submit succeeded).
    try:
        placeholder = await bot.send_message(
            chat_id,
            "🎵 <b>Песня дня готовится…</b>\n"
            f"📊 По {bundle.n_messages} сообщениям за сутки.\n"
            "Обычно занимает 2–3 минуты.",
        )
        placeholder_id = placeholder.message_id
    except Exception:  # noqa: BLE001
        log.exception("daily-song: chat %s placeholder failed", chat_id)
        placeholder_id = None

    # --- Phase 3: poll until terminal / timeout ---
    elapsed = 0
    while elapsed < TASK_TIMEOUT_SEC:
        await asyncio.sleep(TASK_POLL_INTERVAL_SEC)
        elapsed += TASK_POLL_INTERVAL_SEC
        try:
            result = await provider.poll(task_id)
        except SongProviderError as exc:
            log.info("daily-song: chat %s suno failed: %s", chat_id, exc)
            await _cleanup_placeholder(bot, chat_id, placeholder_id)
            await _fallback_lyrics(bot, chat_id, draft)
            await _finalize(run_id, "failed", error=f"suno:{exc}")
            return
        except Exception as exc:  # noqa: BLE001  transient poll error
            log.warning("daily-song: chat %s poll error: %s", chat_id, exc)
            continue
        if result is None:
            continue
        # Terminal success (real audio or lyrics-only result).
        await _deliver(
            bot,
            chat_id=chat_id,
            placeholder_id=placeholder_id,
            draft=draft,
            result=result,
            run_id=run_id,
            provider_name=provider.name,
            suno_task_id=task_id,
        )
        return

    # Timed out.
    log.info("daily-song: chat %s timed out after %ss", chat_id, TASK_TIMEOUT_SEC)
    await _cleanup_placeholder(bot, chat_id, placeholder_id)
    await _fallback_lyrics(bot, chat_id, draft)
    await _finalize(run_id, "failed", error="timeout")


# ---------- delivery helpers ----------


async def _deliver(
    bot: Bot,
    *,
    chat_id: int,
    placeholder_id: int | None,
    draft: SongDraft,
    result: SongResult,
    run_id: int,
    provider_name: str,
    suno_task_id: str,
) -> None:
    """Post the finished song (or lyrics-only) and finalize the ledger."""
    if result.is_lyrics_only:
        await _cleanup_placeholder(bot, chat_id, placeholder_id)
        await _fallback_lyrics(bot, chat_id, draft)
        await _finalize(run_id, "done", error="lyrics_only")
        return

    title = draft.title or result.title or "(без названия)"

    # Persist the Song first so it survives a delivery failure.
    song_id: int | None = None
    with contextlib.suppress(Exception):
        async with SessionLocal() as session:
            song = await upsert_song(
                session,
                suno_task_id=suno_task_id,
                model=provider_name,
                chat_id=chat_id,
                prompt=draft.lyrics,
                title=title,
                style=draft.style,
                lyrics=draft.lyrics,
                audio_url=result.audio_url,
                stream_url=result.stream_url,
                image_url=result.image_url,
                duration=result.duration,
                requested_by=None,
                status="success",
            )
            song_id = song.id

    await _cleanup_placeholder(bot, chat_id, placeholder_id)

    # Single-message delivery: audio with cover thumbnail + caption.
    sent = await deliver_song(
        bot,
        chat_id,
        audio_ref=result.audio_url,
        title=title,
        style=draft.style,
        lyrics=draft.lyrics,
        image_url=result.image_url,
    )

    # Capture the permanent Telegram file_id.
    if song_id is not None and sent is not None and sent.audio:
        with contextlib.suppress(Exception):
            async with SessionLocal() as session:
                await set_tg_file_id(session, song_id, sent.audio.file_id)

    await _finalize(run_id, "done", song_id=song_id)
    # Bookkeeping on the chat row.
    with contextlib.suppress(Exception):
        async with SessionLocal() as session:
            chat = await session.get(Chat, chat_id)
            if chat is not None:
                chat.last_song_sent_at = datetime.now(timezone.utc)
                await session.commit()


async def _fallback_lyrics(bot: Bot, chat_id: int, draft: SongDraft) -> None:
    """Lyrics-only post (F5.4) when Suno didn't deliver audio."""
    head = "🎵 <b>Песня дня (только текст — Suno не справился)</b>"
    if draft.title:
        head += f"\n<b>{html.escape(draft.title)}</b>"
    if draft.style:
        head += f"\n🎨 <i>{html.escape(draft.style[:120])}</i>"
    with contextlib.suppress(Exception):
        await bot.send_message(chat_id, head)
    if draft.lyrics:
        with contextlib.suppress(Exception):
            await bot.send_message(
                chat_id,
                f"<pre>{html.escape(draft.lyrics)[:3500]}</pre>",
                disable_web_page_preview=True,
            )


async def _cleanup_placeholder(
    bot: Bot, chat_id: int, placeholder_id: int | None
) -> None:
    if placeholder_id is None:
        return
    with contextlib.suppress(Exception):
        await bot.delete_message(chat_id, placeholder_id)


async def _finalize(run_id: int, status: str, **fields) -> None:
    """Open a fresh session to update the ledger row by id."""
    async with SessionLocal() as session:
        run = await session.get(DailySong, run_id)
        if run is not None:
            await mark(session, run, status, **fields)


__all__ = ["run_daily_song_for_chat"]
