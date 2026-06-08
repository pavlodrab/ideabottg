"""Admin UI for the quiet-hours / night-mode setting.

Entry point: ``/quiet`` command in private chat (admin-only).
Actions:

* ``qh:toggle``    — flip enabled/disabled
* ``qh:preset:K``  — apply one of the named presets
* ``qh:custom``    — open FSM that asks for ``HH:MM-HH:MM``
* ``qh:close``     — dismiss the panel
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.menus import quiet_hours_keyboard
from app.services.admins import is_admin
from app.services.quiet_hours import (
    QUIET_HOURS_PRESETS,
    get_state,
    normalize_hhmm,
    save_to_db,
)
from app.states import QuietHoursEdit

log = logging.getLogger(__name__)

router = Router(name="quiet_hours")


async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


def _panel_text() -> str:
    s = get_state()
    status = "🟢 Включено" if s.enabled else "🔴 Выключено"
    return (
        "🌙 <b>Тишина (ночной режим)</b>\n\n"
        f"{status}\n"
        f"⏰ Окно: <b>{s.start} → {s.end}</b>\n\n"
        "Когда тишина включена и текущее время попадает в окно, "
        "бот не отправляет плановые сообщения. Ответы на команды "
        "от пользователей работают как обычно.\n\n"
        "Окно может проходить через полночь "
        "(<code>23:00 → 08:00</code> = тихо с 23:00 до 08:00 утра)."
    )


# ---------- entry point ----------

@router.message(Command("quiet"), F.chat.type == ChatType.PRIVATE)
async def cmd_quiet(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    await message.answer(_panel_text(), reply_markup=quiet_hours_keyboard())


# ---------- toggle ----------

@router.callback_query(F.data == "qh:toggle")
async def cb_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    s = get_state()
    new_state = await save_to_db(session, enabled=not s.enabled)
    await callback.answer(
        "🟢 Включено" if new_state.enabled else "🔴 Выключено"
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _panel_text(), reply_markup=quiet_hours_keyboard()
        )


# ---------- presets ----------

@router.callback_query(F.data.startswith("qh:preset:"))
async def cb_preset(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    key = (callback.data or "").split(":", 2)[2]
    preset = QUIET_HOURS_PRESETS.get(key)
    if preset is None:
        await callback.answer("⚠️ Шаблон не найден", show_alert=True)
        return
    start, end = preset
    await save_to_db(session, start=start, end=end)
    await callback.answer(f"✅ {start} → {end}")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _panel_text(), reply_markup=quiet_hours_keyboard()
        )


# ---------- custom HH:MM-HH:MM via FSM ----------

@router.callback_query(F.data == "qh:custom")
async def cb_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(QuietHoursEdit.waiting_window)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⌨️ <b>Свой промежуток тишины</b>\n\n"
            "Пришли в формате <code>ЧЧ:ММ-ЧЧ:ММ</code>.\n\n"
            "Примеры:\n"
            "<code>23:00-08:00</code> — классическая ночь\n"
            "<code>22:30-09:30</code> — длиннее\n"
            "<code>13:00-15:00</code> — тихий час днём\n\n"
            "Или /cancel чтобы отменить."
        )
    await callback.answer()


@router.message(QuietHoursEdit.waiting_window, F.chat.type == ChatType.PRIVATE, F.text)
async def receive_window(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        # Let /cancel and friends fall through to their own handlers.
        return

    if "-" not in text:
        await message.answer(
            "⚠️ Формат: <code>HH:MM-HH:MM</code>. Попробуй ещё раз или /cancel."
        )
        return

    raw_start, raw_end = text.split("-", 1)
    try:
        start = normalize_hhmm(raw_start)
        end = normalize_hhmm(raw_end)
    except ValueError as exc:
        await message.answer(
            f"⚠️ Не понял время: {exc}.\nПопробуй ещё раз или /cancel."
        )
        return

    await save_to_db(session, start=start, end=end)
    await state.clear()
    await message.answer(_panel_text(), reply_markup=quiet_hours_keyboard())


# ---------- /cancel during FSM ----------

@router.message(QuietHoursEdit.waiting_window, Command("cancel"))
async def cancel_custom(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=quiet_hours_keyboard())


# ---------- close ----------

@router.callback_query(F.data == "qh:close")
async def cb_close(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        try:
            await callback.message.delete()
        except Exception:
            await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
