"""Manual trigger for the daily-song pipeline.

Two entry points, both admin-only:

- ``/song_now <chat_id>`` (DM) — kicks the pipeline for one chat. The
  status placeholder lands in the admin's DM, the final mp3 is posted
  to the **target chat** (so the group hears its own song-of-the-day).
- ``/musicmenu → 🎵 Сгенерировать песню сейчас`` — same flow via the
  admin home keyboard. Opens a chat picker first, then runs the
  pipeline for the picked chat.
- In a group, ``/musicmenu → 🎵 Сгенерировать сейчас`` runs the
  pipeline for that very group (no picker needed).

Why placeholder in DM and audio in the target group?
The pipeline can take 2–4 minutes. Showing "⏳ generating…" in the
target group would be noisy / spammy; admins want the progress in
their own DM and the **finished song** in the group as a single,
clean message. The split is implemented in
``song_pipeline.watch_suno_task`` via separate ``placeholder_chat_id``
and ``audio_chat_id`` parameters.
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Chat
from app.services.admins import is_admin, is_owner
from app.services.chats import list_chats
from app.services.chat_messages import count_messages, purge_chat_history
from app.services.songs import song_stats
from app.services.song_pipeline import (
    DEFAULT_MIN_MESSAGES,
    SongPipelineError,
    start_song_from_prompt,
    start_song_generation,
    watch_suno_task,
)
from app.services.suno import get_api_key as get_suno_api_key

log = logging.getLogger(__name__)

router = Router(name="song_admin")


# ---------- public /music (user prompt → song) ----------

# Per-user cooldown for /music. In-memory (single-instance bot): enough
# to stop rapid-fire spam that would burn Suno credits. Reset on a
# failed attempt so a config error doesn't lock the user out.
MUSIC_COOLDOWN_SEC = 180
# Hard cap on the user's prompt length before the LLM call.
MUSIC_MAX_LEN = 800
_last_music_at: dict[int, float] = {}

# Matches a trailing style marker: "... стиль панк" / "... в стиле lo-fi"
# / "... style punk". The part before the marker is the lyric idea.
_STYLE_RE = re.compile(
    r"\s+(?:в\s+стиле|стиль|style)\s+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def parse_music_command(text: str) -> tuple[str, str | None]:
    """Split ``/music`` args into ``(idea, style|None)``.

    A trailing ``стиль X`` / ``в стиле X`` / ``style X`` sets the style;
    everything before it is the lyric idea. If the marker leaves no idea
    before it, the whole text is treated as the idea (no style).
    """
    text = (text or "").strip()
    m = _STYLE_RE.search(text)
    if m:
        idea = text[: m.start()].strip()
        style = m.group(1).strip()
        if idea and style:
            return idea, style
    return text, None


# Daily-song time presets shown in the schedule submenu, as (HH, MM).
# Crontab built from these is "MM HH * * *" in the global settings.tz.
SONG_TIME_PRESETS: list[tuple[int, int]] = [
    (18, 0),
    (20, 0),
    (21, 0),
    (22, 0),
]


def _cron_to_hhmm(cron: str | None) -> str | None:
    """Best-effort 'MM HH * * *' → 'HH:MM'. Returns None if unparseable."""
    if not cron:
        return None
    parts = cron.split()
    if len(parts) < 2:
        return None
    minute, hour = parts[0], parts[1]
    if not (minute.isdigit() and hour.isdigit()):
        return None
    return f"{int(hour):02d}:{int(minute):02d}"


def _song_schedule_keyboard(
    chat_id: int, *, enabled: bool, cron: str | None
) -> InlineKeyboardMarkup:
    """Time-preset picker + off button for the daily-song schedule.

    The currently-active preset (if any) gets a ✅ marker.
    """
    current = _cron_to_hhmm(cron) if enabled else None
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for hh, mm in SONG_TIME_PRESETS:
        label = f"{hh:02d}:{mm:02d}"
        marker = "✅ " if current == label else "🕘 "
        row.append(
            InlineKeyboardButton(
                text=f"{marker}{label}",
                callback_data=f"music:song_at:{chat_id}:{hh}:{mm}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="🚫 Выключить" if enabled else "🚫 Выключено",
                callback_data=f"music:song_off:{chat_id}",
            ),
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"music:menu_open:{chat_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _render_song_schedule_text(
    chat: Chat, *, tz: str
) -> str:
    if chat.song_enabled and chat.song_cron:
        hhmm = _cron_to_hhmm(chat.song_cron) or chat.song_cron
        state = f"🟢 включено · ежедневно в <b>{hhmm}</b> ({tz})"
    else:
        state = "🔴 выключено"
    title = html.escape(chat.title or str(chat.chat_id))
    return (
        f"📅 <b>Расписание «Песни дня»</b>\n"
        f"📍 {title}\n\n"
        f"Сейчас: {state}\n\n"
        "Бот раз в день возьмёт сообщения чата за последние 24 часа, "
        "сгенерирует песню и запостит её сюда. Если за сутки меньше "
        f"{DEFAULT_MIN_MESSAGES} сообщений — день пропускается молча.\n\n"
        "Выбери время или выключи 👇"
    )


# ---------- gating ----------

async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


# ---------- shared launcher ----------

async def _launch_song(
    *,
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    requested_by: int | None,
    placeholder: Message,
) -> None:
    """Run the synchronous half of the pipeline and spawn the poller.

    On any :class:`SongPipelineError` we edit ``placeholder`` to show
    the error inline. The placeholder doubles as the "I'm working on
    it" card while the Suno task runs in the background.

    Audio gets posted to ``chat_id`` (the source group). The placeholder
    card stays where it was sent — usually the admin's DM, but for the
    in-group invocation it's the same chat.
    """
    try:
        result = await start_song_generation(
            session=session,
            chat_id=chat_id,
            requested_by=requested_by,
        )
    except SongPipelineError as exc:
        log.info(
            "song-pipeline: refused chat_id=%s code=%s msg=%s",
            chat_id,
            exc.code,
            exc.msg,
        )
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                f"❌ <b>Не получилось.</b>\n\n{html.escape(exc.humanized())}",
            )
        return
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "song-pipeline: unexpected exception for chat_id=%s", chat_id
        )
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                "❌ <b>Неожиданная ошибка.</b>\n"
                f"<code>{html.escape(str(exc))}</code>\n\n"
                "Подробности в /logs."
            )
        return

    # Submitted to Suno successfully. Update the placeholder with the
    # task_id and what we know so the admin can correlate logs even
    # before mp3 is ready.
    placeholder_text = (
        "🎵 <b>Песня дня — задача отправлена в Suno</b>\n\n"
        f"📍 Чат: <code>{chat_id}</code>\n"
        f"📊 Сообщений за сутки: <b>{result.n_messages}</b>\n"
        f"🧠 LLM: <code>{html.escape(result.llm_model)}</code>\n"
        f"🎚 Suno: <code>{html.escape(result.suno_model)}</code>\n"
        f"🆔 task: <code>{html.escape(result.suno_task_id)}</code>\n\n"
        f"📝 <b>{html.escape(result.draft.title)}</b>\n"
        f"🎨 <i>{html.escape(result.draft.style[:200])}</i>\n"
    )
    if result.draft.summary:
        placeholder_text += (
            f"\n💬 <i>{html.escape(result.draft.summary[:300])}</i>\n"
        )
    placeholder_text += "\n⏳ Жду готовности (обычно 2–3 минуты)…"
    with contextlib.suppress(Exception):
        await placeholder.edit_text(
            placeholder_text, disable_web_page_preview=True
        )

    # Spawn the poller. It runs in the background; this handler returns
    # immediately so the admin can do other things meanwhile.
    suno_key = await get_suno_api_key(session)
    if not suno_key:
        # Should never happen — start_song_generation already validated.
        # But check anyway so we don't crash the bg task.
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                "❌ Suno-ключ исчез после старта. Проверь /musicmenu."
            )
        return

    asyncio.create_task(
        watch_suno_task(
            bot=bot,
            api_key=suno_key,
            task_id=result.suno_task_id,
            placeholder_chat_id=placeholder.chat.id,
            placeholder_message_id=placeholder.message_id,
            audio_chat_id=chat_id,
            requested_by=requested_by,
            suno_model=result.suno_model,
            prompt=result.draft.lyrics,
            title=result.draft.title,
            style=result.draft.style,
            lyrics=result.draft.lyrics,
            chat_id_for_song=chat_id,
        ),
        name=f"song-pipeline:{result.suno_task_id}",
    )


@router.message(Command("music"), F.text)
async def cmd_music(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Public: any user generates a song from their own text.

    ``/music <текст> [стиль <X>]`` — the text is run through the
    songwriter LLM (improves rhymes / structure, keeps the user's
    intent), then Suno. With no explicit style the LLM picks one from
    the text's tone. Works in groups and DM; the mp3 is posted to the
    same chat.
    """
    user = message.from_user
    if user is None:
        return

    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "🎵 <b>Сгенерировать песню</b>\n\n"
            "<code>/music &lt;текст&gt;</code> — и я сделаю из него песню.\n"
            "Можно задать стиль в конце: "
            "<code>стиль панк</code> / <code>в стиле lo-fi</code>.\n\n"
            "Примеры:\n"
            "• <code>/music Андрюха крутой, чек-пук, лучший друг стиль панк</code>\n"
            "• <code>/music песня про субботнее утро и кофе</code> "
            "(стиль выберу сам)\n\n"
            "Без стиля — прогоню текст через нейронку, причешу рифмы и "
            "подберу стиль под настроение."
        )
        return

    if len(raw) > MUSIC_MAX_LEN:
        await message.answer(
            f"⚠️ Слишком длинно (лимит {MUSIC_MAX_LEN} символов). "
            "Сократи идею — припев и пара строк куплета достаточно."
        )
        return

    # Per-user cooldown.
    now = time.monotonic()
    last = _last_music_at.get(user.id)
    if last is not None and (now - last) < MUSIC_COOLDOWN_SEC:
        wait = int(MUSIC_COOLDOWN_SEC - (now - last)) + 1
        await message.answer(
            f"⏳ Не так быстро — подожди {wait} c перед следующей песней."
        )
        return
    _last_music_at[user.id] = now

    idea, style = parse_music_command(raw)

    placeholder = await message.answer(
        "⏳ <b>Готовлю песню по твоему тексту…</b>\n"
        + (f"🎨 Стиль: <i>{html.escape(style)}</i>\n" if style else "")
        + "Причёсываю рифмы и зову Suno. Обычно 2–3 минуты."
    )

    try:
        result = await start_song_from_prompt(
            session=session,
            user_text=idea,
            style_override=style,
            requested_by=user.id,
        )
    except SongPipelineError as exc:
        _last_music_at.pop(user.id, None)  # allow immediate retry after fix
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                f"❌ <b>Не получилось.</b>\n\n{html.escape(exc.humanized())}"
            )
        return
    except Exception as exc:  # noqa: BLE001
        _last_music_at.pop(user.id, None)
        log.exception("music: unexpected error for user %s", user.id)
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                "❌ <b>Неожиданная ошибка.</b>\n"
                f"<code>{html.escape(str(exc))}</code>"
            )
        return

    placeholder_text = (
        "🎵 <b>Песня — задача отправлена в Suno</b>\n\n"
        f"📝 <b>{html.escape(result.draft.title)}</b>\n"
        f"🎨 <i>{html.escape(result.draft.style[:200])}</i>\n"
        f"🆔 task: <code>{html.escape(result.suno_task_id)}</code>\n\n"
        "⏳ Жду готовности (обычно 2–3 минуты)…"
    )
    with contextlib.suppress(Exception):
        await placeholder.edit_text(placeholder_text, disable_web_page_preview=True)

    suno_key = await get_suno_api_key(session)
    if not suno_key:
        with contextlib.suppress(Exception):
            await placeholder.edit_text("❌ Suno-ключ исчез после старта.")
        return

    # Group songs are tied to the chat; DM songs aren't (chat_id_for_song
    # must be a registered chat FK or None).
    is_group = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
    asyncio.create_task(
        watch_suno_task(
            bot=bot,
            api_key=suno_key,
            task_id=result.suno_task_id,
            placeholder_chat_id=placeholder.chat.id,
            placeholder_message_id=placeholder.message_id,
            audio_chat_id=message.chat.id,
            requested_by=user.id,
            suno_model=result.suno_model,
            prompt=result.draft.lyrics,
            title=result.draft.title,
            style=result.draft.style,
            lyrics=result.draft.lyrics,
            chat_id_for_song=message.chat.id if is_group else None,
        ),
        name=f"music:{result.suno_task_id}",
    )


# ---------- /song_now ----------
@router.message(Command("song_now"), F.chat.type == ChatType.PRIVATE)
async def cmd_song_now(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    bot: Bot,
) -> None:
    if not await _require_admin(message, session):
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>/song_now &lt;chat_id&gt;</code>\n\n"
            "Можно подсмотреть chat_id в /captured или /chats. "
            "Mp3 уйдёт в этот чат, статус — сюда в DM."
        )
        return
    try:
        chat_id = int(raw)
    except ValueError:
        await message.answer("⚠️ chat_id должен быть числом.")
        return

    # Quick existence check — better message than waiting for the
    # pipeline to fail with no_chat after spending an LLM call.
    if await session.get(Chat, chat_id) is None:
        await message.answer("⚠️ Такого чата нет в базе.")
        return

    placeholder = await message.answer(
        "⏳ <b>Готовлю песню дня</b>\n\n"
        f"📍 Чат: <code>{chat_id}</code>\n"
        "Собираю историю чата и зову LLM…"
    )
    await _launch_song(
        bot=bot,
        session=session,
        chat_id=chat_id,
        requested_by=message.from_user.id if message.from_user else None,
        placeholder=placeholder,
    )


# ---------- /musicmenu → 🎵 Сгенерировать песню → chat picker ----------

@router.callback_query(F.data == "mm:gen_pick")
async def cb_gen_pick(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chats = await list_chats(session)
    if not chats:
        await callback.answer(
            "🤷 Нет ни одного зарегистрированного чата. "
            "Сначала добавь меня в группу.",
            show_alert=True,
        )
        return
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        title = (chat.title or str(chat.chat_id))[:50]
        emoji = "🟢" if chat.is_active else "🟡"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {title}"[:64],
                    callback_data=f"mm:gen:{chat.chat_id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="mm:home")]
    )
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "🎵 <b>Песня дня — выбери чат</b>\n\n"
                "Бот возьмёт сообщения из этого чата за последние 24 часа, "
                "пропустит через LLM и сгенерирует песню в Suno. "
                "Mp3 уйдёт в выбранный чат, статус — сюда в DM.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
    await callback.answer()


@router.callback_query(F.data.startswith("mm:gen:"))
async def cb_gen_run_dm(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    """Run the pipeline from the DM chat picker.

    Placeholder lives in the admin's DM (callback.message.chat.id);
    audio uplinks to the target group (chat_id parsed from callback).
    """
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    chat_title = chat.title or str(chat.chat_id)
    placeholder = await callback.message.edit_text(
        "⏳ <b>Готовлю песню дня</b>\n\n"
        f"📍 {html.escape(chat_title)}\n"
        "Собираю историю чата и зову LLM…"
    )
    # edit_text returns Message | bool depending on the situation;
    # fall back to the original message when the edit returned True.
    pl_msg = placeholder if isinstance(placeholder, Message) else callback.message
    await _launch_song(
        bot=bot,
        session=session,
        chat_id=chat_id,
        requested_by=callback.from_user.id if callback.from_user else None,
        placeholder=pl_msg,
    )
    await callback.answer()


# ---------- in-group "🎵 Сгенерировать сейчас" ----------

@router.callback_query(F.data.startswith("music:gen_now:"))
async def cb_gen_run_in_chat(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    """Run the pipeline from the group's per-chat ``/musicmenu``.

    Placeholder + audio land in the same chat — admin invoked this in
    the group, so the group already knows it asked for it.
    """
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return

    if not isinstance(callback.message, Message):
        await callback.answer()
        return

    placeholder = await callback.message.answer(
        "⏳ <b>Песня дня</b> · готовлю…\n"
        "Собираю последние 24ч чата и зову LLM. Это займёт 2–4 минуты."
    )
    await _launch_song(
        bot=bot,
        session=session,
        chat_id=chat_id,
        requested_by=callback.from_user.id if callback.from_user else None,
        placeholder=placeholder,
    )
    await callback.answer("🎵 Запустил")


# ---------- /song_stats ----------

@router.message(Command("song_stats"), F.chat.type == ChatType.PRIVATE)
async def cmd_song_stats(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    stats = await song_stats(session, days=30)
    lines = [
        "📊 <b>Статистика песен</b>",
        "",
        f"🎵 Всего: <b>{stats['total']}</b>",
        f"🗓 За {stats['days']} дней: <b>{stats['recent']}</b>",
    ]

    if stats["by_chat"]:
        lines.append("")
        lines.append("<b>По чатам (за период):</b>")
        for chat_id, count in stats["by_chat"]:
            label = "—" if chat_id is None else f"<code>{chat_id}</code>"
            lines.append(f"• {label}: {count}")

    # Distribution incl. non-success rows, if any ever get stored.
    non_success = [
        (st, c) for st, c in stats["by_status"] if st != "success"
    ]
    if non_success:
        lines.append("")
        lines.append("<b>Не-success статусы (за период):</b>")
        for st, count in non_success:
            lines.append(f"• <code>{html.escape(st)}</code>: {count}")

    await message.answer("\n".join(lines))


# ---------- /song_purge ----------

@router.message(Command("song_purge"), F.chat.type == ChatType.PRIVATE)
async def cmd_song_purge(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    """OWNER-only: wipe captured chat_messages for one chat (N1.3).

    Two-step: this shows a count + inline confirm. Songs are NOT
    deleted — only the raw message history the summarizer reads.
    """
    user = message.from_user
    if user is None or not await is_owner(session, user.id):
        # Stay quiet-ish: only the owner gets to use this.
        if user is not None and await is_admin(session, user.id):
            await message.answer("🔒 Только владелец бота может чистить историю.")
        return

    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Использование: <code>/song_purge &lt;chat_id&gt;</code>\n\n"
            "Удаляет всю захваченную историю сообщений этого чата "
            "(таблица <code>chat_messages</code>). Песни не трогаются."
        )
        return
    try:
        chat_id = int(raw)
    except ValueError:
        await message.answer("⚠️ chat_id должен быть числом.")
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await message.answer("⚠️ Такого чата нет в базе.")
        return

    n = await count_messages(session, chat_id=chat_id)
    title = html.escape(chat.title or str(chat_id))
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🗑 Да, удалить {n}",
                    callback_data=f"song:purge_yes:{chat_id}",
                ),
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data="song:purge_no",
                ),
            ]
        ]
    )
    await message.answer(
        f"⚠️ <b>Удалить историю чата?</b>\n"
        f"📍 {title}\n\n"
        f"Будет удалено сообщений: <b>{n}</b>.\n"
        "Это необратимо. Песни (mp3/тексты) останутся.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "song:purge_no")
async def cb_song_purge_no(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text("Отменено. История не тронута.")
    await callback.answer()


@router.callback_query(F.data.startswith("song:purge_yes:"))
async def cb_song_purge_yes(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not await is_owner(session, user.id):
        await callback.answer("Только владелец", show_alert=True)
        return
    try:
        chat_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    deleted = await purge_chat_history(session, chat_id)
    log.info(
        "song_purge: owner=%s wiped %d chat_messages for chat=%s",
        user.id,
        deleted,
        chat_id,
    )
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                f"🗑 Удалено сообщений: <b>{deleted}</b>.\n"
                "История чата очищена."
            )
    await callback.answer("Готово")


# ---------- daily-song schedule submenu ----------
@router.callback_query(F.data.startswith("music:song_sched:"))
async def cb_song_sched_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Open the schedule picker for a chat (from its /musicmenu)."""
    if not await _require_admin(callback, session):
        return
    try:
        chat_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                _render_song_schedule_text(chat, tz=settings.tz),
                reply_markup=_song_schedule_keyboard(
                    chat_id,
                    enabled=chat.song_enabled,
                    cron=chat.song_cron,
                ),
            )
    await callback.answer()


@router.callback_query(F.data.startswith("music:song_at:"))
async def cb_song_sched_set(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    """Enable the daily song at the picked HH:MM (global settings.tz)."""
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":")
    # music:song_at:<chat_id>:<hh>:<mm>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
        hh = int(parts[3])
        mm = int(parts[4])
    except ValueError:
        await callback.answer()
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    chat.song_cron = f"{mm} {hh} * * *"
    chat.song_enabled = True
    await session.commit()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)

    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                _render_song_schedule_text(chat, tz=settings.tz),
                reply_markup=_song_schedule_keyboard(
                    chat_id,
                    enabled=chat.song_enabled,
                    cron=chat.song_cron,
                ),
            )
    await callback.answer(f"✅ Включено · {hh:02d}:{mm:02d}")


@router.callback_query(F.data.startswith("music:song_off:"))
async def cb_song_sched_off(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    """Disable the daily song for a chat (keeps the stored time)."""
    if not await _require_admin(callback, session):
        return
    try:
        chat_id = int((callback.data or "").split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    chat.song_enabled = False
    await session.commit()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)

    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                _render_song_schedule_text(chat, tz=settings.tz),
                reply_markup=_song_schedule_keyboard(
                    chat_id,
                    enabled=chat.song_enabled,
                    cron=chat.song_cron,
                ),
            )
    await callback.answer("🚫 Выключено")


__all__ = ["router"]
