from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def prompt_keyboard(bot_username: str, chat_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard attached to the periodic 'share an idea' prompt."""
    deep_link = f"https://t.me/{bot_username}?start=idea_{chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✍️ В личку", url=deep_link),
                InlineKeyboardButton(
                    text="💬 Ответить здесь", callback_data="idea:hint"
                ),
            ],
        ]
    )


def anonymity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🙈 Анонимно", callback_data="anon:1"),
                InlineKeyboardButton(text="✍️ С именем", callback_data="anon:0"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="anon:cancel")],
        ]
    )


def owner_card_keyboard(idea_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐", callback_data=f"card:star:{idea_id}"
                ),
                InlineKeyboardButton(
                    text="✅", callback_data=f"card:read:{idea_id}"
                ),
                InlineKeyboardButton(
                    text="🗑", callback_data=f"card:archive:{idea_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✉️ Ответить автору",
                    callback_data=f"card:reply:{idea_id}",
                )
            ],
        ]
    )
