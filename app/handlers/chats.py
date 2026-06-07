"""Chat membership tracking + text-shortcut /pause and /resume.

The interactive chat list is provided by app.handlers.admin_menu; this module
only handles the bot's join/leave events and the text-only fallback toggles.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admins import get_idea_recipients, is_admin
from app.services.chats import set_chat_active, upsert_chat

log = logging.getLogger(__name__)

router = Router(name="chats")

PRESENT_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
ABSENT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


@router.my_chat_member(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL})
)
async def on_my_chat_member(
    event: ChatMemberUpdated,
    bot: Bot,
    session: AsyncSession,
    scheduler=None,
) -> None:
    """Track when the bot is added to or removed from a chat."""
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    chat = event.chat

    became_present = old_status in ABSENT_STATUSES and new_status in PRESENT_STATUSES
    became_absent = old_status in PRESENT_STATUSES and new_status in ABSENT_STATUSES

    if not (became_present or became_absent):
        return

    _, created = await upsert_chat(
        session,
        chat_id=chat.id,
        title=chat.title,
        is_active=became_present,
    )

    if scheduler is not None:
        await scheduler.sync_chat(chat.id)

    log.info(
        "chat_member %s: chat_id=%s title=%r status=%s -> %s (created=%s)",
        "joined" if became_present else "left",
        chat.id,
        chat.title,
        old_status,
        new_status,
        created,
    )

    recipients = await get_idea_recipients(session)
    if not recipients:
        return

    if became_present:
        text = (
            "✅ <b>Бот добавлен в чат</b>\n\n"
            f"📍 {chat.title or chat.id}\n"
            f"🆔 <code>{chat.id}</code>\n\n"
            "Открой /menu чтобы настроить расписание и текст призыва."
        )
    else:
        text = (
            "👋 <b>Бот удалён из чата</b>\n\n"
            f"📍 {chat.title or chat.id}\n"
            f"🆔 <code>{chat.id}</code>\n\n"
            "Чат помечен неактивным. Идеи из него больше не приходят."
        )

    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, text)
        except Exception as exc:  # noqa: BLE001
            log.warning("notify admin %s failed: %s", admin_id, exc)


@router.message(Command("pause"), F.chat.type == ChatType.PRIVATE)
async def cmd_pause(message: Message, session: AsyncSession, scheduler=None) -> None:
    await _toggle_chat_active(message, session, scheduler, active=False)


@router.message(Command("resume"), F.chat.type == ChatType.PRIVATE)
async def cmd_resume(message: Message, session: AsyncSession, scheduler=None) -> None:
    await _toggle_chat_active(message, session, scheduler, active=True)


async def _toggle_chat_active(
    message: Message,
    session: AsyncSession,
    scheduler,
    *,
    active: bool,
) -> None:
    if message.from_user is None or not await is_admin(session, message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        cmd = "resume" if active else "pause"
        await message.answer(
            f"Использование: <code>/{cmd} &lt;chat_id&gt;</code>\n"
            "Удобнее через /menu → Чаты."
        )
        return

    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        await message.answer("⚠️ chat_id должен быть числом.")
        return

    chat = await set_chat_active(session, chat_id, active)
    if chat is None:
        await message.answer("⚠️ Такого чата нет в базе.")
        return

    if scheduler is not None:
        await scheduler.sync_chat(chat_id)

    state = "🟢 активен" if active else "🟡 на паузе"
    await message.answer(f"{state}: <b>{chat.title or chat.chat_id}</b>")
