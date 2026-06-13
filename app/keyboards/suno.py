"""Inline keyboards for the Suno admin UI.

Callback-data namespace: `suno:*` (does not collide with `chat:*`,
`sched:*`, `prompt:*`, `card:*`, `admin:*`, `tag:*`, `anon:*`,
`ideas:*`).
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.suno import MODEL_LABELS, SUPPORTED_MODELS


def suno_menu_keyboard(
    *, has_api_key: bool, current_model: str
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
            )
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
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]
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
