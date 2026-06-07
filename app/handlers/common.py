from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admins import is_admin

router = Router(name="common")


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: Message, session: AsyncSession) -> None:
    user = message.from_user
    if user is not None and await is_admin(session, user.id):
        await message.answer(
            "🤖 <b>IdeaBot</b>\n\n"
            "Я собираю идеи в твоих чатах.\n\n"
            "Команды:\n"
            "• /menu — главное меню\n"
            "• /chats — список чатов и настройки\n"
            "• /admins — управление админами\n"
            "• /help — справка"
        )
        return

    await message.answer(
        "👋 <b>Привет!</b>\n\n"
        "Я бот для сбора идей. Если ты участник чата, где я работаю, "
        "просто жми кнопку «✍️ В личку» под моими сообщениями там — "
        "и пиши свою идею здесь."
    )


@router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
async def cmd_help(message: Message, session: AsyncSession) -> None:
    user = message.from_user
    if user is None or not await is_admin(session, user.id):
        await message.answer(
            "Если хочешь поделиться идеей — жми кнопку под призывом в чате "
            "или используй ссылку, которую ведущий тебе пришлёт."
        )
        return

    await message.answer(
        "🤖 <b>Команды админа</b>\n\n"
        "<b>Меню</b>\n"
        "• /menu — главное меню\n"
        "• /chats — чаты и их настройки\n"
        "• /ideas — все идеи с фильтрами\n"
        "• /admins — управление админами\n\n"
        "<b>Шорткаты</b>\n"
        "• /pause &lt;chat_id&gt; — пауза\n"
        "• /resume &lt;chat_id&gt; — снова активен\n"
        "• /setcron &lt;chat_id&gt; &lt;cron|off&gt; — расписание текстом\n"
        "• /test_prompt &lt;chat_id&gt; — отправить призыв сейчас\n"
        "• /cancel — отменить текущий ввод"
    )
