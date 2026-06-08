"""Admin UI: main menu, chat settings, schedule wizard, prompt editor."""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.menus import (
    chat_settings_keyboard,
    chats_list_keyboard,
    home_keyboard,
    prompt_editor_keyboard,
    schedule_wizard_keyboard,
)
from app.models import Chat
from app.services.admins import is_admin, list_admins
from app.services.chats import list_chats, set_chat_active
from app.services.ideas import DEFAULT_PROMPT
from app.services.prompts import send_prompt_to_chat
from app.services.schedules import PRESETS_BY_KEY, humanize_cron
from app.states import PromptEditing, ScheduleCustom

log = logging.getLogger(__name__)

router = Router(name="admin_menu")

PROMPT_PREVIEW_LIMIT = 80


# ---------- gating ----------

async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


# ---------- entry points ----------

@router.message(Command("menu"), F.chat.type == ChatType.PRIVATE)
async def cmd_menu(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    await _send_home(message, session)


@router.message(Command("chats"), F.chat.type == ChatType.PRIVATE)
async def cmd_chats(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    await _send_chats_list(message, session, page=0)


@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    await _edit_home(callback, session)


async def _send_home(message: Message, session: AsyncSession) -> None:
    chats = await list_chats(session)
    admins = await list_admins(session)
    await message.answer(
        _home_text(len(chats), len(admins)),
        reply_markup=home_keyboard(len(chats), len(admins)),
    )


async def _edit_home(callback: CallbackQuery, session: AsyncSession) -> None:
    chats = await list_chats(session)
    admins = await list_admins(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _home_text(len(chats), len(admins)),
            reply_markup=home_keyboard(len(chats), len(admins)),
        )
    await callback.answer()


def _home_text(n_chats: int, n_admins: int) -> str:
    return (
        "🤖 <b>IdeaBot — главное меню</b>\n\n"
        f"📋 Чатов: <b>{n_chats}</b>\n"
        f"👥 Админов: <b>{n_admins}</b>\n\n"
        "Выбери раздел ниже 👇"
    )


# ---------- chat list ----------

@router.callback_query(F.data.startswith("chat:list:"))
async def cb_chats_list(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    page = int(callback.data.split(":")[2]) if callback.data else 0
    if isinstance(callback.message, Message):
        chats = await list_chats(session)
        await callback.message.edit_text(
            _chats_text(chats, page),
            reply_markup=chats_list_keyboard(chats, page),
        )
    await callback.answer()


async def _send_chats_list(message: Message, session: AsyncSession, page: int) -> None:
    chats = await list_chats(session)
    await message.answer(
        _chats_text(chats, page),
        reply_markup=chats_list_keyboard(chats, page),
    )


def _chats_text(chats: list, page: int) -> str:
    if not chats:
        return (
            "📭 <b>Пока ни одного чата.</b>\n\n"
            "Добавь меня в группу — она появится здесь."
        )
    return f"📋 <b>Чаты бота</b> ({len(chats)})\n\nВыбери чат для настройки:"


# ---------- chat settings panel ----------

@router.callback_query(F.data.startswith("chat:open:"))
async def cb_chat_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    await _show_chat_settings(callback, session, chat_id)


async def _show_chat_settings(
    callback: CallbackQuery, session: AsyncSession, chat_id: int
) -> None:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден.", show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _chat_settings_text(chat),
            reply_markup=chat_settings_keyboard(chat),
        )
    await callback.answer()


def _chat_settings_text(chat: Chat) -> str:
    status = "🟢 Активен" if chat.is_active else "🔴 На паузе"
    schedule = humanize_cron(chat.schedule_cron)
    raw_prompt = chat.prompt_text or DEFAULT_PROMPT
    plain = (
        raw_prompt.replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
        .replace("\n", " ")
    )
    if len(plain) > PROMPT_PREVIEW_LIMIT:
        plain = plain[:PROMPT_PREVIEW_LIMIT] + "…"
    is_default = chat.prompt_text is None
    prompt_marker = " <i>(дефолт)</i>" if is_default else ""

    autopub = "🟢 авто-публикация для голосования" if chat.auto_publish else "🔴 авто-публикация выключена"

    title = html.escape(chat.title or f"chat {chat.chat_id}")
    return (
        f"📋 <b>{title}</b>\n"
        f"🆔 <code>{chat.chat_id}</code>\n"
        f"{status}\n"
        f"⏰ {schedule}\n"
        f"✏️ {html.escape(plain)}{prompt_marker}\n"
        f"🗳 {autopub}"
    )


@router.callback_query(F.data.startswith("chat:pause:"))
async def cb_chat_pause(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    await set_chat_active(session, chat_id, False)
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)
    await callback.answer("⏸ Поставлен на паузу")
    await _show_chat_settings(callback, session, chat_id)


@router.callback_query(F.data.startswith("chat:resume:"))
async def cb_chat_resume(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    await set_chat_active(session, chat_id, True)
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)
    await callback.answer("▶️ Возобновлён")
    await _show_chat_settings(callback, session, chat_id)


@router.callback_query(F.data.startswith("chat:fire:"))
async def cb_chat_fire(
    callback: CallbackQuery, bot: Bot, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None or not chat.is_active:
        await callback.answer("⚠️ Чат на паузе или не найден", show_alert=True)
        return
    ok = await send_prompt_to_chat(bot, session, chat)
    await callback.answer("✅ Отправлено" if ok else "⚠️ Не отправилось", show_alert=not ok)


# ---------- schedule wizard ----------

@router.callback_query(F.data.startswith("sched:open:"))
async def cb_sched_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    title = html.escape(chat.title or f"chat {chat.chat_id}")
    text = (
        f"⏰ <b>Расписание для {title}</b>\n\n"
        f"Сейчас: {humanize_cron(chat.schedule_cron)}\n\n"
        "Выбери шаблон или задай свой cron 👇"
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=schedule_wizard_keyboard(chat_id))
    await callback.answer()


@router.callback_query(F.data.startswith("sched:preset:"))
async def cb_sched_preset(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    chat_id = int(parts[2])
    preset = PRESETS_BY_KEY.get(parts[3])
    if preset is None:
        await callback.answer("⚠️ Шаблон не найден", show_alert=True)
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    chat.schedule_cron = preset.cron
    await session.commit()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)
    await callback.answer(f"✅ {preset.label}")
    await _show_chat_settings(callback, session, chat_id)


@router.callback_query(F.data.startswith("sched:off:"))
async def cb_sched_off(
    callback: CallbackQuery, session: AsyncSession, scheduler=None
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    chat.schedule_cron = None
    await session.commit()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)
    await callback.answer("⏸ Расписание выключено")
    await _show_chat_settings(callback, session, chat_id)


@router.callback_query(F.data.startswith("sched:custom:"))
async def cb_sched_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    await state.set_state(ScheduleCustom.waiting_cron)
    await state.update_data(chat_id=chat_id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "⌨️ <b>Свой cron</b>\n\n"
            "Отправь cron-выражение из 5 полей:\n"
            "<code>минута час день месяц день_недели</code>\n\n"
            "Примеры:\n"
            "<code>0 18 * * *</code> — каждый день в 18:00\n"
            "<code>30 9 * * 1-5</code> — будни 09:30\n"
            "<code>0 */4 * * *</code> — каждые 4 часа\n\n"
            "Или /cancel чтобы отменить."
        )
    await callback.answer()


@router.message(ScheduleCustom.waiting_cron, F.chat.type == ChatType.PRIVATE, F.text)
async def receive_custom_cron(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    scheduler=None,
) -> None:
    cron = (message.text or "").strip()
    if cron.startswith("/"):
        return  # let /cancel etc fall through
    try:
        CronTrigger.from_crontab(cron)
    except ValueError as exc:
        await message.answer(f"⚠️ Невалидный cron: {exc}\n\nПопробуй ещё раз или /cancel.")
        return

    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None:
        await state.clear()
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await state.clear()
        await message.answer("⚠️ Чат пропал из базы.")
        return
    chat.schedule_cron = cron
    await session.commit()
    await state.clear()
    if scheduler is not None:
        await scheduler.sync_chat(chat_id)
    await message.answer(
        f"✅ Расписание сохранено: <code>{cron}</code>\n\n"
        "Открой /chats чтобы вернуться."
    )


# ---------- prompt editor ----------

@router.callback_query(F.data.startswith("prompt:open:"))
async def cb_prompt_open(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    await state.set_state(PromptEditing.waiting_text)
    await state.update_data(chat_id=chat_id)
    current = chat.prompt_text or DEFAULT_PROMPT
    is_default = chat.prompt_text is None
    suffix = " <i>(дефолт)</i>" if is_default else ""
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"✏️ <b>Текст призыва</b>\n\n"
            f"Сейчас{suffix}:\n"
            f"━━━━━━━━━━━━\n"
            f"{current}\n"
            f"━━━━━━━━━━━━\n\n"
            "Отправь новый текст следующим сообщением.\n"
            "Поддерживается HTML: &lt;b&gt;, &lt;i&gt;, &lt;u&gt;, &lt;code&gt;.\n\n"
            "Или /cancel.",
            reply_markup=prompt_editor_keyboard(chat_id),
        )
    await callback.answer()


@router.message(PromptEditing.waiting_text, F.chat.type == ChatType.PRIVATE, F.text)
async def receive_prompt_text(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    text = (message.html_text or message.text or "").strip()
    if text.startswith("/"):
        return
    if len(text) < 5:
        await message.answer("Слишком коротко. Попробуй ещё раз или /cancel.")
        return
    if len(text) > 2000:
        await message.answer("Слишком длинно. Уложись в 2000 символов.")
        return

    data = await state.get_data()
    chat_id = data.get("chat_id")
    if chat_id is None:
        await state.clear()
        return
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await state.clear()
        await message.answer("⚠️ Чат пропал из базы.")
        return
    chat.prompt_text = text
    await session.commit()
    await state.clear()
    await message.answer(
        "✅ Текст призыва сохранён.\n\nВот как он будет выглядеть:\n━━━━━━━━━━━━"
    )
    await message.answer(text)


@router.callback_query(F.data.startswith("prompt:reset:"))
async def cb_prompt_reset(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    chat.prompt_text = None
    await session.commit()
    await state.clear()
    await callback.answer("↩️ Сброшено к дефолту")
    await _show_chat_settings(callback, session, chat_id)




@router.callback_query(F.data.startswith("chat:autopub:"))
async def cb_chat_autopub(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    chat_id = int(callback.data.split(":")[2])
    chat = await session.get(Chat, chat_id)
    if chat is None:
        await callback.answer("⚠️ Чат не найден", show_alert=True)
        return
    chat.auto_publish = not chat.auto_publish
    await session.commit()
    await callback.answer(
        "🟢 Авто-голосование включено"
        if chat.auto_publish
        else "🔴 Авто-голосование выключено"
    )
    await _show_chat_settings(callback, session, chat_id)
