"""Suno API admin UI: API-key, model, credits, test generation.

All settings are stored in the `settings` table (DB-backed) so the bot
owner configures everything from inside Telegram — no env vars, no
redeploy. See `app/services/suno.py` for the keys.

Callback-data namespace: `suno:*` (does not collide with existing
`chat:*` / `sched:*` / `prompt:*` / `card:*` / `admin:*` / etc).
"""
from __future__ import annotations

import asyncio
import contextlib
import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.keyboards.suno import (
    suno_back_keyboard,
    suno_duration_keyboard,
    suno_menu_keyboard,
    suno_model_keyboard,
    suno_remove_key_confirm_keyboard,
)
from app.services.admins import is_admin
from app.services.songs import set_tg_file_id, upsert_song
from app.services.suno import (
    DEFAULT_CALLBACK_URL,
    DURATION_PRESETS_SEC,
    MAX_TARGET_DURATION_SEC,
    MIN_TARGET_DURATION_SEC,
    MODEL_LABELS,
    SUPPORTED_MODELS,
    SunoApiError,
    SunoApiOrgClient,
    TaskSnapshot,
    append_duration_hint,
    clear_api_key,
    format_duration_label,
    get_api_key,
    get_callback_url,
    get_model,
    get_target_duration_sec,
    mask_key,
    set_api_key,
    set_model,
    set_target_duration_sec,
)
from app.states import (
    SunoApiKeyEditing,
    SunoDurationCustom,
    SunoTestPrompt,
)

log = logging.getLogger(__name__)

router = Router(name="suno_admin")

# How long we will wait for a generation task to finish before giving up
# the background poller. The Suno docs say full mp3 takes 2-3 minutes;
# we give it a generous ceiling and edit the chat message when ready.
TEST_GEN_TIMEOUT_SEC = 360
TEST_GEN_POLL_INTERVAL_SEC = 15
PROMPT_MAX_LEN = 500  # non-custom mode limit per Suno docs


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


# ---------- entry: /suno + suno:home ----------

@router.message(Command("suno"), F.chat.type == ChatType.PRIVATE)
async def cmd_suno(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    text, kb = await _build_menu(session)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "suno:home")
async def cb_suno_home(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.clear()
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


async def _build_menu(session: AsyncSession) -> tuple[str, object]:
    api_key = await get_api_key(session)
    model = await get_model(session)
    callback_url = await get_callback_url(session)
    target_duration = await get_target_duration_sec(session)

    masked = mask_key(api_key)
    if api_key:
        status_line = f"🟢 <b>API-ключ задан</b> · <code>{html.escape(masked)}</code>"
        hint = (
            "Открой «🧪 Тестовая генерация», чтобы прогнать пайплайн на одной "
            "короткой подсказке и убедиться, что всё работает."
        )
    else:
        status_line = "🔴 <b>API-ключ не задан</b>"
        hint = (
            "Возьми ключ на <a href=\"https://sunoapi.org/api-key\">"
            "sunoapi.org/api-key</a> и нажми «🔑 Задать API-ключ»."
        )

    text = (
        "🎵 <b>Suno API</b>  · <code>sunoapi.org</code>\n\n"
        f"{status_line}\n"
        f"🎚 Модель по умолчанию: <code>{html.escape(model)}</code>\n"
        f"🎯 Целевая длительность: <code>"
        f"{format_duration_label(target_duration)}</code> "
        f"(~{target_duration} сек)\n"
        f"🔁 Callback URL: <code>{html.escape(callback_url)}</code>\n\n"
        f"{hint}"
    )
    kb = suno_menu_keyboard(
        has_api_key=bool(api_key),
        current_model=model,
        target_duration_sec=target_duration,
    )
    return text, kb


# ---------- API key: set ----------

@router.callback_query(F.data == "suno:set_key")
async def cb_suno_set_key(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(SunoApiKeyEditing.waiting_key)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🔑 <b>API-ключ Suno</b>\n\n"
            "Пришли ключ одним сообщением. Получить ключ можно тут:\n"
            "<a href=\"https://sunoapi.org/api-key\">"
            "sunoapi.org/api-key</a>\n\n"
            "Я сразу проверю его звонком к API (запросом баланса) и "
            "удалю это сообщение из истории, чтобы ключ не светился.\n\n"
            "Или /cancel.",
            reply_markup=suno_back_keyboard(),
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.message(
    SunoApiKeyEditing.waiting_key, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_api_key(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return  # let /cancel and other commands fall through

    # Try to delete the user's message so the key doesn't linger in history.
    with contextlib.suppress(Exception):
        await message.delete()

    if len(raw) < 8:
        await message.answer(
            "⚠️ Слишком короткий ключ. Перепроверь и пришли ещё раз, или /cancel.",
            reply_markup=suno_back_keyboard(),
        )
        return

    # Validate by hitting /api/v1/generate/credit. If the call fails we do
    # NOT save the key — better to surface a clear error than silently store
    # a bad value.
    client = SunoApiOrgClient(raw)
    try:
        credits = await client.get_credits()
    except SunoApiError as exc:
        await message.answer(
            "❌ <b>Ключ не принят Suno API.</b>\n\n"
            f"Причина: {html.escape(exc.humanized())}\n\n"
            "Перепроверь ключ на "
            "<a href=\"https://sunoapi.org/api-key\">sunoapi.org/api-key</a> "
            "и попробуй ещё раз. Или /cancel.",
            reply_markup=suno_back_keyboard(),
            disable_web_page_preview=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected error validating suno key")
        await message.answer(
            f"❌ Не получилось проверить ключ: <code>{html.escape(str(exc))}</code>\n\n"
            "Попробуй ещё раз или /cancel.",
            reply_markup=suno_back_keyboard(),
        )
        return

    await set_api_key(session, raw)
    await state.clear()

    text, kb = await _build_menu(session)
    await message.answer(
        f"✅ <b>API-ключ сохранён.</b>\n"
        f"💰 Текущий баланс: <b>{credits}</b> кредитов\n\n" + text,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


# ---------- API key: remove ----------

@router.callback_query(F.data == "suno:remove_key")
async def cb_suno_remove_key(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🗑 <b>Удалить API-ключ?</b>\n\n"
            "После удаления тестовая генерация и расчёт кредитов перестанут "
            "работать, пока не задашь ключ снова.",
            reply_markup=suno_remove_key_confirm_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "suno:remove_key_yes")
async def cb_suno_remove_key_yes(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    removed = await clear_api_key(session)
    await callback.answer("🗑 Удалён" if removed else "Уже удалён")
    if isinstance(callback.message, Message):
        text, kb = await _build_menu(session)
        await callback.message.edit_text(text, reply_markup=kb)


# ---------- credits ----------

@router.callback_query(F.data == "suno:credits")
async def cb_suno_credits(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    api_key = await get_api_key(session)
    if not api_key:
        await callback.answer("Сначала задай API-ключ", show_alert=True)
        return

    client = SunoApiOrgClient(api_key)
    try:
        credits = await client.get_credits()
    except SunoApiError as exc:
        await callback.answer(
            f"⚠️ Suno: {exc.humanized()}", show_alert=True
        )
        return

    await callback.answer(f"💰 Кредитов: {credits}", show_alert=True)


@router.message(Command("suno_credits"), F.chat.type == ChatType.PRIVATE)
async def cmd_suno_credits(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    api_key = await get_api_key(session)
    if not api_key:
        await message.answer(
            "🔴 API-ключ не задан. Открой /suno и задай ключ."
        )
        return
    client = SunoApiOrgClient(api_key)
    try:
        credits = await client.get_credits()
    except SunoApiError as exc:
        await message.answer(
            "⚠️ Suno API: " + html.escape(exc.humanized())
        )
        return
    await message.answer(f"💰 Остаток кредитов: <b>{credits}</b>")


# ---------- model picker ----------

@router.callback_query(F.data == "suno:model_open")
async def cb_suno_model_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    current = await get_model(session)
    if isinstance(callback.message, Message):
        lines = [
            "🎚 <b>Модель Suno по умолчанию</b>\n",
            f"Сейчас: <code>{html.escape(current)}</code>\n",
            "Выбери модель ниже. Она будет использоваться для всех "
            "запросов из этого бота — и для тестовой генерации, и для "
            "будущей фичи «Песня дня».",
        ]
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=suno_model_keyboard(current)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("suno:model_set:"))
async def cb_suno_model_set(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    slug = parts[2]
    if slug not in SUPPORTED_MODELS:
        await callback.answer("⚠️ Неизвестная модель", show_alert=True)
        return
    await set_model(session, slug)
    await callback.answer(f"✅ {MODEL_LABELS.get(slug, slug)}")
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=kb)


# ---------- duration picker ----------

@router.callback_query(F.data == "suno:duration_open")
async def cb_suno_duration_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    current = await get_target_duration_sec(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🎯 <b>Целевая длительность песни</b>\n\n"
            f"Сейчас: <code>{format_duration_label(current)}</code> "
            f"(~{current} сек)\n\n"
            "Suno не принимает параметр «длительность» напрямую — "
            "вместо этого бот добавляет к prompt подсказку "
            "<code>[Length: ~M:SS]</code> и просит модель уложиться в "
            "1 куплет + припев + 1 куплет. Эффект статистический: "
            "модели V4_5+ / V4_5all чаще попадают в цель.\n\n"
            "Выбери пресет или задай своё значение:",
            reply_markup=suno_duration_keyboard(current),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("suno:duration_set:"))
async def cb_suno_duration_set(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        seconds = int(parts[2])
    except ValueError:
        await callback.answer("⚠️ Неверное значение", show_alert=True)
        return
    stored = await set_target_duration_sec(session, seconds)
    await callback.answer(f"✅ {format_duration_label(stored)}")
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "suno:duration_custom")
async def cb_suno_duration_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(SunoDurationCustom.waiting_seconds)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✏️ <b>Своё значение длительности</b>\n\n"
            f"Пришли число секунд от {MIN_TARGET_DURATION_SEC} до "
            f"{MAX_TARGET_DURATION_SEC}.\n\n"
            "Примеры:\n"
            "• <code>120</code> — 2:00\n"
            "• <code>180</code> — 3:00\n"
            "• <code>210</code> — 3:30\n\n"
            "Или /cancel.",
            reply_markup=suno_back_keyboard(),
        )
    await callback.answer()


@router.message(
    SunoDurationCustom.waiting_seconds,
    F.chat.type == ChatType.PRIVATE,
    F.text,
)
async def receive_duration_custom(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    try:
        seconds = int(raw)
    except ValueError:
        await message.answer(
            "⚠️ Не число. Пришли целое число секунд или /cancel."
        )
        return
    if not (MIN_TARGET_DURATION_SEC <= seconds <= MAX_TARGET_DURATION_SEC):
        await message.answer(
            f"⚠️ Вне диапазона [{MIN_TARGET_DURATION_SEC}, "
            f"{MAX_TARGET_DURATION_SEC}]. Или /cancel."
        )
        return
    stored = await set_target_duration_sec(session, seconds)
    await state.clear()
    text, kb = await _build_menu(session)
    await message.answer(
        f"✅ Длительность сохранена: "
        f"<code>{format_duration_label(stored)}</code>\n\n" + text,
        reply_markup=kb,
    )


# ---------- test generation ----------

@router.callback_query(F.data == "suno:gen_open")
async def cb_suno_gen_open(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    api_key = await get_api_key(session)
    if not api_key:
        await callback.answer("Сначала задай API-ключ", show_alert=True)
        return

    await state.set_state(SunoTestPrompt.waiting_prompt)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🧪 <b>Тестовая генерация</b>\n\n"
            f"Пришли короткое описание того, что должно играть "
            f"(до {PROMPT_MAX_LEN} символов). Лирика будет сгенерирована "
            "автоматически на основе описания.\n\n"
            "Примеры:\n"
            "• <code>A short relaxing piano tune</code>\n"
            "• <code>уютная гитара, дождь за окном, вечер</code>\n"
            "• <code>energetic synthwave with a driving beat</code>\n\n"
            "Я отправлю задачу в Suno и пришлю ссылку на mp3, как только "
            "она будет готова (обычно 2–3 минуты).\n\n"
            "Или /cancel.",
            reply_markup=suno_back_keyboard(),
        )
    await callback.answer()


@router.message(
    SunoTestPrompt.waiting_prompt, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_test_prompt(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    if len(text) < 5:
        await message.answer(
            "Слишком коротко. Хотя бы пара слов нужна. Или /cancel."
        )
        return
    if len(text) > PROMPT_MAX_LEN:
        await message.answer(
            f"⚠️ Слишком длинно — лимит {PROMPT_MAX_LEN} символов. Обрежь и пришли ещё раз."
        )
        return

    api_key = await get_api_key(session)
    if not api_key:
        await state.clear()
        await message.answer(
            "🔴 API-ключ пропал. Открой /suno и задай его снова."
        )
        return

    model = await get_model(session)
    callback_url = await get_callback_url(session)
    target_duration_sec = await get_target_duration_sec(session)

    # Append a length-hint to the user's prompt so Suno aims at the
    # configured target duration instead of the model's natural ceiling
    # (which on V4 is 4 minutes and on V4_5+/all is 8). Idempotent — a
    # prompt that already contains "[Length:" is not re-tagged.
    final_prompt = append_duration_hint(text, target_duration_sec)

    client = SunoApiOrgClient(api_key)
    try:
        task_id = await client.generate_music(
            prompt=final_prompt,
            model=model,
            callback_url=callback_url,
            custom_mode=False,
            instrumental=False,
        )
    except SunoApiError as exc:
        await state.clear()
        await message.answer(
            "❌ Suno отклонил задачу: "
            + html.escape(exc.humanized())
        )
        return

    await state.clear()

    sent = await message.answer(
        "🎵 <b>Задача отправлена</b>\n\n"
        f"🆔 <code>{html.escape(task_id)}</code>\n"
        f"🎚 Модель: <code>{html.escape(model)}</code>\n"
        f"🎯 Цель: <code>{format_duration_label(target_duration_sec)}</code>\n"
        "⏳ Жду готовности (обычно 2–3 минуты)…\n\n"
        "Можно в любой момент проверить вручную:\n"
        f"<code>/suno_status {html.escape(task_id)}</code>"
    )

    asyncio.create_task(
        _watch_task(
            bot=bot,
            api_key=api_key,
            task_id=task_id,
            notify_chat_id=sent.chat.id,
            notify_message_id=sent.message_id,
            requested_by=message.from_user.id if message.from_user else None,
            model=model,
            prompt=final_prompt,
        ),
        name=f"suno-watch:{task_id}",
    )


async def _watch_task(
    *,
    bot: Bot,
    api_key: str,
    task_id: str,
    notify_chat_id: int,
    notify_message_id: int,
    requested_by: int | None,
    model: str,
    prompt: str,
) -> None:
    """Background poller: polls Suno every TEST_GEN_POLL_INTERVAL_SEC,
    edits the placeholder and (on success) persists a ``Song`` row +
    delivers the mp3 to the user.

    Persisting happens in a fresh ``SessionLocal()`` because this runs
    outside the request lifecycle — middleware-injected sessions only
    live for the duration of the originating handler.
    """
    client = SunoApiOrgClient(api_key)
    deadline = TEST_GEN_TIMEOUT_SEC
    elapsed = 0
    last_snapshot: TaskSnapshot | None = None

    while elapsed < deadline:
        await asyncio.sleep(TEST_GEN_POLL_INTERVAL_SEC)
        elapsed += TEST_GEN_POLL_INTERVAL_SEC

        try:
            snapshot = await client.get_task(task_id)
        except SunoApiError as exc:
            log.warning("suno watch %s poll error: %s", task_id, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("suno watch %s unexpected: %s", task_id, exc)
            continue

        last_snapshot = snapshot

        if snapshot.is_terminal:
            await _handle_terminal(
                bot,
                notify_chat_id,
                notify_message_id,
                task_id,
                snapshot,
                requested_by=requested_by,
                model=model,
                prompt=prompt,
            )
            return

    # Timed out.
    msg = (
        f"⏰ <b>Тайм-аут.</b> Задача всё ещё не готова за "
        f"{TEST_GEN_TIMEOUT_SEC // 60} минут.\n\n"
        f"🆔 <code>{html.escape(task_id)}</code>\n"
        f"📊 Последний статус: <code>"
        f"{html.escape(last_snapshot.status if last_snapshot else 'неизвестно')}"
        f"</code>\n\n"
        f"Можно подождать и проверить руками:\n"
        f"<code>/suno_status {html.escape(task_id)}</code>"
    )
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            msg, chat_id=notify_chat_id, message_id=notify_message_id
        )


async def _handle_terminal(
    bot: Bot,
    chat_id: int,
    message_id: int,
    task_id: str,
    snapshot: TaskSnapshot,
    *,
    requested_by: int | None,
    model: str,
    prompt: str,
) -> None:
    """Edit the placeholder, persist a ``Song`` row on success, and
    deliver the mp3. On first ``send_audio`` we capture Telegram's
    permanent ``file_id`` so the song stays playable from /musiclist
    long after Suno's 15-day mp3 retention expires.
    """
    if snapshot.is_success:
        # 1. Persist Song row first (so /musiclist can find it even if
        # the audio delivery below fails for any reason).
        song_id: int | None = None
        try:
            async with SessionLocal() as session:
                song = await upsert_song(
                    session,
                    suno_task_id=task_id,
                    model=model,
                    prompt=prompt,
                    title=snapshot.title,
                    audio_url=snapshot.audio_url,
                    stream_url=snapshot.stream_url,
                    image_url=snapshot.image_url,
                    duration=snapshot.duration,
                    requested_by=requested_by,
                    status="success",
                )
                song_id = song.id
        except Exception:  # noqa: BLE001
            log.exception("persist song for task %s failed", task_id)

        # 2. Edit the placeholder card.
        title = snapshot.title or "(без названия)"
        duration = (
            f" · {snapshot.duration:.0f} сек"
            if snapshot.duration is not None
            else ""
        )
        edited = (
            "✅ <b>Готово!</b>\n\n"
            f"🎵 <b>{html.escape(title)}</b>{duration}\n"
            f"🆔 <code>{html.escape(task_id)}</code>\n\n"
            "ℹ️ Файл хранится на серверах Suno <b>15 дней</b>.\n"
            "После первого проигрывания через бота он навсегда "
            "сохраняется в Telegram — найти его можно через /musiclist."
        )
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                edited, chat_id=chat_id, message_id=message_id
            )

        # 3. Deliver the mp3 + capture Telegram's file_id on success.
        if snapshot.audio_url:
            try:
                sent_audio = await bot.send_audio(
                    chat_id,
                    audio=snapshot.audio_url,
                    title=title,
                    performer="Suno",
                    caption=f"🎵 {html.escape(title)}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("send_audio for task %s failed: %s", task_id, exc)
                with contextlib.suppress(Exception):
                    await bot.send_message(
                        chat_id,
                        f"🔗 <a href=\"{html.escape(snapshot.audio_url)}\">"
                        "Скачать mp3</a>",
                        disable_web_page_preview=False,
                    )
                return

            # Persist Telegram's permanent file_id so /musiclist can
            # re-deliver this track via Telegram even after Suno's
            # 15-day URL retention expires.
            if (
                song_id is not None
                and sent_audio
                and sent_audio.audio
            ):
                try:
                    async with SessionLocal() as session:
                        await set_tg_file_id(
                            session, song_id, sent_audio.audio.file_id
                        )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "capture tg_audio_file_id for song %s failed",
                        song_id,
                    )
        return

    # Terminal but not success — show errorMessage from API if present.
    reason = snapshot.error_message or snapshot.status
    edited = (
        "❌ <b>Suno вернул ошибку.</b>\n\n"
        f"🆔 <code>{html.escape(task_id)}</code>\n"
        f"📊 Статус: <code>{html.escape(snapshot.status)}</code>\n"
        f"💬 Причина: {html.escape(reason)}"
    )
    with contextlib.suppress(Exception):
        await bot.edit_message_text(
            edited, chat_id=chat_id, message_id=message_id
        )


@router.message(Command("suno_status"), F.chat.type == ChatType.PRIVATE)
async def cmd_suno_status(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    """`/suno_status <task_id>` — one-shot check on a Suno task."""
    if not await _require_admin(message, session):
        return
    task_id = (command.args or "").strip()
    if not task_id:
        await message.answer(
            "Использование: <code>/suno_status &lt;task_id&gt;</code>"
        )
        return

    api_key = await get_api_key(session)
    if not api_key:
        await message.answer(
            "🔴 API-ключ не задан. Открой /suno и задай его."
        )
        return

    client = SunoApiOrgClient(api_key)
    try:
        snapshot = await client.get_task(task_id)
    except SunoApiError as exc:
        await message.answer(
            "❌ Suno: " + html.escape(exc.humanized())
        )
        return

    text_lines = [
        "🎵 <b>Suno · статус задачи</b>\n",
        f"🆔 <code>{html.escape(task_id)}</code>",
        f"📊 Статус: <code>{html.escape(snapshot.status)}</code>",
    ]
    if snapshot.error_message:
        text_lines.append(
            f"💬 Причина: {html.escape(snapshot.error_message)}"
        )
    if snapshot.title:
        text_lines.append(f"🎼 Название: <b>{html.escape(snapshot.title)}</b>")
    if snapshot.duration is not None:
        text_lines.append(f"⏱ Длительность: {snapshot.duration:.0f} сек")
    if snapshot.audio_url:
        text_lines.append(
            f"🔗 <a href=\"{html.escape(snapshot.audio_url)}\">mp3</a>"
        )
    elif snapshot.stream_url:
        text_lines.append(
            f"🎧 <a href=\"{html.escape(snapshot.stream_url)}\">"
            "поток (готов раньше mp3)</a>"
        )

    await message.answer("\n".join(text_lines), disable_web_page_preview=False)

    if snapshot.is_success and snapshot.audio_url:
        with contextlib.suppress(Exception):
            await message.bot.send_audio(  # type: ignore[union-attr]
                message.chat.id,
                audio=snapshot.audio_url,
                title=snapshot.title or "Suno",
                performer="Suno",
            )


__all__ = ["router"]
