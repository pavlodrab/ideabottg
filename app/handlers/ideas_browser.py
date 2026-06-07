"""Owner-side /ideas browser with status filters and pagination."""
import html
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.menus import (
    IDEAS_FILTERS,
    idea_view_keyboard,
    ideas_filter_keyboard,
    ideas_list_keyboard,
)
from app.models import Chat, Idea
from app.services.admins import is_admin
from app.services.ideas import (
    STATUS_FILTERS,
    count_ideas,
    format_idea_card,
    list_ideas,
    tag_label,
)

log = logging.getLogger(__name__)

router = Router(name="ideas_browser")

PAGE_SIZE = 8

FILTER_LABELS = dict(IDEAS_FILTERS)


async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


# ---------- entry: /ideas command and filter screen ----------

@router.message(Command("ideas"), F.chat.type == ChatType.PRIVATE)
async def cmd_ideas(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    await _send_filter_screen(message, session, active="new")


@router.callback_query(F.data.startswith("ideas:filter:"))
async def cb_ideas_filter(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = callback.data.split(":") if callback.data else []
    active = parts[2] if len(parts) > 2 else "new"
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            await _filter_text(session),
            reply_markup=ideas_filter_keyboard(active),
        )
    await callback.answer()


async def _send_filter_screen(
    message: Message, session: AsyncSession, *, active: str
) -> None:
    await message.answer(
        await _filter_text(session),
        reply_markup=ideas_filter_keyboard(active),
    )


async def _filter_text(session: AsyncSession) -> str:
    counts = {
        key: await count_ideas(session, status_filter=key)
        for key in STATUS_FILTERS
    }
    return (
        "💡 <b>Идеи</b>\n\n"
        f"🆕 Новые: <b>{counts['new']}</b>\n"
        f"⭐ Избранное: <b>{counts['starred']}</b>\n"
        f"✅ Прочитано: <b>{counts['read']}</b>\n"
        f"🗑 Архив: <b>{counts['archived']}</b>\n"
        f"📋 Всего: <b>{counts['all']}</b>\n\n"
        "Выбери фильтр 👇"
    )


# ---------- list view ----------

@router.callback_query(F.data.startswith("ideas:list:"))
async def cb_ideas_list(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    filter_key = parts[2]
    try:
        page = int(parts[3])
    except ValueError:
        page = 0

    if filter_key not in STATUS_FILTERS:
        filter_key = "new"

    ideas = await list_ideas(
        session, status_filter=filter_key, page=page, page_size=PAGE_SIZE
    )
    total = await count_ideas(session, status_filter=filter_key)
    has_next = (page + 1) * PAGE_SIZE < total

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _list_text(filter_key, page, total),
            reply_markup=ideas_list_keyboard(
                ideas, filter_key=filter_key, page=page, has_next=has_next
            ),
        )
    await callback.answer()


def _list_text(filter_key: str, page: int, total: int) -> str:
    label = FILTER_LABELS.get(filter_key, filter_key)
    if total == 0:
        return f"<b>{label}</b>\n\n📭 Пока пусто."
    return (
        f"<b>{label}</b>  ·  всего {total}\n"
        f"страница {page + 1}\n\n"
        "Тапни идею, чтобы открыть."
    )


# ---------- single idea view ----------

@router.callback_query(F.data.startswith("ideas:open:"))
async def cb_ideas_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        idea_id = int(parts[2])
        page = int(parts[4])
    except ValueError:
        await callback.answer()
        return
    filter_key = parts[3] if parts[3] in STATUS_FILTERS else "new"

    idea = await session.get(Idea, idea_id)
    if idea is None:
        await callback.answer("⚠️ Идея не найдена", show_alert=True)
        return

    chat_title: str | None = None
    if idea.chat_id is not None:
        chat = await session.get(Chat, idea.chat_id)
        chat_title = chat.title if chat else None

    body = format_idea_card(idea, chat_title)
    body += _format_meta(idea)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            body, reply_markup=idea_view_keyboard(idea.id, filter_key, page)
        )
    await callback.answer()


def _format_meta(idea: Idea) -> str:
    status_map = {
        "new": "🆕 новая",
        "starred": "⭐ в избранном",
        "read": "✅ прочитана",
        "archived": "🗑 в архиве",
    }
    status = status_map.get(idea.status, idea.status)
    when = idea.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") \
        if isinstance(idea.created_at, datetime) else ""
    return (
        f"\n━━━━━━━━━━━━\n"
        f"<i>{tag_label(idea.tag)}  ·  {status}  ·  {when}</i>"
    )
