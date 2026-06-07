"""Admin user management: add / remove / toggle receive_ideas."""
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.keyboards.menus import (
    admin_card_keyboard,
    admins_list_keyboard,
    confirm_keyboard,
)
from app.services.admins import (
    add_admin,
    is_admin,
    is_owner,
    list_admins,
    remove_admin,
    toggle_receive_ideas,
)
from app.states import AdminAdd
from app.models import Admin

log = logging.getLogger(__name__)

router = Router(name="admin_users")


async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


# ---------- list ----------

@router.message(Command("admins"), F.chat.type == ChatType.PRIVATE)
async def cmd_admins(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    admins = await list_admins(session)
    await message.answer(
        _admins_text(admins),
        reply_markup=admins_list_keyboard(admins, settings.owner_id),
    )


@router.callback_query(F.data == "admin:list")
async def cb_admin_list(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    admins = await list_admins(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _admins_text(admins),
            reply_markup=admins_list_keyboard(admins, settings.owner_id),
        )
    await callback.answer()


def _admins_text(admins: list[Admin]) -> str:
    return (
        f"👥 <b>Админы</b> ({len(admins)})\n\n"
        "👑 — владелец\n"
        "✅ — админ\n"
        "🟢/🔴 — получает ли идеи\n\n"
        "Тапни по строке для управления."
    )


# ---------- card ----------

@router.callback_query(F.data.startswith("admin:open:"))
async def cb_admin_open(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    user_id = int(callback.data.split(":")[2])
    await _show_admin_card(callback, session, user_id)


async def _show_admin_card(
    callback: CallbackQuery, session: AsyncSession, user_id: int
) -> None:
    admin = await session.get(Admin, user_id)
    if admin is None:
        await callback.answer("⚠️ Админ не найден", show_alert=True)
        return
    viewer = callback.from_user
    viewer_is_owner = (
        viewer is not None and await is_owner(session, viewer.id)
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _admin_card_text(admin),
            reply_markup=admin_card_keyboard(admin, viewer_is_owner),
        )
    await callback.answer()


def _admin_card_text(admin: Admin) -> str:
    role = "👑 Владелец" if admin.is_owner else "✅ Админ"
    name = f"@{admin.username}" if admin.username else f"id {admin.user_id}"
    bell = "🟢 Получает идеи" if admin.receive_ideas else "🔴 Не получает идеи"
    return (
        f"{role}\n"
        f"<b>{html.escape(name)}</b>\n"
        f"🆔 <code>{admin.user_id}</code>\n\n"
        f"{bell}"
    )


# ---------- toggle receive_ideas ----------

@router.callback_query(F.data.startswith("admin:toggle:"))
async def cb_admin_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    user_id = int(callback.data.split(":")[2])
    admin = await toggle_receive_ideas(session, user_id)
    if admin is None:
        await callback.answer("⚠️ Админ не найден", show_alert=True)
        return
    msg = "🟢 Будет получать идеи" if admin.receive_ideas else "🔴 Не будет получать идеи"
    await callback.answer(msg)
    await _show_admin_card(callback, session, user_id)


# ---------- remove (with confirmation) ----------

@router.callback_query(F.data.startswith("admin:remove:"))
async def cb_admin_remove(callback: CallbackQuery, session: AsyncSession) -> None:
    if not await _require_admin(callback, session):
        return
    if callback.from_user is None or not await is_owner(session, callback.from_user.id):
        await callback.answer("Только владелец может удалять", show_alert=True)
        return

    user_id = int(callback.data.split(":")[2])
    admin = await session.get(Admin, user_id)
    if admin is None:
        await callback.answer("⚠️ Не найден", show_alert=True)
        return
    if admin.is_owner:
        await callback.answer("Нельзя удалить владельца", show_alert=True)
        return

    name = f"@{admin.username}" if admin.username else f"id {admin.user_id}"
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🗑 <b>Удалить админа?</b>\n\n{html.escape(name)}",
            reply_markup=confirm_keyboard(
                yes_callback=f"admin:remove_yes:{user_id}",
                no_callback=f"admin:open:{user_id}",
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:remove_yes:"))
async def cb_admin_remove_yes(callback: CallbackQuery, session: AsyncSession) -> None:
    if callback.from_user is None or not await is_owner(session, callback.from_user.id):
        await callback.answer("Только владелец", show_alert=True)
        return
    user_id = int(callback.data.split(":")[2])
    ok = await remove_admin(session, user_id)
    if not ok:
        await callback.answer("Не удалось удалить", show_alert=True)
        return
    await callback.answer("🗑 Удалён")
    # back to admin list
    admins = await list_admins(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _admins_text(admins),
            reply_markup=admins_list_keyboard(admins, settings.owner_id),
        )


# ---------- add admin (FSM) ----------

@router.callback_query(F.data == "admin:add")
async def cb_admin_add(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if callback.from_user is None or not await is_owner(session, callback.from_user.id):
        await callback.answer("Только владелец может добавлять", show_alert=True)
        return

    await state.set_state(AdminAdd.waiting_user)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "➕ <b>Добавить админа</b>\n\n"
            "Пришли мне:\n"
            "• <code>user_id</code> (число), или\n"
            "• перешли любое сообщение от того человека.\n\n"
            "Или /cancel."
        )
    await callback.answer()


@router.message(AdminAdd.waiting_user, F.chat.type == ChatType.PRIVATE)
async def receive_admin_user(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return

    user_id: int | None = None
    username: str | None = None

    # forwarded message — extract user
    if message.forward_from is not None:
        user_id = message.forward_from.id
        username = message.forward_from.username
    elif message.forward_sender_name is not None:
        await message.answer(
            "⚠️ У этого пользователя приватные настройки пересылки. "
            "Попроси его прислать тебе свой <code>user_id</code> "
            "(можно через @userinfobot) и пришли числом."
        )
        return
    else:
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            try:
                user_id = int(text)
            except ValueError:
                user_id = None

    if user_id is None:
        await message.answer(
            "⚠️ Не понял. Пришли число (user_id) или перешли сообщение.\nИли /cancel."
        )
        return

    if user_id <= 0:
        await message.answer("⚠️ user_id должен быть положительным числом.")
        return

    admin, created = await add_admin(session, user_id, username)
    await state.clear()

    name = f"@{admin.username}" if admin.username else f"id {admin.user_id}"
    if created:
        await message.answer(f"✅ Добавлен админ: <b>{html.escape(name)}</b>")
        try:
            await bot.send_message(
                user_id,
                "🎉 Тебя сделали админом IdeaBot.\n"
                "Теперь ты будешь получать идеи в личку.\n\n"
                "Открой /menu чтобы посмотреть настройки.",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("notify new admin %s failed: %s", user_id, exc)
            await message.answer(
                "ℹ️ Я не смог написать ему в личку — пусть сам напишет /start боту."
            )
    else:
        await message.answer(
            f"ℹ️ <b>{html.escape(name)}</b> уже был админом."
        )
