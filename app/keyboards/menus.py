"""Inline keyboards for admin menus."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.quiet_hours import QUIET_HOURS_PRESET_ROWS, get_state as get_qh_state


def quiet_hours_keyboard() -> InlineKeyboardMarkup:
    """Render the quiet-hours panel keyboard.

    Layout:

        [ toggle (🟢 / 🔴) ]
        [ preset 1 ] [ preset 2 ]
        [ preset 3 ] [ preset 4 ]
        [ ⌨️ Свой промежуток ]
        [ ❌ Закрыть ]
    """
    s = get_qh_state()
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🔴 Выключить" if s.enabled else "🟢 Включить",
                callback_data="qh:toggle",
            )
        ]
    ]
    # Two presets per row to keep the panel compact.
    row: list[InlineKeyboardButton] = []
    for key, label in QUIET_HOURS_PRESET_ROWS:
        row.append(
            InlineKeyboardButton(text=label, callback_data=f"qh:preset:{key}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="⌨️ Свой промежуток", callback_data="qh:custom")]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="qh:close")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
