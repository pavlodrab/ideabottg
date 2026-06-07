"""Owner-side /ideas browser with status filters and pagination."""
import csv
import html
import io
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
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




# ---------- /export CSV ----------

EXPORT_HEADER = [
    "id",
    "created_at",
    "status",
    "tag",
    "chat_id",
    "chat_title",
    "from_user_id",
    "from_username",
    "is_anonymous",
    "text",
]


@router.message(Command("export"), F.chat.type == ChatType.PRIVATE)
async def cmd_export(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return

    parts = (message.text or "").split(maxsplit=1)
    filter_key = parts[1].strip() if len(parts) > 1 else "all"
    if filter_key not in STATUS_FILTERS:
        await message.answer(
            "⚠️ Фильтр должен быть один из: "
            f"{', '.join(STATUS_FILTERS.keys())}\n"
            "По умолчанию <code>/export</code> = все идеи."
        )
        return

    statuses = STATUS_FILTERS[filter_key]
    stmt = select(Idea).order_by(Idea.created_at.asc())
    if statuses is not None:
        stmt = stmt.where(Idea.status.in_(statuses))
    result = await session.execute(stmt)
    ideas = list(result.scalars().all())

    if not ideas:
        await message.answer("📭 Нечего экспортировать — идей по этому фильтру нет.")
        return

    # resolve chat titles in one query to avoid N+1
    chat_ids = {i.chat_id for i in ideas if i.chat_id is not None}
    chat_titles: dict[int, str] = {}
    if chat_ids:
        result = await session.execute(
            select(Chat.chat_id, Chat.title).where(Chat.chat_id.in_(chat_ids))
        )
        chat_titles = {row[0]: (row[1] or "") for row in result.all()}

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADER)
    for idea in ideas:
        writer.writerow(
            [
                idea.id,
                idea.created_at.astimezone(timezone.utc).isoformat()
                if isinstance(idea.created_at, datetime)
                else "",
                idea.status,
                idea.tag,
                idea.chat_id if idea.chat_id is not None else "",
                chat_titles.get(idea.chat_id, "") if idea.chat_id is not None else "",
                idea.from_user_id,
                idea.from_username or "",
                "1" if idea.is_anonymous else "0",
                (idea.text or "").replace("\r\n", "\n"),
            ]
        )

    data = buffer.getvalue().encode("utf-8-sig")  # BOM helps Excel detect UTF-8
    filename = (
        f"ideas-{filter_key}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    )
    document = BufferedInputFile(data, filename=filename)

    label = dict(IDEAS_FILTERS).get(filter_key, filter_key)
    await message.answer_document(
        document,
        caption=(
            f"📤 <b>Экспорт</b>: {label}\n"
            f"Идей: {len(ideas)}"
        ),
    )
