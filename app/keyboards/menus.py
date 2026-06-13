from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Admin, Chat
from app.services.schedules import PRESETS

CHATS_PER_PAGE = 6


def home_keyboard(chat_count: int, admin_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📋 Чаты ({chat_count})", callback_data="chat:list:0")],
            [InlineKeyboardButton(text="💡 Идеи", callback_data="ideas:filter:new")],
            [InlineKeyboardButton(text=f"👥 Админы ({admin_count})", callback_data="admin:list")],
            [InlineKeyboardButton(text="🎵 Suno API", callback_data="suno:home")],
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
    autopub_label = (
        "🗳 Авто-голосование: 🟢"
        if chat.auto_publish
        else "🗳 Авто-голосование: 🔴"
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
            [InlineKeyboardButton(text=autopub_label, callback_data=f"chat:autopub:{cid}")],
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

    if admin.delivery_mode == "stream":
        rows.append(
            [
                InlineKeyboardButton(
                    text="📊 Перейти на дайджест",
                    callback_data=f"admin:mode:{admin.user_id}:digest",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔔 Перейти на поток",
                    callback_data=f"admin:mode:{admin.user_id}:stream",
                ),
                InlineKeyboardButton(
                    text="📤 Отправить дайджест сейчас",
                    callback_data=f"admin:digest_now:{admin.user_id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="⏰ Расписание дайджеста",
                    callback_data=f"admin:digest_sched:{admin.user_id}",
                )
            ]
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


DIGEST_PRESETS: list[tuple[str, str, str]] = [
    ("daily_09", "Каждый день 09:00", "0 9 * * *"),
    ("daily_18", "Каждый день 18:00", "0 18 * * *"),
    ("weekly_mon_09", "Понедельник 09:00", "0 9 * * 1"),
    ("weekly_fri_18", "Пятница 18:00", "0 18 * * 5"),
    ("weekly_sun_18", "Воскресенье 18:00", "0 18 * * 0"),
]


def digest_schedule_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label, _ in DIGEST_PRESETS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"admin:digest_set:{user_id}:{key}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"admin:open:{user_id}",
            )
        ]
    )
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


def tag_keyboard() -> InlineKeyboardMarkup:
    from app.services.ideas import TAGS

    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(TAGS), 2):
        row: list[InlineKeyboardButton] = []
        for tag in TAGS[i : i + 2]:
            row.append(
                InlineKeyboardButton(
                    text=f"{tag.icon} {tag.label}",
                    callback_data=f"tag:{tag.key}",
                )
            )
        rows.append(row)
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tag:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


IDEAS_FILTERS = [
    ("new", "🆕 Новые"),
    ("starred", "⭐ Избранное"),
    ("read", "✅ Прочитано"),
    ("archived", "🗑 Архив"),
    ("all", "📋 Все"),
]


def ideas_filter_keyboard(active: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, label in IDEAS_FILTERS:
        marker = "• " if key == active else ""
        row.append(
            InlineKeyboardButton(
                text=f"{marker}{label}",
                callback_data=f"ideas:list:{key}:0",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ideas_list_keyboard(
    ideas: list,
    *,
    filter_key: str,
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    from app.services.ideas import TAGS_BY_KEY

    rows: list[list[InlineKeyboardButton]] = []
    for idea in ideas:
        tag_info = TAGS_BY_KEY.get(idea.tag) or TAGS_BY_KEY["other"]
        preview = (idea.text or "").replace("\n", " ")[:40]
        label = f"{tag_info.icon} #{idea.id} {preview}"[:60]
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"ideas:open:{idea.id}:{filter_key}:{page}",
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=f"ideas:list:{filter_key}:{page - 1}"
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                text="➡️", callback_data=f"ideas:list:{filter_key}:{page + 1}"
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Сменить фильтр", callback_data=f"ideas:filter:{filter_key}"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def idea_view_keyboard(
    idea_id: int,
    filter_key: str,
    page: int,
    *,
    can_publish: bool = False,
    is_published: bool = False,
    vote_up: int = 0,
    vote_down: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="⭐", callback_data=f"card:star:{idea_id}"),
            InlineKeyboardButton(text="✅", callback_data=f"card:read:{idea_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"card:archive:{idea_id}"),
        ],
        [
            InlineKeyboardButton(
                text="✉️ Ответить автору",
                callback_data=f"card:reply:{idea_id}",
            )
        ],
    ]
    if is_published:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 Опубликовано · 👍 {vote_up}  👎 {vote_down}",
                    callback_data=f"card:refresh:{idea_id}",
                )
            ]
        )
    elif can_publish:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать в чат",
                    callback_data=f"card:publish:{idea_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data=f"ideas:list:{filter_key}:{page}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
