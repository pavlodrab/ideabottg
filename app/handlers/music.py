"""User-facing music UI: ``/musiclist``, ``/musicmenu``, ``/captured``.

- ``/musiclist`` — open to everyone in groups; in DM regular users see
  their own test-generations, admins see the cross-chat archive. Each
  page shows up to 5 songs with a ``▶️ N`` button row to play any of
  them in-place.
- ``/musicmenu`` — admin-only. Picks the default Suno style for a chat.
  In a group: configures that chat. In DM: lists registered chats and
  configures the picked one. Free-text "Custom" style supported.
- ``/captured`` — admin-only DM diagnostic. Shows how many text
  messages the bot logged per chat, both over the retention window
  (default 2 days) and the last 24h. Useful sanity check that the
  capture pipeline is on.

All settings live in DB — no env vars to redeploy.
"""
from __future__ import annotations

import contextlib
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.music import (
    STYLE_LABEL_BY_SLUG,
    STYLE_PROMPT_BY_SLUG,
    music_list_keyboard,
    music_menu_keyboard,
    music_style_back_keyboard,
)
from app.models import Chat, Song
from app.services.admins import is_admin
from app.services.chat_messages import (
    RETENTION_DAYS,
    count_messages,
    oldest_message_at,
)
from app.services.chats import list_chats
from app.services.songs import (
    PAGE_SIZE,
    count_songs_for_chat,
    count_songs_for_user,
    get_song,
    list_songs_for_chat,
    list_songs_for_user,
    set_tg_file_id,
)
from app.states import MusicCustomStyle

log = logging.getLogger(__name__)

router = Router(name="music")

# ---------- /musiclist ----------

@router.message(Command("musiclist"))
async def cmd_musiclist(message: Message, session: AsyncSession) -> None:
    """Open to everyone. In groups, lists that chat's songs. In DM,
    lists the user's own songs (admin sees everything)."""
    user = message.from_user
    if user is None:
        return

    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        text, kb = await _render_chat_page(session, message.chat.id, page=0)
        await message.answer(
            text, reply_markup=kb, disable_web_page_preview=True
        )
        return

    if message.chat.type == ChatType.PRIVATE:
        admin = await is_admin(session, user.id)
        text, kb = await _render_user_page(session, user.id, admin, page=0)
        await message.answer(
            text, reply_markup=kb, disable_web_page_preview=True
        )


@router.callback_query(F.data.startswith("music:page:"))
async def cb_music_page(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    # music : page : <scope> : <scope_id> : <page>
    if len(parts) != 5:
        await callback.answer()
        return
    scope, scope_id_str, page_str = parts[2], parts[3], parts[4]
    try:
        scope_id = int(scope_id_str)
        page = max(0, int(page_str))
    except ValueError:
        await callback.answer()
        return

    if scope == "chat":
        text, kb = await _render_chat_page(session, scope_id, page=page)
    elif scope == "user":
        user = callback.from_user
        if user is None:
            await callback.answer()
            return
        # An admin paginating their own DM list still sees the admin
        # archive — recheck rather than trusting the old scope_id.
        admin = await is_admin(session, user.id)
        text, kb = await _render_user_page(
            session, user.id, admin, page=page
        )
    else:
        await callback.answer()
        return

    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )
    await callback.answer()


@router.callback_query(F.data == "music:noop")
async def cb_music_noop(callback: CallbackQuery) -> None:
    """Silent ack for the page-indicator button."""
    await callback.answer()


@router.callback_query(F.data.startswith("music:play:"))
async def cb_music_play(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        song_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return

    song = await get_song(session, song_id)
    if song is None:
        await callback.answer("Песня не найдена", show_alert=True)
        return

    user = callback.from_user
    if user is None:
        await callback.answer()
        return

    # Permission check — see top of file.
    if isinstance(callback.message, Message):
        chat_type = callback.message.chat.type
        if chat_type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            if song.chat_id is not None and song.chat_id != callback.message.chat.id:
                await callback.answer(
                    "Эта песня — из другого чата", show_alert=True
                )
                return
        else:  # DM
            admin = await is_admin(session, user.id)
            if not admin and song.requested_by != user.id:
                await callback.answer(
                    "Нет доступа к этой песне", show_alert=True
                )
                return
    else:
        await callback.answer()
        return

    # Prefer Telegram's permanent file_id; fall back to Suno's mp3 URL
    # (which expires after 15 days). If neither works, surface a link.
    audio_ref: str | None = song.tg_audio_file_id or song.audio_url
    if audio_ref is None:
        await callback.answer(
            "Файл недоступен — Suno удаляет mp3 через 15 дней.",
            show_alert=True,
        )
        return

    title = song.title or "Suno"
    try:
        sent = await bot.send_audio(
            chat_id=callback.message.chat.id,
            audio=audio_ref,
            title=title,
            performer="Suno",
            caption=f"🎵 {html.escape(title)}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("music play %s failed: %s", song.id, exc)
        # Try a plain link as a last resort.
        if song.audio_url:
            with contextlib.suppress(Exception):
                await bot.send_message(
                    callback.message.chat.id,
                    f"🔗 <a href=\"{html.escape(song.audio_url)}\">"
                    f"{html.escape(title)}</a>",
                    disable_web_page_preview=False,
                )
        await callback.answer("⚠️ Не вышло отправить аудио", show_alert=True)
        return

    # Capture Telegram's file_id on first successful send so future
    # plays are instant (and survive Suno's 15-day URL retention).
    if sent and sent.audio and not song.tg_audio_file_id:
        with contextlib.suppress(Exception):
            await set_tg_file_id(session, song.id, sent.audio.file_id)

    await callback.answer()


# ---------- /musicmenu ----------

@router.message(
    Command("musicmenu"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
)
async def cmd_musicmenu(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Group /musicmenu: per-chat song style picker.

    The DM /musicmenu is handled by ``musicmenu_admin.py`` and shows
    the unified bot-management home. We deliberately split on chat
    type so each context gets the most useful screen first.
    """
    user = message.from_user
    if user is None:
        return
    if not await is_admin(session, user.id):
        # Visible (not silent) so an admin testing in a group can tell
        # the difference between "bot ignored me" and "I'm not a bot
        # admin". Bot-admins = the OWNER_ID + anyone added via /admins
        # in DM — NOT Telegram group admins.
        await message.answer(
            "🔒 Музыкальное меню — только для админов бота.\n"
            "Если это твой бот — открой меню в личке: /musicmenu, "
            "или добавь себя через /admins."
        )
        return

    await state.clear()

    chat = await session.get(Chat, message.chat.id)
    if chat is None:
        # Auto-register: the bot might have been added before chat
        # tracking existed, or the my_chat_member update was missed.
        # Registering here lets the menu work instead of dead-ending.
        from app.services.chats import upsert_chat

        chat, _ = await upsert_chat(
            session, message.chat.id, message.chat.title, True
        )
    await message.answer(
        _render_menu_text(chat),
        reply_markup=music_menu_keyboard(chat.chat_id, chat.song_style),
    )


@router.callback_query(F.data.startswith("music:menu_open:"))
async def cb_music_menu_open(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not await is_admin(session, user.id):
        await callback.answer("Только для админов", show_alert=True)
        return

    parts = (callback.data or "").split(":", 2)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден в базе", show_alert=True)
        return

    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_menu_text(chat),
            reply_markup=music_menu_keyboard(chat.chat_id, chat.song_style),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("music:style_set:"))
async def cb_music_style_set(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not await is_admin(session, user.id):
        await callback.answer("Только для админов", show_alert=True)
        return

    parts = (callback.data or "").split(":")
    # music : style_set : <chat_id> : <slug>
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id = int(parts[2])
    except ValueError:
        await callback.answer()
        return
    slug = parts[3]
    prompt = STYLE_PROMPT_BY_SLUG.get(slug)
    if prompt is None:
        await callback.answer("Неизвестный пресет", show_alert=True)
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    chat.song_style = prompt
    await session.commit()
    await callback.answer(f"✅ {STYLE_LABEL_BY_SLUG.get(slug, slug)}")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_menu_text(chat),
            reply_markup=music_menu_keyboard(chat.chat_id, chat.song_style),
        )


@router.callback_query(F.data.startswith("music:style_reset:"))
async def cb_music_style_reset(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not await is_admin(session, user.id):
        await callback.answer("Только для админов", show_alert=True)
        return

    parts = (callback.data or "").split(":", 2)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    chat.song_style = None
    await session.commit()
    await callback.answer("🗑 Стиль сброшен")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_menu_text(chat),
            reply_markup=music_menu_keyboard(chat.chat_id, chat.song_style),
        )


@router.callback_query(F.data.startswith("music:style_custom:"))
async def cb_music_style_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    user = callback.from_user
    if user is None or not await is_admin(session, user.id):
        await callback.answer("Только для админов", show_alert=True)
        return

    parts = (callback.data or "").split(":", 2)
    try:
        chat_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("Чат не найден", show_alert=True)
        return

    await state.set_state(MusicCustomStyle.waiting_text)
    await state.update_data(chat_id=chat_id)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✏️ <b>Свой стиль для Suno</b>\n\n"
            "Опиши на английском (Suno так лучше понимает) что должно "
            "играть — жанр, темп, инструменты, настроение. "
            "До 500 символов.\n\n"
            "Примеры:\n"
            "• <code>uplifting indie folk with banjo, hand claps, "
            "warm vocals</code>\n"
            "• <code>dark synthwave, slow tempo, analog pads, "
            "moody saxophone</code>\n\n"
            "Или /cancel.",
            reply_markup=music_style_back_keyboard(chat_id),
        )
    await callback.answer()


@router.message(
    MusicCustomStyle.waiting_text, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_custom_style(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return  # let /cancel etc fall through

    if len(text) < 3:
        await message.answer(
            "Слишком коротко. Хотя бы пара слов нужна. Или /cancel."
        )
        return
    if len(text) > 500:
        await message.answer(
            "⚠️ Слишком длинно — лимит 500 символов. Обрежь и пришли ещё раз."
        )
        return

    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None:
        await state.clear()
        await message.answer("⚠️ Потерял контекст. Открой /musicmenu снова.")
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await state.clear()
        await message.answer("⚠️ Чат не найден.")
        return

    chat.song_style = text
    await session.commit()
    await state.clear()

    await message.answer(
        _render_menu_text(chat),
        reply_markup=music_menu_keyboard(chat.chat_id, chat.song_style),
    )


# ---------- /captured ----------

@router.message(Command("captured"), F.chat.type == ChatType.PRIVATE)
async def cmd_captured(message: Message, session: AsyncSession) -> None:
    """Diagnostic: how many chat messages did the bot log?

    Without args lists every registered chat. With ``<chat_id>`` shows
    just that one. Use this to verify the capture middleware is alive.
    """
    user = message.from_user
    if user is None or not await is_admin(session, user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)

    if len(parts) >= 2:
        try:
            chat_id = int(parts[1].strip())
        except ValueError:
            await message.answer("⚠️ chat_id должен быть числом.")
            return
        chat = await session.get(Chat, chat_id)
        if chat is None:
            await message.answer("⚠️ Такого чата нет в базе.")
            return
        total = await count_messages(session, chat_id=chat_id)
        last_24h = await count_messages(
            session, chat_id=chat_id, since=cutoff_24h
        )
        oldest = await oldest_message_at(session, chat_id=chat_id)
        oldest_line = (
            f"Самое старое: <code>{oldest.strftime('%Y-%m-%d %H:%M UTC')}</code>"
            if oldest is not None
            else "Самое старое: —"
        )
        status = "🟢 активен" if chat.is_active else "🟡 на паузе"
        await message.answer(
            f"📊 <b>{html.escape(chat.title or str(chat.chat_id))}</b>\n"
            f"{status}\n\n"
            f"Захвачено за 24 часа: <b>{last_24h}</b>\n"
            f"Всего в базе (≤{RETENTION_DAYS} дн.): <b>{total}</b>\n"
            f"{oldest_line}"
        )
        return

    chats = await list_chats(session)
    if not chats:
        await message.answer(
            "Нет ни одного зарегистрированного чата. "
            "Добавь меня в группу для начала."
        )
        return

    grand_total = await count_messages(session)
    grand_24h = await count_messages(session, since=cutoff_24h)
    grand_oldest = await oldest_message_at(session)

    lines = [
        f"📊 <b>Захвачено сообщений</b>  ·  retention {RETENTION_DAYS} дн.\n",
        f"<b>Итого</b> · 24ч: {grand_24h} · всего: {grand_total}",
    ]
    if grand_oldest is not None:
        lines.append(
            f"<i>Самая старая запись: "
            f"{grand_oldest.strftime('%Y-%m-%d %H:%M UTC')}</i>\n"
        )
    else:
        lines.append("<i>Записей пока нет.</i>\n")
    for chat in chats:
        last_24h = await count_messages(
            session, chat_id=chat.chat_id, since=cutoff_24h
        )
        total = await count_messages(session, chat_id=chat.chat_id)
        emoji = "🟢" if chat.is_active else "🟡"
        title = (chat.title or str(chat.chat_id))[:60]
        lines.append(
            f"{emoji} <b>{html.escape(title)}</b>\n"
            f"   24ч: {last_24h} · всего: {total} · "
            f"<code>{chat.chat_id}</code>"
        )

    await message.answer("\n".join(lines))


# ---------- formatting helpers ----------

def _render_menu_text(chat: Chat) -> str:
    title = html.escape(chat.title or str(chat.chat_id))
    if chat.song_style:
        current = (
            "<b>Стиль:</b> <i>"
            + html.escape(chat.song_style[:200])
            + "</i>"
        )
    else:
        current = "<b>Стиль:</b> <i>не задан</i> (Suno выберет сам)"
    return (
        f"🎵 <b>Музыка для чата</b>\n"
        f"📍 {title}\n\n"
        f"{current}\n\n"
        "Выбери пресет ниже или задай свой стиль текстом. Это будет "
        "стилем по умолчанию для генераций «Песни дня» в этом чате."
    )


async def _render_chat_page(
    session: AsyncSession, chat_id: int, *, page: int
) -> tuple[str, Any]:
    total = await count_songs_for_chat(session, chat_id)
    if total == 0:
        chat = await session.get(Chat, chat_id)
        title = html.escape(chat.title or str(chat_id)) if chat else str(chat_id)
        return (
            f"🎵 <b>Песни чата</b>\n📍 {title}\n\n"
            "Пока ничего не сгенерировано. "
            "Когда фича «Песня дня» поверх соберётся, песни будут "
            "появляться здесь автоматически.",
            None,
        )
    songs = await list_songs_for_chat(
        session, chat_id, page=page, page_size=PAGE_SIZE
    )
    chat = await session.get(Chat, chat_id)
    title = html.escape(chat.title or str(chat_id)) if chat else str(chat_id)
    body = _format_song_lines(songs, page=page, page_size=PAGE_SIZE)
    text = f"🎵 <b>Песни чата</b>\n📍 {title}\n\n{body}"
    kb = music_list_keyboard(
        scope="chat",
        scope_id=chat_id,
        songs_on_page=songs,
        page=page,
        total=total,
        page_size=PAGE_SIZE,
    )
    return text, kb


async def _render_user_page(
    session: AsyncSession, user_id: int, is_admin_user: bool, *, page: int
) -> tuple[str, Any]:
    total = await count_songs_for_user(session, user_id, is_admin=is_admin_user)
    header = (
        "🎵 <b>Архив песен</b> (все чаты)\n\n"
        if is_admin_user
        else "🎵 <b>Твои песни</b> (созданные через /suno)\n\n"
    )
    if total == 0:
        empty = (
            "Здесь пусто. "
            + ("Песни появятся, когда кто-то их сгенерирует."
               if is_admin_user
               else "Сгенерируй первую через /suno → Тестовая генерация.")
        )
        return header + empty, None
    songs = await list_songs_for_user(
        session, user_id, is_admin=is_admin_user, page=page, page_size=PAGE_SIZE
    )
    body = _format_song_lines(songs, page=page, page_size=PAGE_SIZE)
    text = header + body
    kb = music_list_keyboard(
        scope="user",
        scope_id=user_id,
        songs_on_page=songs,
        page=page,
        total=total,
        page_size=PAGE_SIZE,
    )
    return text, kb


def _format_song_lines(
    songs: list[Song], *, page: int, page_size: int
) -> str:
    lines: list[str] = []
    for i, song in enumerate(songs, start=1):
        idx = page * page_size + i  # global index
        title = html.escape(song.title or "(без названия)")
        date = song.created_at.strftime("%d.%m.%Y") if song.created_at else "—"
        meta = [date, html.escape(song.model)]
        if song.duration:
            meta.append(f"{song.duration:.0f} сек")
        meta_str = " · ".join(meta)
        link_part = ""
        if song.audio_url:
            link_part = (
                f"\n   🔗 <a href=\"{html.escape(song.audio_url)}\">mp3</a>"
            )
        lines.append(
            f"<b>{idx}.</b> {title}\n   {meta_str}{link_part}"
        )
    lines.append(
        "\n<i>Tap ▶️ N to play.  Suno hosts mp3 for 15 days; played "
        "tracks are kept in Telegram permanently.</i>"
    )
    return "\n\n".join(lines)
