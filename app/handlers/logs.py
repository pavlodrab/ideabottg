"""``/logs`` admin command + Suno-menu integration.

Reads the in-memory ring buffer (see :mod:`app.services.logs`) and
renders the tail to the current chat. Admin-only, DM-only — same gating
convention as ``/captured`` and ``/suno_credits``.

UX
---

* ``/logs`` (no args) — show the last 50 lines (level filter "all").
* ``/logs warning 100`` — last 100 WARNING+ lines.
* The same data reachable from ``/musicmenu`` via the "📜 Логи" button.
* Inline level switch buttons under each rendered tail.

Keyboards live in this file because they're trivial — no shared state
with other modules.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.admins import is_admin
from app.services.logs import (
    LEVEL_TOKENS,
    get_recent,
    parse_level,
    render_level_label,
    render_lines,
)

router = Router(name="logs")

# Default tail size for an in-message render. Telegram's per-message
# cap is 4096 chars; 50 lines × ~120 chars + HTML overhead fits the
# common case, with a graceful fall-through to a .txt document when
# someone asks for more.
DEFAULT_TAIL = 50

# Beyond this many lines we send a .txt document instead of a message,
# because crossing 4096 chars truncates and Telegram doesn't paginate.
DOCUMENT_THRESHOLD = 80

# Hard cap so a malicious or fat-fingered N can't exceed the buffer.
MAX_TAIL = 500


async def _require_admin(
    cb_or_msg: CallbackQuery | Message, session: AsyncSession
) -> bool:
    user = cb_or_msg.from_user
    if user is None or not await is_admin(session, user.id):
        if isinstance(cb_or_msg, CallbackQuery):
            await cb_or_msg.answer("Только для админов", show_alert=True)
        return False
    return True


def _logs_keyboard(active_token: str, n: int) -> InlineKeyboardMarkup:
    """Level-switch buttons under the rendered tail.

    Each button re-runs the command at the same N but a different level
    filter — admins can flip between "all → WARN+ → ERROR+" without
    retyping.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for token, _level, label in LEVEL_TOKENS:
        marker = "• " if token == active_token else ""
        row.append(
            InlineKeyboardButton(
                text=f"{marker}{label}",
                callback_data=f"logs:tail:{token}:{n}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="📥 Скачать .txt",
                callback_data=f"logs:dump:{active_token}:{MAX_TAIL}",
            ),
            InlineKeyboardButton(
                text="🏠 Меню", callback_data="mm:home"
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("logs"), F.chat.type == ChatType.PRIVATE)
async def cmd_logs(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    if not await _require_admin(message, session):
        return

    args = (command.args or "").strip().split()
    token = "all"
    n = DEFAULT_TAIL
    if args:
        # First arg can be a level token or a number; second arg is the
        # number when first was a token. Order-tolerant on purpose
        # because admins fat-finger this often.
        first = args[0]
        if first.isdigit():
            n = max(1, min(MAX_TAIL, int(first)))
        else:
            token = first.lower()
        if len(args) > 1 and args[1].isdigit():
            n = max(1, min(MAX_TAIL, int(args[1])))

    await _send_tail(message, token=token, n=n)


@router.callback_query(F.data == "logs:home")
async def cb_logs_home(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await _send_tail_via_callback(callback, token="all", n=DEFAULT_TAIL)


@router.callback_query(F.data.startswith("logs:tail:"))
async def cb_logs_tail(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    token = parts[2]
    try:
        n = max(1, min(MAX_TAIL, int(parts[3])))
    except ValueError:
        n = DEFAULT_TAIL
    await _send_tail_via_callback(callback, token=token, n=n)


@router.callback_query(F.data.startswith("logs:dump:"))
async def cb_logs_dump(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    """Send the full retained buffer as a .txt attachment."""
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    token = parts[2]
    try:
        n = max(1, min(MAX_TAIL, int(parts[3])))
    except ValueError:
        n = MAX_TAIL

    level = parse_level(token)
    lines = get_recent(n, min_level=level)
    if not lines:
        await callback.answer("Лог пуст", show_alert=True)
        return

    payload = "\n".join(lines).encode("utf-8")
    file = BufferedInputFile(
        payload, filename=f"ideabottg-logs-{token}-{len(lines)}.txt"
    )
    if isinstance(callback.message, Message):
        await callback.message.answer_document(
            document=file,
            caption=(
                f"📜 Логи · {render_level_label(level)} · "
                f"{len(lines)} строк"
            ),
        )
    await callback.answer()


async def _send_tail(message: Message, *, token: str, n: int) -> None:
    level = parse_level(token)
    lines = get_recent(n, min_level=level)

    header = (
        f"📜 <b>Логи бота</b>\n"
        f"уровень: <code>{render_level_label(level)}</code>  ·  "
        f"строк: <b>{len(lines)}</b>\n\n"
    )

    if len(lines) > DOCUMENT_THRESHOLD:
        # Anything over the threshold won't render legibly in-line —
        # ship it as a .txt instead so it stays readable.
        payload = "\n".join(lines).encode("utf-8")
        file = BufferedInputFile(
            payload,
            filename=f"ideabottg-logs-{token}-{len(lines)}.txt",
        )
        await message.answer_document(
            document=file,
            caption=header + "<i>В сообщение не помещается — отдаю файлом.</i>",
            reply_markup=_logs_keyboard(token, DEFAULT_TAIL),
        )
        return

    body = render_lines(lines)
    await message.answer(
        header + body,
        reply_markup=_logs_keyboard(token, n),
        disable_web_page_preview=True,
    )


async def _send_tail_via_callback(
    callback: CallbackQuery, *, token: str, n: int
) -> None:
    level = parse_level(token)
    lines = get_recent(n, min_level=level)

    header = (
        f"📜 <b>Логи бота</b>\n"
        f"уровень: <code>{render_level_label(level)}</code>  ·  "
        f"строк: <b>{len(lines)}</b>\n\n"
    )
    body = render_lines(lines)
    text = header + body

    # Telegram caps message text at 4096; if our render overflows, fall
    # back to sending a fresh document instead of editing in place.
    if isinstance(callback.message, Message):
        if len(text) <= 4000:
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=_logs_keyboard(token, n),
                    disable_web_page_preview=True,
                )
            except Exception:  # noqa: BLE001
                # Editing can fail when the new text is identical to
                # the existing one — answer the callback so the user
                # gets visual feedback either way.
                pass
        else:
            payload = "\n".join(lines).encode("utf-8")
            file = BufferedInputFile(
                payload,
                filename=f"ideabottg-logs-{token}-{len(lines)}.txt",
            )
            await callback.message.answer_document(
                document=file,
                caption=header + "<i>Длинно для сообщения — файл.</i>",
                reply_markup=_logs_keyboard(token, n),
            )
    await callback.answer()


# Re-export so other modules importing this file get a hint that the
# logging level vocabulary is centralised in services.logs (and not
# duplicated here).
__all__ = ["router"]


# Quiet logger reference so unused-import linters don't strip the
# stdlib import — we use logging in tests / future expansion.
_log = logging.getLogger(__name__)
