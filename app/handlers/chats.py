import logging

from aiogram import Bot, Router, F
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admins import get_idea_recipients, is_admin
from app.services.chats import list_chats, set_chat_active, upsert_chat

log = logging.getLogger(__name__)

router = Router(name="chats")

PRESENT_STATUSES = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR}
ABSENT_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


def _format_chat_line(title: str | None, chat_id: int, is_active: bool) -> str:
    icon = "🟢" if is_active else "🔴"
    name = title or f"chat {chat_id}"
    return f"{icon} <b>{name}</b>\n   <code>{chat_id}</code>"


@router.my_chat_member(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}))
async def on_my_chat_member(
    event: ChatMemberUpdated, bot: Bot, session: AsyncSession
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
            "Расписание ещё не настроено. Открой /chats, чтобы задать его."
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


@router.message(Command("chats"), F.chat.type == ChatType.PRIVATE)
async def cmd_chats(message: Message, session: AsyncSession) -> None:
    if message.from_user is None or not await is_admin(session, message.from_user.id):
        return

    chats = await list_chats(session)
    if not chats:
        await message.answer(
            "📭 <b>Пока ни одного чата.</b>\n\n"
            "Добавь меня в группу — она появится здесь автоматически."
        )
        return

    lines = [_format_chat_line(c.title, c.chat_id, c.is_active) for c in chats]
    body = "\n\n".join(lines)
    await message.answer(f"<b>📋 Чаты бота</b>\n\n{body}")


@router.message(Command("pause"), F.chat.type == ChatType.PRIVATE)
async def cmd_pause(message: Message, session: AsyncSession) -> None:
    await _toggle_chat_active(message, session, active=False)


@router.message(Command("resume"), F.chat.type == ChatType.PRIVATE)
async def cmd_resume(message: Message, session: AsyncSession) -> None:
    await _toggle_chat_active(message, session, active=True)


async def _toggle_chat_active(
    message: Message, session: AsyncSession, *, active: bool
) -> None:
    if message.from_user is None or not await is_admin(session, message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        cmd = "resume" if active else "pause"
        await message.answer(
            f"Использование: <code>/{cmd} &lt;chat_id&gt;</code>\n"
            "Список чатов — /chats"
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

    state = "🟢 активен" if active else "🟡 на паузе"
    await message.answer(f"{state}: <b>{chat.title or chat.chat_id}</b>")
