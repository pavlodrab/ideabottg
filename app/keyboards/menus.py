from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Admin, Chat
from app.services.schedules import PRESETS

CHATS_PER_PAGE = 6


def home_keyboard(chat_count: int, admin_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📋 Чаты ({chat_count})", callback_data="chat:list:0")],
            [InlineKeyboardButton(text=f"👥 Админы ({admin_count})", callback_data="admin:list")],
        ]
    )


def chats_list_keyboard(chats: list[Chat], page: int) -> InlineKeyboardMarkup:
    total = len(chats)
    start = page * CHATS_PER_PAGE
    end = start + CHATS_PER_PAGE
    page_chats = chats[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for c in page_chats:
        icon = "🟢" if c.is_active else "🔴"
        title = c.title or f"chat {c.chat_id}"
        # 32-char truncation keeps callback under 64 bytes
        label = f"{icon} {title}"[:48]
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"chat:open:{c.chat_id}")]
        )

    nav: list[InlineKeyboardButton] = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"chat:list:{page - 1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"chat:list:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_settings_keyboard(chat: Chat) -> InlineKeyboardMarkup:
    cid = chat.chat_id
    pause_btn = (
        InlineKeyboardButton(text="⏸ Пауза", callback_data=f"chat:pause:{cid}")
        if chat.is_active
        else InlineKeyboardButton(text="▶️ Возобновить", callback_data=f"chat:resume:{cid}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏰ Расписание", callback_data=f"sched:open:{cid}"),
                InlineKeyboardButton(text="✏️ Текст призыва", callback_data=f"prompt:open:{cid}"),
            ],
            [
                pause_btn,
                InlineKeyboardButton(text="📤 Отправить сейчас", callback_data=f"chat:fire:{cid}"),
            ],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="chat:list:0")],
        ]
    )


def schedule_wizard_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for preset in PRESETS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=preset.label,
                    callback_data=f"sched:preset:{chat_id}:{preset.key}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="⌨️ Свой cron", callback_data=f"sched:custom:{chat_id}"),
            InlineKeyboardButton(text="⏸ Выключить", callback_data=f"sched:off:{chat_id}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"chat:open:{chat_id}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prompt_editor_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Сбросить к дефолту", callback_data=f"prompt:reset:{chat_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"chat:open:{chat_id}")],
        ]
    )


def admins_list_keyboard(
    admins: list[Admin], owner_id: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in admins:
        crown = "👑" if a.is_owner else "✅"
        bell = "🟢" if a.receive_ideas else "🔴"
        name = f"@{a.username}" if a.username else f"id {a.user_id}"
        label = f"{crown} {name} {bell}"[:48]
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"admin:open:{a.user_id}")]
        )
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="admin:add")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_card_keyboard(
    admin: Admin, viewer_is_owner: bool
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    bell_label = "🔕 Не получать идеи" if admin.receive_ideas else "🔔 Получать идеи"
    rows.append(
        [InlineKeyboardButton(text=bell_label, callback_data=f"admin:toggle:{admin.user_id}")]
    )
    if viewer_is_owner and not admin.is_owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить админа",
                    callback_data=f"admin:remove:{admin.user_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="admin:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(yes_callback: str, no_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=yes_callback),
                InlineKeyboardButton(text="❌ Отмена", callback_data=no_callback),
            ]
        ]
    )


def cancel_keyboard(back_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=back_callback)]
        ]
    )
