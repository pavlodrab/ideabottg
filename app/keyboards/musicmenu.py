"""Inline keyboards + text rendering for the unified ``/musicmenu`` admin home.

When an admin runs ``/musicmenu`` in DM (or any callback returns
``home``), this is the screen they land on. It pulls together every
admin-facing section of the bot:

- Idea / chat / admin management (existing routers).
- Quiet hours (existing).
- Music generation: Suno + OpenRouter + per-chat style + target
  duration + in-bot logs.

Why one big screen?
-------------------

The old top-level ``home_keyboard`` only showed five admin sections
and required a second tap to reach Suno. With OpenRouter on top, log
viewer, and target duration to add, two-tap-to-most-things became the
common case. Lifting the keys + indicators (🟢/🔴) one level up makes
the bot's state legible at a glance.

Callback-data namespace
-----------------------

``mm:*`` for entries owned by this screen (``mm:home``, ``mm:styles``).
The buttons that open existing sections re-use those sections' own
namespaces (``suno:home``, ``llm:home``, ``logs:home``, ``qh:open``,
``ideas:filter:new``, ``admin:list``, ``chat:list:0``) — so a tap on
"Suno API" goes directly into the existing Suno admin handler with no
extra plumbing.
"""
from __future__ import annotations

import html
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def musicmenu_home_keyboard(
    *,
    chat_count: int,
    admin_count: int,
    suno_ok: bool,
    llm_ok: bool,
    suno_model: str,
    llm_model_label: str,
    target_duration_label: str,
) -> InlineKeyboardMarkup:
    """Render the unified admin home keyboard.

    The 🟢/🔴 indicators are computed from whether each provider has an
    API key set in the DB — that's the only piece of state that can
    cause day-to-day "why isn't generation working" surprises, so it's
    worth surfacing on the entry screen.
    """
    suno_label = (
        f"🎚 Suno · {'🟢' if suno_ok else '🔴'} · {suno_model}"
    )[:64]
    llm_label = (
        f"🤖 OpenRouter · {'🟢' if llm_ok else '🔴'} · {llm_model_label}"
    )[:64]

    rows: list[list[InlineKeyboardButton]] = [
        # Existing admin sections (top of screen).
        [
            InlineKeyboardButton(
                text=f"📋 Чаты ({chat_count})",
                callback_data="chat:list:0",
            ),
        ],
        [
            InlineKeyboardButton(
                text="💡 Идеи",
                callback_data="ideas:filter:new",
            ),
            InlineKeyboardButton(
                text=f"👥 Админы ({admin_count})",
                callback_data="admin:list",
            ),
        ],
        [
            InlineKeyboardButton(text="🌙 Тишина", callback_data="qh:open"),
            InlineKeyboardButton(text="📜 Логи", callback_data="logs:home"),
        ],
        # Music generation block. Visually separated by the section
        # heading rendered in the message body (see
        # :func:`render_musicmenu_home_text`).
        [
            InlineKeyboardButton(text=suno_label, callback_data="suno:home"),
        ],
        [
            InlineKeyboardButton(text=llm_label, callback_data="llm:home"),
        ],
        [
            InlineKeyboardButton(
                text=f"🎯 Длительность · {target_duration_label}",
                callback_data="suno:duration_open",
            ),
            InlineKeyboardButton(
                text="🎼 Стили чатов",
                callback_data="mm:styles",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🎵 Архив песен",
                callback_data="mm:archive",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_musicmenu_home_text(
    *,
    chat_count: int,
    admin_count: int,
    suno_ok: bool,
    llm_ok: bool,
    suno_model: str,
    llm_model_label: str,
    target_duration_label: str,
) -> str:
    """Body text shown above the keyboard.

    Mirrors what's encoded in the buttons but in human-readable form so
    admins don't have to decode emoji to know if the bot is "ready".
    """
    suno_status = "🟢 ключ задан" if suno_ok else "🔴 нет ключа"
    llm_status = "🟢 ключ задан" if llm_ok else "🔴 нет ключа"
    return (
        "🎵 <b>Управление ботом</b>\n\n"
        f"📋 Чатов: <b>{chat_count}</b>  ·  👥 Админов: <b>{admin_count}</b>\n\n"
        "──────────  Генерация песни  ──────────\n"
        f"🎚 <b>Suno</b> · {suno_status} · модель <code>"
        f"{html.escape(suno_model)}</code>\n"
        f"🤖 <b>OpenRouter</b> · {llm_status} · модель <code>"
        f"{html.escape(llm_model_label)}</code>\n"
        f"🎯 <b>Длительность</b> · "
        f"<code>{html.escape(target_duration_label)}</code>\n\n"
        "Тапни раздел ниже 👇"
    )


def musicmenu_styles_keyboard(
    chats: Iterable,
) -> InlineKeyboardMarkup:
    """Chat picker for "🎼 Стили чатов" — opens
    :func:`app.keyboards.music.music_menu_keyboard` for the chosen chat.

    Re-uses the existing ``music:menu_open:<chat_id>`` callback, which
    is already handled by :func:`app.handlers.music.cb_music_menu_open`.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for chat in chats:
        title = (chat.title or str(chat.chat_id))[:50]
        emoji = "🟢" if chat.is_active else "🟡"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{emoji} {title}"[:64],
                    callback_data=f"music:menu_open:{chat.chat_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data="mm:home"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
