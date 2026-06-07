"""Weekly/daily digest generator and sender.

A digest is the alternative to streaming idea cards into an admin's DM.
Each digest-mode admin has their own cron schedule and `last_digest_at`
watermark. When the scheduler fires, this module gathers ideas created
since the watermark, formats a compact summary, sends it, and advances
the watermark.
"""
import html
import logging
from collections import defaultdict
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin, Chat, Idea
from app.services.ideas import TAGS_BY_KEY

log = logging.getLogger(__name__)

MAX_PER_CHAT_LINES = 5
TG_MAX_LEN = 4000  # leave headroom under the 4096 hard limit


async def send_digest_to_admin(
    bot: Bot, session: AsyncSession, admin: Admin
) -> bool:
    """Build and send a digest. Always advances last_digest_at; returns True
    if a non-empty digest was actually delivered."""
    since = admin.last_digest_at or admin.created_at
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(Idea).where(Idea.created_at > since).order_by(Idea.created_at.asc())
    )
    ideas = list(result.scalars().all())

    # Always advance the watermark so we don't repeat content even on
    # empty windows or delivery failures.
    admin.last_digest_at = now
    await session.commit()

    if not ideas:
        try:
            await bot.send_message(
                admin.user_id,
                _empty_digest_text(since, now),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "deliver empty digest to %s failed: %s", admin.user_id, exc
            )
        return False

    chat_ids = {i.chat_id for i in ideas if i.chat_id is not None}
    chat_titles: dict[int, str] = {}
    if chat_ids:
        result = await session.execute(
            select(Chat.chat_id, Chat.title).where(Chat.chat_id.in_(chat_ids))
        )
        chat_titles = {row[0]: (row[1] or "") for row in result.all()}

    text = _format_digest(ideas, chat_titles, since, now)
    try:
        await bot.send_message(admin.user_id, text)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("deliver digest to %s failed: %s", admin.user_id, exc)
        return False


def _format_digest(
    ideas: list[Idea],
    chat_titles: dict[int, str],
    since: datetime,
    now: datetime,
) -> str:
    grouped: dict[int | None, list[Idea]] = defaultdict(list)
    for idea in ideas:
        grouped[idea.chat_id].append(idea)

    header = (
        f"📊 <b>Дайджест идей</b>\n"
        f"{since.strftime('%Y-%m-%d %H:%M')} — {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
        f"Всего: <b>{len(ideas)}</b>\n"
    )

    sections: list[str] = []
    for chat_id, items in sorted(
        grouped.items(),
        key=lambda kv: -len(kv[1]),
    ):
        title = (
            html.escape(chat_titles.get(chat_id) or f"chat {chat_id}")
            if chat_id is not None
            else "ЛС"
        )
        section = [f"\n📍 <b>{title}</b> ({len(items)})"]
        for idea in items[:MAX_PER_CHAT_LINES]:
            section.append(_format_line(idea))
        remaining = len(items) - MAX_PER_CHAT_LINES
        if remaining > 0:
            section.append(f"  <i>… ещё {remaining}</i>")
        sections.append("\n".join(section))

    body = header + "".join(sections) + "\n\nОткрой /ideas чтобы увидеть все."

    if len(body) > TG_MAX_LEN:
        # Fallback: send a counts-only summary if the full digest is too long.
        body = (
            header
            + "\n"
            + "\n".join(
                f"📍 {html.escape(chat_titles.get(cid) or (f'chat {cid}' if cid is not None else 'ЛС'))}"
                f" — {len(items)}"
                for cid, items in sorted(grouped.items(), key=lambda kv: -len(kv[1]))
            )
            + "\n\nОткрой /ideas чтобы увидеть все."
        )

    return body


def _format_line(idea: Idea) -> str:
    tag = TAGS_BY_KEY.get(idea.tag) or TAGS_BY_KEY["other"]
    if idea.is_anonymous:
        author = "Аноним"
    elif idea.from_username:
        author = f"@{idea.from_username}"
    else:
        author = f"id {idea.from_user_id}"
    preview = (idea.text or "").replace("\n", " ")
    if len(preview) > 60:
        preview = preview[:60] + "…"
    return f"  {tag.icon} #{idea.id} «{html.escape(preview)}» — {html.escape(author)}"


def _empty_digest_text(since: datetime, now: datetime) -> str:
    return (
        "📊 <b>Дайджест идей</b>\n"
        f"{since.strftime('%Y-%m-%d %H:%M')} — {now.strftime('%Y-%m-%d %H:%M')} UTC\n\n"
        "📭 За этот период новых идей не было."
    )
