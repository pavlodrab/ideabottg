from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>👋 Привет!</b>\n\n"
        "Я бот для сбора идей в чатах.\n"
        "Добавь меня в группу, и я буду периодически собирать идеи "
        "от участников и присылать их тебе в личку.\n\n"
        "Команды для админа:\n"
        "• /quiet — ночной режим (тишина)\n"
    )
