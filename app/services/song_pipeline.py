"""Daily-song pipeline: chat history → LLM SongDraft → Suno → group post.

This is the orchestrator the user asked for in
``[13.06.26 21:38] Я гой: Давай может добавим ии с опенроутера что бы
она основываясь на контексте чата генерила песню``.

Flow
----

1. Validate that both API keys (Suno + OpenRouter) are configured.
2. Fetch the last 24 h of captured ``chat_messages`` for the target chat.
3. If too few — bail out with ``too_few_messages`` (no LLM call, no
   Suno call, no credit spent).
4. Format messages as ``@username: text`` lines, oldest first.
5. One LLM call with the songwriter system prompt → ``SongDraft``
   (``title``, ``style``, ``lyrics``, ``summary``). Up to 3 retries
   when the model returns non-JSON.
6. Submit to Suno in **customMode=True** with our own title + style +
   lyrics. ``prompt`` field carries the lyrics, length-hint already
   baked in by the songwriter prompt.
7. Caller receives a :class:`SongGenerationResult` with the Suno
   ``task_id`` and the draft. They're expected to spawn a polling
   task via :func:`watch_suno_task`.

The poller (``watch_suno_task``) and the terminal handler
(``handle_terminal``) used to live in ``suno_admin.py`` as private
functions for the Test-Generation flow only. They moved here so both
flows share the same code path — single bug fix surface, identical
``Song`` row writes, identical ``tg_audio_file_id`` capture.

Out of scope (Phase 3.2+)
-------------------------

- Cron / scheduler-job wiring (``daily_song_at`` per chat).
- ``daily_songs`` row + dedupe by ``(chat_id, date_msk)``.
- ``LyricsOnlyProvider`` fallback when Suno fails.
- Per-role models (``llm_models`` table from the spec). MVP runs one
  model end-to-end.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Chat
from app.services.chat_messages import fetch_messages_since
from app.services.llm import (
    DEFAULT_SONGWRITER_SYSTEM_PROMPT,
    LlmApiError,
    OpenRouterClient,
    get_api_key as get_llm_api_key,
    get_model as get_llm_model,
    get_referer as get_llm_referer,
    get_system_prompt as get_llm_system_prompt,
)
from app.services.songs import set_tg_file_id, upsert_song
from app.services.suno import (
    SunoApiError,
    SunoApiOrgClient,
    TaskSnapshot,
    format_duration_label,
    get_api_key as get_suno_api_key,
    get_callback_url,
    get_model as get_suno_model,
    get_target_duration_sec,
)

log = logging.getLogger(__name__)


# ---------- tunables ----------

# How far back we look at chat history. 24 h = "yesterday's chatter".
DEFAULT_WINDOW_HOURS = 24

# Below this we don't even call the LLM — the song would be hollow.
DEFAULT_MIN_MESSAGES = 20

# Cap on messages fed into the LLM. ~800 lines × ~120 chars ≈ 100 KB,
# comfortably under the 128k-token context of free Gemini Flash but
# already much more than a typical day's chat. Tail-biased — we keep
# the most recent N messages on overflow.
MAX_MESSAGES_FOR_LLM = 800
MAX_CHAT_TEXT_CHARS = 100_000

# Length cap on each draft field — defensive against over-eager models.
MAX_TITLE_LEN = 200
MAX_STYLE_LEN = 500
MAX_LYRICS_LEN = 3000
MAX_SUMMARY_LEN = 500

# How many JSON-parse retries we do before declaring the LLM uncooperative.
LLM_JSON_RETRIES = 3
LLM_MAX_TOKENS = 2000
LLM_TEMPERATURE = 0.7

# Polling cadence for Suno tasks. Mirrors the old test-gen settings so
# the user-facing latency stays unchanged.
TASK_TIMEOUT_SEC = 360
TASK_POLL_INTERVAL_SEC = 15


# ---------- typed return values ----------

@dataclass
class SongDraft:
    """LLM output: enough to drive a customMode=True Suno call."""

    title: str
    style: str
    lyrics: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "style": self.style,
            "lyrics": self.lyrics,
            "summary": self.summary,
        }


@dataclass
class SongGenerationResult:
    """What :func:`start_song_generation` hands back synchronously.

    The Suno task itself is still running — caller spawns
    :func:`watch_suno_task` to poll it.
    """

    suno_task_id: str
    draft: SongDraft
    n_messages: int
    llm_model: str
    suno_model: str


# ---------- error type ----------


class SongPipelineError(RuntimeError):
    """Carries a machine-readable code + a human message.

    Codes used in this module (used by handlers to pick an icon):

    - ``no_suno_key``     — Suno API key not configured
    - ``no_llm_key``      — OpenRouter API key not configured
    - ``no_chat``         — chat_id isn't registered in the DB
    - ``too_few_messages``— window has fewer than ``min_messages``
    - ``llm_call_failed`` — OpenRouter returned an error
    - ``llm_invalid_json``— model couldn't produce JSON in 3 attempts
    - ``llm_invalid_draft``— JSON missing required keys
    - ``suno_call_failed``— Suno rejected the customMode submission

    The ``humanized()`` helper returns a one-line ready-for-Telegram
    message; emoji and HTML escape are applied at the call site.
    """

    def __init__(self, code: str, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg

    def humanized(self) -> str:
        return self.msg


# ---------- step 1: prepare chat-text input ----------


def build_chat_text(messages) -> str:
    """Format a list of ``ChatMessage`` rows for an LLM prompt.

    Output is one line per message in the form ``@username: text`` (or
    ``user12345: text`` for users without a public username),
    chronologically oldest-first. We deliberately don't include
    timestamps — the model doesn't need precision, and they would
    inflate token use.
    """
    lines: list[str] = []
    for m in messages:
        author = m.username or m.full_name or f"user{m.user_id}"
        text = (m.text or "").replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"@{author}: {text}")
    return "\n".join(lines)


def trim_chat_text(text: str, max_chars: int = MAX_CHAT_TEXT_CHARS) -> str:
    """If the joined text overflows, keep the *tail* (most recent
    messages). The morning's news is less interesting than the
    evening's, and free-tier model context limits make tail-bias
    cheap and reasonable.
    """
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


# ---------- step 2: songwriter LLM call ----------


def _build_user_message(
    chat_text: str, target_seconds: int, style_override: str | None = None
) -> str:
    """Compose the songwriter user-message.

    When the chat has a fixed ``song_style`` set via ``/musicmenu``,
    ``style_override`` is forwarded so the LLM uses **that** style
    verbatim instead of picking one from the chat tone. ``title`` and
    ``lyrics`` are always picked by the LLM.
    """
    if style_override:
        style_block = (
            f"СТИЛЬ ЗАФИКСИРОВАН админом чата — используй именно его:\n"
            f"\"{style_override.strip()}\"\n\n"
            "Его и положи в поле \"style\" JSON-ответа без изменений."
        )
    else:
        style_block = (
            "Стиль (style) выбери САМ так, чтобы он соответствовал тону чата: "
            "грустный → indie folk / lo-fi; угарный → pop punk / synthwave; "
            "философский → ambient / neo-classical; политика → punk; и т.п."
        )

    return (
        "Вот сообщения за последние сутки группового чата (oldest first):\n"
        "━━━━━━━━━━\n"
        f"{chat_text}\n"
        "━━━━━━━━━━\n\n"
        f"Сделай SongDraft по этому чату. Целевая длительность песни — "
        f"около {format_duration_label(target_seconds)} ({target_seconds} сек), "
        "ровно 1 куплет + припев + 1 куплет (без bridge, без длинного outro).\n\n"
        f"{style_block}\n\n"
        "Верни СТРОГО JSON без markdown, без префикса \"json\", без "
        "комментариев. Формат:\n"
        "{\"title\": \"...\", \"style\": \"...\", \"lyrics\": \"...\", "
        "\"summary\": \"...\"}"
    )


def _parse_song_draft(parsed) -> SongDraft:
    """Turn a parsed JSON dict into a SongDraft, with field clamping
    and a permissive missing-field policy: ``summary`` is optional,
    everything else must be present and non-empty."""
    if not isinstance(parsed, dict):
        raise SongPipelineError(
            "llm_invalid_draft",
            f"LLM вернул не объект, а {type(parsed).__name__}",
        )
    missing = [k for k in ("title", "style", "lyrics") if not parsed.get(k)]
    if missing:
        raise SongPipelineError(
            "llm_invalid_draft",
            "В JSON-ответе нет ключей: " + ", ".join(missing),
        )
    return SongDraft(
        title=str(parsed["title"]).strip()[:MAX_TITLE_LEN],
        style=str(parsed["style"]).strip()[:MAX_STYLE_LEN],
        lyrics=str(parsed["lyrics"]).strip()[:MAX_LYRICS_LEN],
        summary=str(parsed.get("summary") or "").strip()[:MAX_SUMMARY_LEN],
    )


async def llm_make_song_draft(
    *,
    client: OpenRouterClient,
    model: str,
    system_prompt: str,
    chat_text: str,
    target_seconds: int,
    style_override: str | None = None,
    retries: int = LLM_JSON_RETRIES,
) -> SongDraft:
    """One songwriter LLM call with up-to ``retries`` JSON-parse
    re-attempts.

    On a non-JSON / wrong-shape response the next attempt re-asks the
    same question with an extra ``user`` message reminding the model
    to return strict JSON. Most free-tier models eventually comply.
    Errors propagate as :class:`SongPipelineError`.
    """
    user_message = _build_user_message(chat_text, target_seconds, style_override)

    last_err: Exception | None = None
    for attempt in range(retries):
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        if attempt > 0:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "(retry) Твой предыдущий ответ был не валидным JSON. "
                        "Верни СТРОГО один JSON-объект "
                        "{title, style, lyrics, summary} — без markdown-"
                        "обёрток, без префикса \"json\", без текста "
                        "снаружи. Это критично."
                    ),
                }
            )
        try:
            result = await client.chat(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=LLM_MAX_TOKENS,
                temperature=LLM_TEMPERATURE,
            )
        except LlmApiError as exc:
            # Network / 429 / 5xx — retrying probably won't help fast.
            # Surface immediately so the user sees the real reason.
            raise SongPipelineError(
                "llm_call_failed",
                f"OpenRouter: {exc.humanized()}",
            ) from exc

        log.info(
            "song-pipeline: llm attempt=%s model=%s tokens=%s/%s text_len=%s",
            attempt + 1,
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            len(result.text),
        )

        parsed = result.parse_json()
        if parsed is None:
            # Some routed models still wrap JSON in ```json fences or add
            # a leading 'json'. Try a tolerant second pass before
            # spending another HTTP roundtrip.
            parsed = _tolerant_json_parse(result.text)

        if parsed is not None:
            try:
                return _parse_song_draft(parsed)
            except SongPipelineError as exc:
                last_err = exc
                # fall through to retry — the JSON was valid but had
                # missing/empty required fields.
                continue

        last_err = ValueError(
            f"non-JSON response: {result.text[:200]!r}"
        )

    raise SongPipelineError(
        "llm_invalid_json",
        f"LLM не вернул валидный SongDraft за {retries} "
        f"попытк{'у' if retries == 1 else 'и'}: {last_err}",
    )


def _tolerant_json_parse(text: str):
    """Strip the most common wrappers that prevent a strict JSON parse:
    triple-backtick fences, a leading ``json`` token, and surrounding
    whitespace. Falls back to ``None`` on failure (mirrors
    :meth:`ChatResult.parse_json`)."""
    s = (text or "").strip()
    if s.startswith("```"):
        # ```json\n{...}\n``` or ```\n{...}\n```
        s = s.lstrip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
        if s.endswith("```"):
            s = s.rstrip("`").strip()
    elif s.lower().startswith("json"):
        s = s[4:].strip()
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


# ---------- step 3: full pipeline ----------


async def collect_recent_messages(
    session: AsyncSession,
    *,
    chat_id: int,
    hours: int = DEFAULT_WINDOW_HOURS,
    limit: int = MAX_MESSAGES_FOR_LLM,
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return await fetch_messages_since(
        session, chat_id=chat_id, since=since, limit=limit
    )


@dataclass
class DraftBundle:
    """Everything the LLM half produces — reused by both the manual
    flow (``start_song_generation``) and the scheduled orchestrator
    (``daily_song.run_daily_song_for_chat``). Holds the Suno key/model
    so the caller can submit via either ``SunoApiOrgClient`` directly
    or a ``SongProvider``."""

    draft: SongDraft
    n_messages: int
    llm_model: str
    suno_model: str
    suno_key: str
    callback_url: str
    target_sec: int


async def generate_song_draft(
    *,
    session: AsyncSession,
    chat_id: int,
    requested_by: int | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_messages: int = DEFAULT_MIN_MESSAGES,
) -> DraftBundle:
    """History → LLM ``SongDraft`` (no Suno submit).

    Raises :class:`SongPipelineError` with a machine code on any
    refusal (missing key, too few messages, bad LLM JSON).
    """
    # 1. Validate keys.
    suno_key = await get_suno_api_key(session)
    if not suno_key:
        raise SongPipelineError(
            "no_suno_key",
            "Не задан Suno API-ключ. Открой /musicmenu → 🎚 Suno → 🔑.",
        )
    llm_key = await get_llm_api_key(session)
    if not llm_key:
        raise SongPipelineError(
            "no_llm_key",
            "Не задан OpenRouter API-ключ. "
            "Открой /musicmenu → 🤖 OpenRouter → 🔑.",
        )

    # 2. Fetch chat row + messages.
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise SongPipelineError(
            "no_chat", f"Чат {chat_id} не зарегистрирован у бота."
        )

    messages = await collect_recent_messages(
        session, chat_id=chat_id, hours=window_hours
    )
    if len(messages) < min_messages:
        raise SongPipelineError(
            "too_few_messages",
            f"За последние {window_hours} часов в чате только "
            f"{len(messages)} сообщений (минимум {min_messages}). "
            "Подожди, пока чат станет активнее, или уменьши минимум.",
        )

    # 3. Build chat-text input.
    chat_text = trim_chat_text(build_chat_text(messages))
    if not chat_text.strip():
        raise SongPipelineError(
            "too_few_messages",
            "Все захваченные сообщения пустые после очистки. "
            "Возможно, чат состоит только из стикеров/реакций.",
        )

    # 4. Pull LLM and Suno settings.
    target_sec = await get_target_duration_sec(session)
    llm_model = await get_llm_model(session)
    referer = await get_llm_referer(session)
    system_prompt = (
        await get_llm_system_prompt(session)
    ) or DEFAULT_SONGWRITER_SYSTEM_PROMPT

    suno_model = await get_suno_model(session)
    callback_url = await get_callback_url(session)

    log.info(
        "song-pipeline: draft-start chat_id=%s n_messages=%s text_chars=%s "
        "llm=%s suno=%s target_sec=%s style_override=%r requested_by=%s",
        chat_id,
        len(messages),
        len(chat_text),
        llm_model,
        suno_model,
        target_sec,
        chat.song_style[:60] if chat.song_style else None,
        requested_by,
    )

    # 5. LLM call.
    or_client = OpenRouterClient(llm_key, referer=referer)
    draft = await llm_make_song_draft(
        client=or_client,
        model=llm_model,
        system_prompt=system_prompt,
        chat_text=chat_text,
        target_seconds=target_sec,
        style_override=chat.song_style,
    )
    log.info(
        "song-pipeline: draft chat_id=%s title=%r style=%r lyrics_len=%s",
        chat_id,
        draft.title,
        draft.style,
        len(draft.lyrics),
    )

    return DraftBundle(
        draft=draft,
        n_messages=len(messages),
        llm_model=llm_model,
        suno_model=suno_model,
        suno_key=suno_key,
        callback_url=callback_url,
        target_sec=target_sec,
    )


async def start_song_generation(
    *,
    session: AsyncSession,
    chat_id: int,
    requested_by: int | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    min_messages: int = DEFAULT_MIN_MESSAGES,
) -> SongGenerationResult:
    """Run the synchronous half of the manual pipeline: history → LLM →
    Suno submit. Polling is split out (see :func:`watch_suno_task`).
    """
    bundle = await generate_song_draft(
        session=session,
        chat_id=chat_id,
        requested_by=requested_by,
        window_hours=window_hours,
        min_messages=min_messages,
    )

    # Suno submit (customMode so we control title / style / lyrics).
    suno_client = SunoApiOrgClient(bundle.suno_key)
    try:
        task_id = await suno_client.generate_music(
            prompt=bundle.draft.lyrics,
            model=bundle.suno_model,
            callback_url=bundle.callback_url,
            custom_mode=True,
            instrumental=False,
            style=bundle.draft.style,
            title=bundle.draft.title,
        )
    except SunoApiError as exc:
        raise SongPipelineError(
            "suno_call_failed",
            f"Suno: {exc.humanized()}",
        ) from exc

    log.info(
        "song-pipeline: suno-submit chat_id=%s task_id=%s suno_model=%s",
        chat_id,
        task_id,
        bundle.suno_model,
    )

    return SongGenerationResult(
        suno_task_id=task_id,
        draft=bundle.draft,
        n_messages=bundle.n_messages,
        llm_model=bundle.llm_model,
        suno_model=bundle.suno_model,
    )


# ---------- step 4: Suno polling + delivery ----------


async def watch_suno_task(
    *,
    bot: Bot,
    api_key: str,
    task_id: str,
    placeholder_chat_id: int,
    placeholder_message_id: int,
    audio_chat_id: int,
    requested_by: int | None,
    suno_model: str,
    prompt: str,
    title: str | None = None,
    style: str | None = None,
    lyrics: str | None = None,
    chat_id_for_song: int | None = None,
    post_lyrics_on_failure: bool = False,
    timeout_sec: int = TASK_TIMEOUT_SEC,
    poll_interval_sec: int = TASK_POLL_INTERVAL_SEC,
) -> None:
    """Poll Suno every ``poll_interval_sec``, edit the placeholder, and
    on success deliver the mp3.

    The two ``*chat_id`` parameters serve different goals:

    - ``placeholder_chat_id`` / ``placeholder_message_id`` — the bot's
      own status card (``⏳ Жду готовности…`` → ✅ / ❌). Usually the
      admin's DM.
    - ``audio_chat_id`` — where the final mp3 should be posted. For
      the test-generation flow this equals ``placeholder_chat_id``;
      for the daily-song flow it's the **target group**.

    All optional fields (``title`` / ``style`` / ``lyrics``) are
    persisted to the ``Song`` row even though Suno also returns them
    on completion — having the LLM-generated values stored alongside
    Suno's lets us debug "why does this song's title not match what
    the LLM wrote".
    """
    client = SunoApiOrgClient(api_key)
    elapsed = 0
    last_snapshot: TaskSnapshot | None = None

    while elapsed < timeout_sec:
        await asyncio.sleep(poll_interval_sec)
        elapsed += poll_interval_sec

        try:
            snapshot = await client.get_task(task_id)
        except SunoApiError as exc:
            log.warning("suno watch %s poll error: %s", task_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("suno watch %s unexpected: %s", task_id, exc)
            continue

        last_snapshot = snapshot

        if snapshot.is_terminal:
            await handle_terminal(
                bot=bot,
                placeholder_chat_id=placeholder_chat_id,
                placeholder_message_id=placeholder_message_id,
                audio_chat_id=audio_chat_id,
                task_id=task_id,
                snapshot=snapshot,
                requested_by=requested_by,
                suno_model=suno_model,
                prompt=prompt,
                title=title,
                style=style,
                lyrics=lyrics,
                chat_id_for_song=chat_id_for_song,
                post_lyrics_on_failure=post_lyrics_on_failure,
            )
            return

    # Timed out — edit the placeholder, leave the audio chat alone.
    msg = (
        f"⏰ <b>Тайм-аут.</b> Задача всё ещё не готова за "
        f"{timeout_sec // 60} минут.\n\n"
        f"🆔 <code>{html.escape(task_id)}</code>\n"
        f"📊 Последний статус: <code>"
        f"{html.escape(last_snapshot.status if last_snapshot else 'неизвестно')}"
        f"</code>\n\n"
        f"Можно подождать и проверить руками:\n"
        f"<code>/suno_status {html.escape(task_id)}</code>"
    )
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            msg,
            chat_id=placeholder_chat_id,
            message_id=placeholder_message_id,
        )
    # Lyrics-only fallback (F5.4): the daily-song flow still wants the
    # chat to get *something* when Suno is slow. Post the LLM lyrics so
    # the song-of-the-day lands as text even without the mp3.
    if post_lyrics_on_failure and lyrics:
        await _post_lyrics_only(
            bot, audio_chat_id, title=title, style=style, lyrics=lyrics
        )


async def _post_lyrics_only(
    bot: Bot,
    chat_id: int,
    *,
    title: str | None,
    style: str | None,
    lyrics: str,
) -> None:
    """Lyrics-only fallback post (F5.4) when Suno didn't deliver an mp3.

    Best-effort: wrapped by the caller's flow so a send failure here
    never crashes the pipeline.
    """
    head = f"🎵 <b>Песня дня (только текст — Suno не справился)</b>"
    if title:
        head += f"\n<b>{html.escape(title)}</b>"
    if style:
        head += f"\n🎨 <i>{html.escape(style[:120])}</i>"
    with contextlib.suppress(Exception):
        await bot.send_message(chat_id, head, disable_web_page_preview=True)
    with contextlib.suppress(Exception):
        await bot.send_message(
            chat_id,
            f"<pre>{html.escape(lyrics)[:3500]}</pre>",
            disable_web_page_preview=True,
        )


async def handle_terminal(
    *,
    bot: Bot,
    placeholder_chat_id: int,
    placeholder_message_id: int,
    audio_chat_id: int,
    task_id: str,
    snapshot: TaskSnapshot,
    requested_by: int | None,
    suno_model: str,
    prompt: str,
    title: str | None = None,
    style: str | None = None,
    lyrics: str | None = None,
    chat_id_for_song: int | None = None,
    post_lyrics_on_failure: bool = False,
) -> None:
    """Persist a ``Song`` row, update the placeholder, deliver mp3.

    Persisting happens in a fresh :data:`SessionLocal` because this
    runs outside any HTTP / handler scope — the originating session
    has long since closed.
    """
    if snapshot.is_success:
        # Prefer the LLM-supplied title over Suno's interpretation when
        # the song-from-chat flow gave us one. Fall back to whatever
        # Suno returned for the simple test-generation path.
        effective_title = title or snapshot.title
        effective_style = style
        effective_lyrics = lyrics

        # 1. Persist the Song so /musiclist finds it even if mp3
        # delivery fails below.
        song_id: int | None = None
        try:
            async with SessionLocal() as session:
                song = await upsert_song(
                    session,
                    suno_task_id=task_id,
                    model=suno_model,
                    chat_id=chat_id_for_song,
                    prompt=prompt,
                    title=effective_title,
                    style=effective_style,
                    lyrics=effective_lyrics,
                    audio_url=snapshot.audio_url,
                    stream_url=snapshot.stream_url,
                    image_url=snapshot.image_url,
                    duration=snapshot.duration,
                    requested_by=requested_by,
                    status="success",
                )
                song_id = song.id
        except Exception:  # noqa: BLE001
            log.exception("persist song for task %s failed", task_id)

        # 2. Update placeholder card.
        display_title = effective_title or "(без названия)"
        duration_part = (
            f" · {snapshot.duration:.0f} сек"
            if snapshot.duration is not None
            else ""
        )
        placeholder_text = (
            "✅ <b>Готово!</b>\n\n"
            f"🎵 <b>{html.escape(display_title)}</b>{duration_part}\n"
            f"🆔 <code>{html.escape(task_id)}</code>"
        )
        if effective_style:
            placeholder_text += (
                f"\n🎨 <i>{html.escape(effective_style[:120])}</i>"
            )
        if audio_chat_id != placeholder_chat_id:
            placeholder_text += (
                "\n\n📤 <i>mp3 отправлен в исходный чат.</i>"
            )
        else:
            placeholder_text += (
                "\n\n<i>Файл хранится на серверах Suno 15 дней. "
                "После первого проигрывания через бота он навсегда "
                "сохраняется в Telegram — найти его можно через "
                "/musiclist.</i>"
            )
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                placeholder_text,
                chat_id=placeholder_chat_id,
                message_id=placeholder_message_id,
            )

        # 3. Deliver mp3 + capture file_id.
        if snapshot.audio_url:
            # Cover art (6.3): Suno returns an image_url for the track.
            # Post it as a photo right before the audio so the song
            # lands with a visual. Best-effort — never blocks the mp3.
            if snapshot.image_url:
                with contextlib.suppress(Exception):
                    await bot.send_photo(
                        audio_chat_id,
                        photo=snapshot.image_url,
                        caption=f"🎵 {html.escape(display_title)}",
                    )
            caption_lines = [f"🎵 {html.escape(display_title)}"]
            if effective_style:
                caption_lines.append(
                    f"🎨 <i>{html.escape(effective_style[:120])}</i>"
                )
            caption = "\n".join(caption_lines)
            try:
                sent_audio = await bot.send_audio(
                    audio_chat_id,
                    audio=snapshot.audio_url,
                    title=display_title[:64],
                    performer="Suno",
                    caption=caption,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "send_audio for task %s to chat %s failed: %s",
                    task_id,
                    audio_chat_id,
                    exc,
                )
                with contextlib.suppress(Exception):
                    await bot.send_message(
                        audio_chat_id,
                        f"🔗 <a href=\"{html.escape(snapshot.audio_url)}\">"
                        "Скачать mp3</a>",
                        disable_web_page_preview=False,
                    )
                return

            # 4. Permanently capture Telegram's file_id.
            if (
                song_id is not None
                and sent_audio
                and sent_audio.audio
            ):
                try:
                    async with SessionLocal() as session:
                        await set_tg_file_id(
                            session, song_id, sent_audio.audio.file_id
                        )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "capture tg_audio_file_id for song %s failed",
                        song_id,
                    )

            # 5. For the daily-song flow we also drop the lyrics into
            # the group as a separate message — the audio caption can
            # only carry ~1024 chars, lyrics often don't fit.
            if (
                audio_chat_id != placeholder_chat_id
                and effective_lyrics
            ):
                with contextlib.suppress(Exception):
                    await bot.send_message(
                        audio_chat_id,
                        f"<pre>{html.escape(effective_lyrics)[:3500]}</pre>",
                        disable_web_page_preview=True,
                    )
        return

    # Terminal but failed — show errorMessage; don't bother audio chat.
    reason = snapshot.error_message or snapshot.status
    edited = (
        "❌ <b>Suno вернул ошибку.</b>\n\n"
        f"🆔 <code>{html.escape(task_id)}</code>\n"
        f"📊 Статус: <code>{html.escape(snapshot.status)}</code>\n"
        f"💬 Причина: {html.escape(reason)}"
    )
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            edited,
            chat_id=placeholder_chat_id,
            message_id=placeholder_message_id,
        )
    # Lyrics-only fallback (F5.4) for the daily-song flow.
    if post_lyrics_on_failure and lyrics:
        await _post_lyrics_only(
            bot, audio_chat_id, title=title, style=style, lyrics=lyrics
        )


__all__ = [
    "DEFAULT_MIN_MESSAGES",
    "DEFAULT_WINDOW_HOURS",
    "MAX_MESSAGES_FOR_LLM",
    "SongDraft",
    "SongGenerationResult",
    "SongPipelineError",
    "build_chat_text",
    "DraftBundle",
    "collect_recent_messages",
    "handle_terminal",
    "generate_song_draft",
    "llm_make_song_draft",
    "start_song_generation",
    "trim_chat_text",
    "watch_suno_task",
]
