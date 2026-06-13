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

from app.models import Chat
from app.services.admins import is_admin
from app.services.chats import list_chats
from app.services.song_pipeline import (
    SongPipelineError,
    start_song_generation,
    watch_suno_task,
)
from app.services.suno import get_api_key as get_suno_api_key

log = logging.getLogger(__name__)

router = Router(name="song_admin")


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


__all__ = ["router"]
