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
        # Admins land directly on the unified menu — same screen as
        # /musicmenu — so the very first message they see has every
        # control reachable in one tap.
        from app.handlers.musicmenu_admin import build_home_view

        text, kb = await build_home_view(session)
        await message.answer(
            text, reply_markup=kb, disable_web_page_preview=True
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
        "<b>Главное меню</b>\n"
        "• /musicmenu — единое меню (чаты, идеи, админы, тишина, "
        "Suno, OpenRouter, длительность, логи)\n"
        "• /menu — алиас на /musicmenu\n\n"
        "<b>Прямые входы</b>\n"
        "• /chats — чаты и их настройки\n"
        "• /ideas — все идеи с фильтрами\n"
        "• /admins — управление админами\n"
        "• /quiet — ночной режим (тишина)\n"
        "• /suno — Suno API (ключ, модель, длительность, тестовая)\n"
        "• /llm — OpenRouter (ключ, модель, system prompt, тест)\n"
        "• /logs — последние логи бота\n\n"
        "<b>Открыто всем</b>\n"
        "• /musiclist — архив сгенерированных песен\n\n"
        "<b>Шорткаты</b>\n"
        "• /pause &lt;chat_id&gt; — пауза\n"
        "• /resume &lt;chat_id&gt; — снова активен\n"
        "• /setcron &lt;chat_id&gt; &lt;cron|off&gt; — расписание текстом\n"
        "• /test_prompt &lt;chat_id&gt; — отправить призыв сейчас\n"
        "• /export [filter] — выгрузить CSV\n"
        "• /captured [chat_id] — статистика захваченных сообщений\n"
        "• /suno_credits — остаток кредитов на Suno\n"
        "• /suno_status &lt;task_id&gt; — статус задачи Suno\n"
        "• /cancel — отменить текущий ввод"
    )
