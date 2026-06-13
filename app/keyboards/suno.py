"""Inline keyboards for the Suno admin UI.

Callback-data namespace: `suno:*` (does not collide with `chat:*`,
`sched:*`, `prompt:*`, `card:*`, `admin:*`, `tag:*`, `anon:*`,
`ideas:*`).
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.suno import (
    DURATION_PRESETS_SEC,
    MODEL_LABELS,
    SUPPORTED_MODELS,
    format_duration_label,
)


def suno_menu_keyboard(
    *, has_api_key: bool, current_model: str, target_duration_sec: int
) -> InlineKeyboardMarkup:
    """Main Suno menu shown on `/suno` and from the home keyboard."""
    rows: list[list[InlineKeyboardButton]] = []

    if has_api_key:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔑 Сменить API-ключ",
                    callback_data="suno:set_key",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="💰 Остаток кредитов",
                    callback_data="suno:credits",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить ключ",
                    callback_data="suno:remove_key",
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔑 Задать API-ключ",
                    callback_data="suno:set_key",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🎚 Модель: {current_model}",
                callback_data="suno:model_open",
            ),
            InlineKeyboardButton(
                text=(
                    f"🎯 Длительность: "
                    f"{format_duration_label(target_duration_sec)}"
                ),
                callback_data="suno:duration_open",
            ),
        ]
    )

    if has_api_key:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧪 Тестовая генерация",
                    callback_data="suno:gen_open",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton(text="🏠 Меню", callback_data="mm:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def suno_model_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """Model picker. Tapping a row sets that model and bounces back to the menu."""
    rows: list[list[InlineKeyboardButton]] = []
    for slug in SUPPORTED_MODELS:
        marker = "✅ " if slug == current_model else ""
        label = MODEL_LABELS.get(slug, slug)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{label}"[:64],
                    callback_data=f"suno:model_set:{slug}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="suno:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def suno_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="suno:home")]
        ]
    )


def suno_remove_key_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить",
                    callback_data="suno:remove_key_yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="suno:home",
                ),
            ]
        ]
    )


def suno_duration_keyboard(current_seconds: int) -> InlineKeyboardMarkup:
    """Pick the target song duration.

    Suno doesn't accept a duration parameter, so the value is steered
    via prompt hints (see ``app.services.suno.append_duration_hint``)
    and — once custom-mode lyrics land in the daily-song pipeline —
    via how many verses the LLM generates.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for sec in DURATION_PRESETS_SEC:
        marker = "✅ " if sec == current_seconds else ""
        row.append(
            InlineKeyboardButton(
                text=f"{marker}{format_duration_label(sec)}",
                callback_data=f"suno:duration_set:{sec}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Свой (секунды)",
                callback_data="suno:duration_custom",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="suno:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
