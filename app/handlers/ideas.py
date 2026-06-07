import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.prompt import anonymity_keyboard
from app.models import Chat
from app.services.admins import is_admin
from app.services.ideas import (
    create_idea,
    dispatch_idea_to_admins,
    set_idea_status,
)
from app.services.prompts import send_prompt_to_chat
from app.states import IdeaSubmission

log = logging.getLogger(__name__)

router = Router(name="ideas")

MIN_IDEA_LEN = 3
MAX_IDEA_LEN = 4000

STATUS_BADGES = {
    "starred": "⭐ В избранном",
    "read": "✅ Прочитано",
    "archived": "🗑 В архиве",
}
STATUS_BY_ACTION = {"star": "starred", "read": "read", "archive": "archived"}


# ---------- DM: deep-link entry point ----------

@router.message(CommandStart(deep_link=True), F.chat.type == ChatType.PRIVATE)
async def cmd_start_with_payload(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    payload = command.args or ""
    if not payload.startswith("idea_"):
        await message.answer(
            "👋 Привет! Я бот для сбора идей.\nДобавь меня в чат, чтобы начать."
        )
        return

    try:
        chat_id = int(payload[len("idea_"):])
    except ValueError:
        await message.answer("⚠️ Неверная ссылка.")
        return

    chat = await session.get(Chat, chat_id)
    chat_title = chat.title if chat else None

    await state.set_state(IdeaSubmission.waiting_text)
    await state.update_data(chat_id=chat_id, chat_title=chat_title)

    location = f"<b>{html.escape(chat_title)}</b>" if chat_title else "выбранного чата"
    await message.answer(
        f"💡 Опиши идею для {location} одним сообщением 👇\n\n"
        "Можно отменить — /cancel"
    )


@router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("✖️ Отменено.")


# ---------- DM: collect idea text ----------

@router.message(IdeaSubmission.waiting_text, F.chat.type == ChatType.PRIVATE, F.text)
async def receive_idea_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < MIN_IDEA_LEN:
        await message.answer("Слишком коротко. Напиши идею текстом 🙏")
        return
    if len(text) > MAX_IDEA_LEN:
        await message.answer(f"Слишком длинно. Уложись в {MAX_IDEA_LEN} символов.")
        return

    await state.update_data(text=text)
    await state.set_state(IdeaSubmission.waiting_anonymity)
    await message.answer(
        "Отправить анонимно или под своим именем?",
        reply_markup=anonymity_keyboard(),
    )


# ---------- DM: anonymity choice ----------

@router.callback_query(
    IdeaSubmission.waiting_anonymity, F.data.startswith("anon:")
)
async def receive_anonymity(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    session: AsyncSession,
) -> None:
    if callback.data is None or callback.from_user is None:
        await callback.answer()
        return

    choice = callback.data.split(":", 1)[1]

    if choice == "cancel":
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.edit_text("✖️ Отменено.")
        await callback.answer()
        return

    is_anonymous = choice == "1"
    data = await state.get_data()
    text = data.get("text", "")
    chat_id = data.get("chat_id")
    chat_title = data.get("chat_title")
    user = callback.from_user

    idea = await create_idea(
        session,
        chat_id=chat_id,
        from_user_id=user.id,
        from_username=user.username,
        text=text,
        is_anonymous=is_anonymous,
    )
    await state.clear()

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"✅ Идея #{idea.id} отправлена. Спасибо 🙌"
        )
    await callback.answer("Готово!")

    await dispatch_idea_to_admins(bot, session, idea, chat_title)


# ---------- In-chat: hint button ----------

@router.callback_query(F.data == "idea:hint")
async def in_chat_hint(callback: CallbackQuery) -> None:
    await callback.answer(
        "Ответь на это сообщение текстом своей идеи 👇",
        show_alert=True,
    )


# ---------- In-chat: capture replies to the bot's prompt ----------

@router.message(
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
    F.reply_to_message,
    F.text,
)
async def capture_in_chat_reply(
    message: Message, bot: Bot, session: AsyncSession
) -> None:
    reply = message.reply_to_message
    if reply is None or reply.from_user is None or message.from_user is None:
        return

    me = await bot.get_me()
    if reply.from_user.id != me.id:
        return

    chat = await session.get(Chat, message.chat.id)
    if chat is None or chat.last_prompt_message_id != reply.message_id:
        return

    text = (message.text or "").strip()
    if len(text) < MIN_IDEA_LEN or len(text) > MAX_IDEA_LEN:
        return

    user = message.from_user
    idea = await create_idea(
        session,
        chat_id=chat.chat_id,
        from_user_id=user.id,
        from_username=user.username,
        text=text,
        is_anonymous=False,
    )

    try:
        await message.reply(
            f"✅ Идея #{idea.id} принята. Спасибо!",
            disable_notification=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ack reply in chat %s failed: %s", message.chat.id, exc)

    await dispatch_idea_to_admins(bot, session, idea, chat.title)


# ---------- Owner card actions ----------

@router.callback_query(F.data.startswith("card:"))
async def owner_card_action(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if callback.from_user is None or not await is_admin(
        session, callback.from_user.id
    ):
        await callback.answer("Только для админов", show_alert=True)
        return

    if callback.data is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    _, action, idea_id_str = parts
    new_status = STATUS_BY_ACTION.get(action)
    if new_status is None:
        await callback.answer()
        return

    try:
        idea_id = int(idea_id_str)
    except ValueError:
        await callback.answer()
        return

    idea = await set_idea_status(session, idea_id, new_status)
    if idea is None:
        await callback.answer("Идея не найдена", show_alert=True)
        return

    badge = STATUS_BADGES[new_status]
    await callback.answer(badge)

    if isinstance(callback.message, Message):
        try:
            current = callback.message.html_text or ""
            await callback.message.edit_text(
                f"{current}\n\n<i>{badge}</i>",
                reply_markup=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("edit idea card failed: %s", exc)


# ---------- Admin: manual prompt for testing ----------

@router.message(Command("test_prompt"), F.chat.type == ChatType.PRIVATE)
async def cmd_test_prompt(
    message: Message, bot: Bot, session: AsyncSession
) -> None:
    if message.from_user is None or not await is_admin(
        session, message.from_user.id
    ):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: <code>/test_prompt &lt;chat_id&gt;</code>"
        )
        return

    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        await message.answer("⚠️ chat_id должен быть числом.")
        return

    chat = await session.get(Chat, chat_id)
    if chat is None:
        await message.answer("⚠️ Такого чата нет в базе.")
        return
    if not chat.is_active:
        await message.answer("⚠️ Чат на паузе. Сначала /resume.")
        return

    ok = await send_prompt_to_chat(bot, session, chat)
    if ok:
        await message.answer("✅ Призыв отправлен.")
    else:
        await message.answer("⚠️ Не удалось отправить призыв (см. логи).")


# ---------- Admin: minimal cron setter (proper wizard comes in next PR) ----------

@router.message(Command("setcron"), F.chat.type == ChatType.PRIVATE)
async def cmd_setcron(
    message: Message, session: AsyncSession, scheduler=None
) -> None:
    if message.from_user is None or not await is_admin(
        session, message.from_user.id
    ):
        return

    text = (message.text or "").split(maxsplit=2)
    if len(text) < 3:
        await message.answer(
            "Использование: <code>/setcron &lt;chat_id&gt; &lt;cron&gt;</code>\n\n"
            "Примеры:\n"
            "<code>/setcron -100123 0 18 * * *</code> — каждый день в 18:00\n"
            "<code>/setcron -100123 0 12 * * 1</code> — каждый понедельник в 12:00\n"
            "<code>/setcron -100123 0 */3 * * *</code> — каждые 3 часа\n\n"
            "Чтобы выключить расписание: <code>/setcron &lt;chat_id&gt; off</code>"
        )
        return

    try:
        chat_id = int(text[1].strip())
    except ValueError:
        await message.answer("⚠️ chat_id должен быть числом.")
        return

    cron_raw = text[2].strip()
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await message.answer("⚠️ Такого чата нет в базе.")
        return

    if cron_raw.lower() in {"off", "none", "disable"}:
        chat.schedule_cron = None
        await session.commit()
        if scheduler is not None:
            await scheduler.sync_chat(chat_id)
        await message.answer(f"⏸ Расписание отключено для <b>{chat.title or chat_id}</b>.")
        return

    # quick validity check using APScheduler
    from apscheduler.triggers.cron import CronTrigger

    try:
        CronTrigger.from_crontab(cron_raw)
    except ValueError as exc:
        await message.answer(f"⚠️ Невалидный cron: {exc}")
        return

    chat.schedule_cron = cron_raw
    await session.commit()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)

    await message.answer(
        f"✅ Расписание для <b>{chat.title or chat_id}</b>:\n"
        f"<code>{cron_raw}</code>"
    )
