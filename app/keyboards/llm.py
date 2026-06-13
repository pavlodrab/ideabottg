"""Inline keyboards for the OpenRouter (LLM) admin UI.

Callback-data namespace: ``llm:*`` (does not collide with ``suno:*``,
``music:*``, ``mm:*``, ``logs:*``, ``chat:*``, ``ideas:*``, ``admin:*``,
``sched:*``, ``prompt:*``, ``card:*``, ``vote:*``, ``tag:*``, ``anon:*``,
``qh:*``).
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.llm import SUPPORTED_MODELS


def llm_menu_keyboard(
    *, has_api_key: bool, current_model: str
) -> InlineKeyboardMarkup:
    """Main OpenRouter menu shown on ``llm:home`` (entered from
    ``/musicmenu`` or ``/llm`` shortcut)."""
    rows: list[list[InlineKeyboardButton]] = []

    if has_api_key:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔑 Сменить API-ключ",
                    callback_data="llm:set_key",
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="💰 Лимит / usage",
                    callback_data="llm:credits",
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить ключ",
                    callback_data="llm:remove_key",
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔑 Задать API-ключ",
                    callback_data="llm:set_key",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=f"🧠 Модель: {current_model[:32]}",
                callback_data="llm:model_open",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="📝 System prompt",
                callback_data="llm:prompt_open",
            )
        ]
    )

    if has_api_key:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧪 Тестовый запрос",
                    callback_data="llm:test_open",
                )
            ]
        )

    rows.append(
        [InlineKeyboardButton(text="🏠 Меню", callback_data="mm:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def llm_model_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """Model picker. Quick-pick rows + a free-text custom button.

    The currently active slug gets a ✅ marker. If the active slug is
    NOT in :data:`SUPPORTED_MODELS` (because the admin set a custom
    one), no preset is marked but the active value is still rendered
    in the message body.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for slug, label in SUPPORTED_MODELS:
        marker = "✅ " if slug == current_model else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{label}"[:64],
                    callback_data=f"llm:model_set:{slug}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Свой slug",
                callback_data="llm:model_custom",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="llm:home")
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def llm_prompt_keyboard(*, has_override: bool) -> InlineKeyboardMarkup:
    """Buttons under the system-prompt view.

    "Сбросить к дефолту" is only shown when there's actually a custom
    override set, so the button doesn't lie.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data="llm:prompt_edit",
            )
        ]
    ]
    if has_override:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Сбросить к дефолту",
                    callback_data="llm:prompt_reset",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="llm:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def llm_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="llm:home")]
        ]
    )


def llm_remove_key_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Да, удалить",
                    callback_data="llm:remove_key_yes",
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="llm:home",
                ),
            ]
        ]
    )
