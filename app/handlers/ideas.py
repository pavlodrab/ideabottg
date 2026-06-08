import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.menus import tag_keyboard
from app.keyboards.prompt import anonymity_keyboard
from app.models import Chat, Idea
from app.services.admins import is_admin
from app.services.ideas import (
    DEFAULT_TAG,
    TAGS_BY_KEY,
    create_idea,
    dispatch_idea_to_admins,
    set_idea_status,
)
from app.services.prompts import send_prompt_to_chat
from app.services.ratelimit import idea_rate_limiter
from app.states import AdminReply, IdeaSubmission

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

MAX_REPLY_LEN = 2000


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

    if message.from_user is not None:
        wait = await idea_rate_limiter.remaining(session, message.from_user.id)
        if wait > 0:
            await message.answer(
                f"⏳ Слишком быстро. Попробуй ещё раз через {wait} сек."
            )
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
    await state.set_state(IdeaSubmission.waiting_tag)
    await message.answer(
        "🏷 <b>Какого типа эта идея?</b>",
        reply_markup=tag_keyboard(),
    )


# ---------- DM: tag selection ----------

@router.callback_query(IdeaSubmission.waiting_tag, F.data.startswith("tag:"))
async def receive_idea_tag(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        await callback.answer()
        return

    choice = callback.data.split(":", 1)[1]
    if choice == "cancel":
        await state.clear()
        if isinstance(callback.message, Message):
            await callback.message.edit_text("✖️ Отменено.")
        await callback.answer()
        return

    tag = choice if choice in TAGS_BY_KEY else DEFAULT_TAG
    await state.update_data(tag=tag)
    await state.set_state(IdeaSubmission.waiting_anonymity)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Отправить анонимно или под своим именем?",
            reply_markup=anonymity_keyboard(),
        )
    await callback.answer()


# ---------- DM: anonymity choice + save ----------

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

    user = callback.from_user

    wait = await idea_rate_limiter.remaining(session, user.id)
    if wait > 0:
        await callback.answer(
            f"⏳ Слишком быстро. Попробуй через {wait} сек.", show_alert=True
        )
        return

    is_anonymous = choice == "1"
    data = await state.get_data()
    text = data.get("text", "")
    chat_id = data.get("chat_id")
    chat_title = data.get("chat_title")
    tag = data.get("tag", DEFAULT_TAG)

    idea = await create_idea(
        session,
        chat_id=chat_id,
        from_user_id=user.id,
        from_username=user.username,
        text=text,
        is_anonymous=is_anonymous,
        tag=tag,
    )
    await state.clear()

    # Auto-publish for voting if the source chat opted in.
    if chat_id is not None:
        chat = await session.get(Chat, chat_id)
        if chat is not None and chat.auto_publish:
            from app.services.voting import publish_idea_to_chat

            await publish_idea_to_chat(bot, session, idea, chat)

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

    wait = await idea_rate_limiter.remaining(session, user.id)
    if wait > 0:
        try:
            await message.reply(
                f"⏳ Слишком быстро. Подожди {wait} сек.",
                disable_notification=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return

    idea = await create_idea(
        session,
        chat_id=chat.chat_id,
        from_user_id=user.id,
        from_username=user.username,
        text=text,
        is_anonymous=False,
        tag=DEFAULT_TAG,
    )

    if chat.auto_publish:
        from app.services.voting import publish_idea_to_chat

        await publish_idea_to_chat(bot, session, idea, chat)

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
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
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

    try:
        idea_id = int(idea_id_str)
    except ValueError:
        await callback.answer()
        return

    if action == "reply":
        await _start_reply_flow(callback, session, state, idea_id)
        return

    if action == "publish":
        await _publish_idea_action(callback, session)
        return

    if action == "refresh":
        await _refresh_idea_card(callback, session)
        return

    new_status = STATUS_BY_ACTION.get(action)
    if new_status is None:
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


# ---------- Owner: reply to author ----------

async def _start_reply_flow(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    idea_id: int,
) -> None:
    idea = await session.get(Idea, idea_id)
    if idea is None:
        await callback.answer("Идея не найдена", show_alert=True)
        return

    await state.set_state(AdminReply.waiting_text)
    await state.update_data(idea_id=idea.id, author_id=idea.from_user_id)

    preview = (idea.text or "").replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:80] + "…"

    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        f"✉️ <b>Ответ на идею #{idea.id}</b>\n\n"
        f"<i>«{html.escape(preview)}»</i>\n\n"
        "Отправь следующим сообщением текст ответа.\n"
        "Автор получит его в личку от меня.\n\n"
        "Отмена — /cancel",
    )


@router.message(AdminReply.waiting_text, F.chat.type == ChatType.PRIVATE, F.text)
async def receive_reply_text(
    message: Message, state: FSMContext, bot: Bot, session: AsyncSession
) -> None:
    text = (message.html_text or message.text or "").strip()
    if text.startswith("/"):
        return
    if len(text) < 1:
        await message.answer("Пустой ответ. Попробуй ещё раз или /cancel.")
        return
    if len(text) > MAX_REPLY_LEN:
        await message.answer(f"Слишком длинно. Уложись в {MAX_REPLY_LEN} символов.")
        return

    data = await state.get_data()
    idea_id = data.get("idea_id")
    author_id = data.get("author_id")
    if not idea_id or not author_id:
        await state.clear()
        return

    idea = await session.get(Idea, idea_id)
    if idea is None:
        await state.clear()
        await message.answer("⚠️ Идея исчезла из базы.")
        return

    preview = (idea.text or "").replace("\n", " ")
    if len(preview) > 120:
        preview = preview[:120] + "…"

    delivered = True
    try:
        await bot.send_message(
            author_id,
            f"💬 <b>Ответ на твою идею #{idea.id}</b>\n\n"
            f"{text}\n\n"
            f"━━━━━━━━━━━━\n"
            f"<i>Идея: «{html.escape(preview)}»</i>",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("deliver reply for idea %s to user %s failed: %s", idea_id, author_id, exc)
        delivered = False

    await state.clear()

    if delivered:
        await message.answer(
            f"✅ Ответ отправлен автору идеи #{idea.id}."
        )
    else:
        await message.answer(
            "⚠️ Не удалось доставить ответ — возможно, пользователь "
            "заблокировал бота или ни разу не писал ему /start."
        )


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


# ---------- Admin: minimal cron setter (proper wizard in admin_menu) ----------

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
            "Удобнее — через /menu → Чаты → Расписание."
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
        await message.answer(
            f"⏸ Расписание отключено для <b>{chat.title or chat_id}</b>."
        )
        return

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




# ---------- Owner: publish for voting / refresh tallies ----------

async def _publish_idea_action(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    try:
        idea_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    idea = await session.get(Idea, idea_id)
    if idea is None:
        await callback.answer("Идея не найдена", show_alert=True)
        return
    if idea.chat_id is None:
        await callback.answer(
            "Эта идея пришла в ЛС — публиковать некуда.", show_alert=True
        )
        return
    if idea.published_message_id is not None:
        await callback.answer("Уже опубликовано", show_alert=True)
        return

    chat = await session.get(Chat, idea.chat_id)
    if chat is None or not chat.is_active:
        await callback.answer(
            "Чат недоступен или на паузе", show_alert=True
        )
        return

    from app.services.voting import publish_idea_to_chat

    sent = await publish_idea_to_chat(callback.bot, session, idea, chat)
    if sent is None:
        await callback.answer(
            "⚠️ Не удалось опубликовать (см. логи)", show_alert=True
        )
        return

    from app.keyboards.prompt import owner_card_keyboard

    await callback.answer("📢 Опубликовано")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=owner_card_keyboard(
                    idea.id,
                    can_publish=False,
                    is_published=True,
                    vote_up=0,
                    vote_down=0,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("update card after publish failed: %s", exc)


async def _refresh_idea_card(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    parts = (callback.data or "").split(":")
    try:
        idea_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer()
        return

    idea = await session.get(Idea, idea_id)
    if idea is None:
        await callback.answer("Идея не найдена", show_alert=True)
        return

    from app.keyboards.prompt import owner_card_keyboard
    from app.services.voting import get_vote_totals

    is_published = idea.published_message_id is not None
    up = down = 0
    if is_published:
        up, down = await get_vote_totals(session, idea.id)

    await callback.answer(f"👍 {up}  👎 {down}")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(
                reply_markup=owner_card_keyboard(
                    idea.id,
                    can_publish=(idea.chat_id is not None) and not is_published,
                    is_published=is_published,
                    vote_up=up,
                    vote_down=down,
                )
            )
        except Exception:  # noqa: BLE001 — keyboard unchanged is a no-op error
            pass
