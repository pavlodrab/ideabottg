"""Unified ``/musicmenu`` admin home (DM, admin-only).

This is the screen the user asked for: one ``/musicmenu`` with every
piece of bot configuration in two taps — chats, ideas, admins, quiet
hours, in-bot logs, Suno API, OpenRouter, target song duration, and a
chat picker for per-chat song styles.

The per-chat ``/musicmenu`` flow (group context, picks one chat's
default Suno style) lives in :mod:`app.handlers.music`. This handler
only takes over the DM case and the ``home`` / ``mm:home`` callbacks.

Why a new handler file instead of expanding ``admin_menu.py``?
The "home" screen now reads four DB settings (Suno key, Suno model,
LLM key, LLM model) plus the target duration — that's noise that
doesn't belong in the chat / schedule / prompt logic. Splitting keeps
``admin_menu.py`` focused on its actual job (chats and ideas).
"""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.musicmenu import (
    musicmenu_home_keyboard,
    musicmenu_styles_keyboard,
    render_musicmenu_home_text,
)
from app.services.admins import is_admin, list_admins
from app.services.chats import list_chats
from app.services.llm import (
    MODEL_LABEL_BY_SLUG as LLM_MODEL_LABEL_BY_SLUG,
    get_api_key as get_llm_api_key,
    get_model as get_llm_model,
)
from app.services.suno import (
    format_duration_label,
    get_api_key as get_suno_api_key,
    get_model as get_suno_model,
    get_target_duration_sec,
)

log = logging.getLogger(__name__)

router = Router(name="musicmenu_admin")


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


# ---------- shared rendering ----------

async def build_home_view(session: AsyncSession) -> tuple[str, object]:
    """Compute (text, keyboard) for the unified DM home.

    Reads every DB setting it surfaces (Suno + LLM + duration + chat /
    admin counts) in a single pass so callers can ``edit_text`` or
    ``answer`` with one assignment.

    Public so :mod:`app.handlers.admin_menu` can re-use the same view
    when the user opens ``/menu`` (the old alias) or hits a ``home``
    callback button left over from an earlier screen.
    """
    chats = await list_chats(session)
    admins = await list_admins(session)

    suno_key = await get_suno_api_key(session)
    suno_model = await get_suno_model(session)

    llm_key = await get_llm_api_key(session)
    llm_model = await get_llm_model(session)
    llm_label = LLM_MODEL_LABEL_BY_SLUG.get(llm_model, llm_model)

    duration_sec = await get_target_duration_sec(session)
    duration_label = format_duration_label(duration_sec)

    text = render_musicmenu_home_text(
        chat_count=len(chats),
        admin_count=len(admins),
        suno_ok=bool(suno_key),
        llm_ok=bool(llm_key),
        suno_model=suno_model,
        llm_model_label=llm_label,
        target_duration_label=duration_label,
    )
    kb = musicmenu_home_keyboard(
        chat_count=len(chats),
        admin_count=len(admins),
        suno_ok=bool(suno_key),
        llm_ok=bool(llm_key),
        suno_model=suno_model,
        llm_model_label=llm_label,
        target_duration_label=duration_label,
    )
    return text, kb


# ---------- entry: /musicmenu (DM) ----------

@router.message(Command("musicmenu"), F.chat.type == ChatType.PRIVATE)
async def cmd_musicmenu_dm(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    user = message.from_user
    if user is None:
        return
    if not await is_admin(session, user.id):
        # Non-admins only get /musiclist; the menu is admin-only on
        # purpose (it shows API-key state etc.).
        return
    await state.clear()
    text, kb = await build_home_view(session)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


# Aliases — let admins land on the same screen no matter which command
# they remember. ``/menu`` was the old top-level entry; ``/start`` for
# admins now also lands here (handled in app/handlers/common.py).
@router.message(Command("musicmenu_home"), F.chat.type == ChatType.PRIVATE)
async def cmd_musicmenu_home_alias(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await cmd_musicmenu_dm(message, state, session)


@router.callback_query(F.data == "mm:home")
async def cb_mm_home(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.clear()
    text, kb = await build_home_view(session)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )
    await callback.answer()


# Legacy ``home`` callback. Many existing keyboards (suno menu, qh
# panel, chats list, ideas filter, admin card) end with a "🏠 Главное
# меню" button that emits ``home`` — keep that working by routing it
# to the same unified view. This replaces the older
# ``app.handlers.admin_menu.cb_home`` for these flows; the legacy
# implementation was left in place but is now unreachable because
# this router is registered first.
@router.callback_query(F.data == "home")
async def cb_legacy_home(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.clear()
    text, kb = await build_home_view(session)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )
    await callback.answer()


# ---------- mm:styles — chat picker for per-chat song styles ----------

@router.callback_query(F.data == "mm:styles")
async def cb_mm_styles(
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
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                "🎼 <b>Стили песни — выбери чат</b>\n\n"
                "Стиль песни задаётся отдельно для каждого чата. "
                "Когда «Песня дня» сгенерируется в этом чате, она "
                "будет использовать настройку, которую ты тут "
                "поставишь.",
                reply_markup=musicmenu_styles_keyboard(chats),
            )
    await callback.answer()


# ---------- mm:archive — quick jump to /musiclist behaviour ----------

@router.callback_query(F.data == "mm:archive")
async def cb_mm_archive(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Bounce admins to the cross-chat song archive without making them
    type ``/musiclist``. Implementation re-uses the existing
    ``music.py`` rendering so we don't drift in two places.
    """
    if not await _require_admin(callback, session):
        return
    # Local import to avoid a circular module-load (music.py imports
    # from app.handlers.musicmenu_admin? — it doesn't today, but the
    # late import keeps that option open).
    from app.handlers.music import _render_user_page  # noqa: PLC0415

    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    text, kb = await _render_user_page(
        session, user.id, is_admin_user=True, page=0
    )
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    await callback.answer()


__all__ = ["router", "build_home_view"]
