"""Inline keyboards for /musiclist and /musicmenu.

Callback-data namespace: ``music:*``. None of these collide with any
other namespace in the app (``suno:*``, ``chat:*``, ``ideas:*``,
``admin:*``, ``sched:*``, ``prompt:*``, ``card:*``, ``vote:*``,
``anon:*``, ``tag:*``).

Conventions
-----------
- Pagination row uses ``music:page:<scope>:<scope_id>:<page>``.
  ``scope`` ∈ ``{chat, user}``. ``scope_id`` is the chat_id or user_id.
- Per-song play button: ``music:play:<song_id>``.
- Style picker (per-chat): ``music:style_set:<chat_id>:<slug>``,
  ``music:style_custom:<chat_id>``, ``music:style_reset:<chat_id>``.
- Idle / dummy buttons: ``music:noop``.
"""
from __future__ import annotations

from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


# Style presets. Keep the list short enough that the inline keyboard
# fits on a phone screen without scrolling. Each preset has a slug
# (callback identifier) and a Suno-friendly natural-language prompt
# that goes into the ``style`` field.
STYLE_PRESETS: list[tuple[str, str, str]] = [
    ("pop",        "🎤 Pop",                "modern pop with catchy melodies and clean vocals"),
    ("rock",       "🎸 Rock",               "energetic rock with electric guitars and live drums"),
    ("lofi",       "🌙 Lo-fi",              "chill lo-fi beats, mellow piano, vinyl crackle"),
    ("folk",       "🪕 Folk",               "acoustic folk with finger-picked guitar and warm vocals"),
    ("synthwave",  "🌆 Synthwave",          "retro 80s synthwave with analog synths and driving beat"),
    ("hiphop",     "🎧 Hip-hop",            "boom-bap hip-hop with crisp drums and warm bass"),
    ("classical",  "🎻 Classical",          "orchestral classical with strings and piano"),
    ("jazz",       "🎷 Jazz",               "smooth jazz with saxophone, double bass, brushes"),
    ("electronic", "🎛 Electronic",         "upbeat electronic dance with synth leads"),
    ("ambient",    "🌌 Ambient",            "atmospheric ambient with pads and gentle textures"),
    ("indie",      "🌲 Indie",              "indie rock with reverb-soaked guitar and earnest vocals"),
    ("metal",      "🤘 Metal",              "heavy metal with distorted guitars and powerful drums"),
]

STYLE_PROMPT_BY_SLUG: dict[str, str] = {slug: prompt for slug, _, prompt in STYLE_PRESETS}
STYLE_LABEL_BY_SLUG: dict[str, str] = {slug: label for slug, label, _ in STYLE_PRESETS}


# ---------- /musiclist ----------

def music_list_keyboard(
    *,
    scope: str,                          # "chat" | "user"
    scope_id: int,
    songs_on_page: list,                 # list[Song]
    page: int,
    total: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    """Pagination + per-song play buttons for /musiclist.

    The buttons rendered:

    - One row of ``▶️ 1 / 2 / 3 / …`` numbered after the songs on this
      page (callback ``music:play:<song_id>``).
    - One pagination row with ``⬅️``, page indicator, ``➡️``.
    """
    rows: list[list[InlineKeyboardButton]] = []

    play_row: list[InlineKeyboardButton] = []
    for i, song in enumerate(songs_on_page, start=1):
        play_row.append(
            InlineKeyboardButton(
                text=f"▶️ {i}",
                callback_data=f"music:play:{song.id}",
            )
        )
    if play_row:
        rows.append(play_row)

    nav: list[InlineKeyboardButton] = []
    page_count = max(1, (total + page_size - 1) // page_size)
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"music:page:{scope}:{scope_id}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1} / {page_count}",
            callback_data="music:noop",
        )
    )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"music:page:{scope}:{scope_id}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- /musicmenu ----------

def music_menu_keyboard(
    chat_id: int, current_style: str | None
) -> InlineKeyboardMarkup:
    """Style picker for a single chat.

    Layout: 12 presets in 6 rows of 2 + a Custom / Reset row.
    Currently-active preset (if it matches one) gets a ✅ marker.
    Custom styles can't be detected via slug, so when ``current_style``
    is set to free-text the menu still works — no marker, just shown
    above the buttons.
    """
    rows: list[list[InlineKeyboardButton]] = []

    # Preset grid, 2 per row.
    row: list[InlineKeyboardButton] = []
    for slug, label, _prompt in STYLE_PRESETS:
        marker = "✅ " if _matches(current_style, slug) else ""
        row.append(
            InlineKeyboardButton(
                text=f"{marker}{label}"[:64],
                callback_data=f"music:style_set:{chat_id}:{slug}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Custom + Reset row.
    rows.append(
        [
            InlineKeyboardButton(
                text="✏️ Свой стиль",
                callback_data=f"music:style_custom:{chat_id}",
            ),
            InlineKeyboardButton(
                text="🗑 Сбросить",
                callback_data=f"music:style_reset:{chat_id}",
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def music_chat_picker_keyboard(
    chats: Iterable,
) -> InlineKeyboardMarkup:
    """Lists registered chats as buttons. Used when admin runs
    /musicmenu in DM and we need them to pick which chat to configure.
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
    return InlineKeyboardMarkup(inline_keyboard=rows)


def music_style_back_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Single back button used while waiting for free-text custom style."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"music:menu_open:{chat_id}",
                )
            ]
        ]
    )


# ---------- helpers ----------

def _matches(current: str | None, slug: str) -> bool:
    """Crude check: was the chat's style set from this preset?

    The stored value is the natural-language Suno prompt. We compare
    against the same prompt. (If the user hand-typed something close
    but not identical, we don't claim a match — that's fine.)
    """
    if not current:
        return False
    return current == STYLE_PROMPT_BY_SLUG.get(slug)
