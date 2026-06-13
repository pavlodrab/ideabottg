"""OpenRouter admin UI: API-key, model, system prompt, test request.

All settings are stored in the existing ``settings`` table — same
pattern as ``app/handlers/suno_admin.py``. No env vars, no redeploy.

Callback-data namespace: ``llm:*``. Entry points:

- ``/llm`` (DM, admin) — quick access to the menu.
- Button "🤖 OpenRouter" in ``/musicmenu``.

The only side-effect outside the ``settings`` table is hitting
OpenRouter's ``/auth/key`` endpoint to validate a freshly entered API
key (so we don't store unusable secrets), and hitting
``/chat/completions`` for the test-prompt round-trip.
"""
from __future__ import annotations

import contextlib
import html
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.llm import (
    llm_back_keyboard,
    llm_menu_keyboard,
    llm_model_keyboard,
    llm_prompt_keyboard,
    llm_remove_key_confirm_keyboard,
)
from app.services.admins import is_admin
from app.services.llm import (
    DEFAULT_SONGWRITER_SYSTEM_PROMPT,
    MODEL_LABEL_BY_SLUG,
    LlmApiError,
    OpenRouterClient,
    clear_api_key,
    get_api_key,
    get_model,
    get_referer,
    get_system_prompt,
    mask_key,
    set_api_key,
    set_model,
    set_system_prompt,
)
from app.states import (
    LlmApiKeyEditing,
    LlmModelEditing,
    LlmSystemPromptEditing,
    LlmTestPrompt,
)

log = logging.getLogger(__name__)

router = Router(name="llm_admin")

# Hard caps so a fat-fingered paste can't blow up a DB row or a
# follow-up API call.
MAX_PROMPT_LEN = 4000          # system prompt
MAX_TEST_PROMPT_LEN = 1000     # /chat test request
MAX_MODEL_SLUG_LEN = 128       # OpenRouter slugs are 30-80 chars in practice


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


# ---------- entry: /llm + llm:home ----------

@router.message(Command("llm"), F.chat.type == ChatType.PRIVATE)
async def cmd_llm(message: Message, session: AsyncSession) -> None:
    if not await _require_admin(message, session):
        return
    text, kb = await _build_menu(session)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "llm:home")
async def cb_llm_home(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.clear()
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )
    await callback.answer()


async def _build_menu(session: AsyncSession) -> tuple[str, object]:
    api_key = await get_api_key(session)
    model = await get_model(session)
    system_prompt = await get_system_prompt(session)

    masked = mask_key(api_key)
    if api_key:
        status_line = (
            f"🟢 <b>API-ключ задан</b> · <code>{html.escape(masked)}</code>"
        )
        hint = (
            "Открой «🧪 Тестовый запрос», чтобы убедиться, что выбранная "
            "модель отвечает. Для песни-дня этот ключ + модель будут "
            "превращать дневной чат в дайджест и SongDraft."
        )
    else:
        status_line = "🔴 <b>API-ключ не задан</b>"
        hint = (
            "Возьми ключ на <a href=\"https://openrouter.ai/keys\">"
            "openrouter.ai/keys</a> и нажми «🔑 Задать API-ключ».\n"
            "Дефолтная модель — <code>google/gemini-2.0-flash-exp:free</code> "
            "(бесплатная, без баланса)."
        )

    model_label = MODEL_LABEL_BY_SLUG.get(model, model)
    prompt_status = (
        "<b>System prompt:</b> кастомный"
        if system_prompt
        else "<b>System prompt:</b> по умолчанию"
    )

    text = (
        "🤖 <b>OpenRouter</b>  · <code>openrouter.ai</code>\n\n"
        f"{status_line}\n"
        f"🧠 Модель: <code>{html.escape(model)}</code>\n"
        f"   <i>{html.escape(model_label)}</i>\n"
        f"{prompt_status}\n\n"
        f"{hint}"
    )
    kb = llm_menu_keyboard(has_api_key=bool(api_key), current_model=model)
    return text, kb


# ---------- API key: set ----------

@router.callback_query(F.data == "llm:set_key")
async def cb_llm_set_key(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(LlmApiKeyEditing.waiting_key)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🔑 <b>API-ключ OpenRouter</b>\n\n"
            "Пришли ключ одним сообщением. Получить его можно тут:\n"
            "<a href=\"https://openrouter.ai/keys\">openrouter.ai/keys</a>\n\n"
            "Я сразу проверю его звонком к /auth/key и удалю это "
            "сообщение из истории, чтобы ключ не светился.\n\n"
            "Или /cancel.",
            reply_markup=llm_back_keyboard(),
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.message(
    LlmApiKeyEditing.waiting_key, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_api_key(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return  # let /cancel and other commands fall through

    # Try to delete the user's message so the key doesn't linger.
    with contextlib.suppress(Exception):
        await message.delete()

    if len(raw) < 16:
        await message.answer(
            "⚠️ Слишком короткий ключ. Перепроверь и пришли ещё раз, или /cancel.",
            reply_markup=llm_back_keyboard(),
        )
        return

    referer = await get_referer(session)
    client = OpenRouterClient(raw, referer=referer)
    try:
        info = await client.get_key_info()
    except LlmApiError as exc:
        await message.answer(
            "❌ <b>Ключ не принят OpenRouter.</b>\n\n"
            f"Причина: {html.escape(exc.humanized())}\n\n"
            "Перепроверь ключ на "
            "<a href=\"https://openrouter.ai/keys\">openrouter.ai/keys</a> "
            "и попробуй ещё раз. Или /cancel.",
            reply_markup=llm_back_keyboard(),
            disable_web_page_preview=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("unexpected error validating openrouter key")
        await message.answer(
            f"❌ Не получилось проверить ключ: <code>{html.escape(str(exc))}</code>\n\n"
            "Попробуй ещё раз или /cancel.",
            reply_markup=llm_back_keyboard(),
        )
        return

    await set_api_key(session, raw)
    await state.clear()

    summary = "✅ <b>API-ключ сохранён.</b>\n"
    if info.label:
        summary += f"🏷 Метка ключа: <code>{html.escape(info.label)}</code>\n"
    if info.usage is not None:
        summary += f"📊 Usage: <b>${info.usage:.4f}</b>\n"
    if info.limit is not None:
        summary += f"💸 Лимит: <b>${info.limit:.2f}</b>\n"
    elif info.is_free_tier:
        summary += "💸 Лимит: free tier (rate-limited)\n"
    summary += "\n"

    text, kb = await _build_menu(session)
    await message.answer(
        summary + text,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


# ---------- API key: remove ----------

@router.callback_query(F.data == "llm:remove_key")
async def cb_llm_remove_key(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🗑 <b>Удалить API-ключ?</b>\n\n"
            "После удаления генерация песни-дня и тестовые запросы "
            "перестанут работать, пока не задашь ключ снова.",
            reply_markup=llm_remove_key_confirm_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "llm:remove_key_yes")
async def cb_llm_remove_key_yes(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    removed = await clear_api_key(session)
    await callback.answer("🗑 Удалён" if removed else "Уже удалён")
    if isinstance(callback.message, Message):
        text, kb = await _build_menu(session)
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )


# ---------- credits / key info ----------

@router.callback_query(F.data == "llm:credits")
async def cb_llm_credits(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    api_key = await get_api_key(session)
    if not api_key:
        await callback.answer("Сначала задай API-ключ", show_alert=True)
        return

    referer = await get_referer(session)
    client = OpenRouterClient(api_key, referer=referer)
    try:
        info = await client.get_key_info()
    except LlmApiError as exc:
        await callback.answer(
            f"⚠️ OpenRouter: {exc.humanized()}", show_alert=True
        )
        return

    parts: list[str] = []
    if info.label:
        parts.append(f"🏷 {info.label}")
    if info.usage is not None:
        parts.append(f"📊 usage ${info.usage:.4f}")
    if info.limit is not None:
        parts.append(f"💸 лимит ${info.limit:.2f}")
    elif info.is_free_tier:
        parts.append("💸 free tier")
    if info.limit_remaining is not None:
        parts.append(f"⏳ осталось ${info.limit_remaining:.4f}")
    summary = "\n".join(parts) or "Нет данных"
    await callback.answer(summary, show_alert=True)


# ---------- model picker ----------

@router.callback_query(F.data == "llm:model_open")
async def cb_llm_model_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    current = await get_model(session)
    if isinstance(callback.message, Message):
        label = MODEL_LABEL_BY_SLUG.get(current, current)
        await callback.message.edit_text(
            "🧠 <b>Модель OpenRouter</b>\n\n"
            f"Сейчас: <code>{html.escape(current)}</code>\n"
            f"<i>{html.escape(label)}</i>\n\n"
            "Выбери из пресетов или задай свой slug.\n"
            "Каталог: <a href=\"https://openrouter.ai/models\">"
            "openrouter.ai/models</a>",
            reply_markup=llm_model_keyboard(current),
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("llm:model_set:"))
async def cb_llm_model_set(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    slug = (callback.data or "").split(":", 2)[2] if callback.data else ""
    if not slug:
        await callback.answer()
        return
    stored = await set_model(session, slug)
    label = MODEL_LABEL_BY_SLUG.get(stored, stored)
    await callback.answer(f"✅ {label}")
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )


@router.callback_query(F.data == "llm:model_custom")
async def cb_llm_model_custom(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(LlmModelEditing.waiting_model)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✏️ <b>Свой slug модели</b>\n\n"
            "Пришли точный slug из каталога OpenRouter:\n"
            "<a href=\"https://openrouter.ai/models\">"
            "openrouter.ai/models</a>\n\n"
            "Примеры:\n"
            "• <code>meta-llama/llama-3.3-70b-instruct:free</code>\n"
            "• <code>anthropic/claude-3-5-sonnet</code>\n"
            "• <code>mistralai/mistral-large-2411</code>\n\n"
            "Я не проверяю slug — увидишь ошибку «model not found» при "
            "первом тестовом запросе, если ошибся.\n\n"
            "Или /cancel.",
            reply_markup=llm_back_keyboard(),
            disable_web_page_preview=True,
        )
    await callback.answer()


@router.message(
    LlmModelEditing.waiting_model, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_model_slug(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    if "/" not in raw:
        await message.answer(
            "⚠️ Slug должен содержать <code>/</code> — например "
            "<code>provider/model</code>. Или /cancel."
        )
        return
    if len(raw) > MAX_MODEL_SLUG_LEN:
        await message.answer(
            f"⚠️ Слишком длинно — лимит {MAX_MODEL_SLUG_LEN} символов."
        )
        return

    stored = await set_model(session, raw)
    await state.clear()
    text, kb = await _build_menu(session)
    await message.answer(
        f"✅ Модель сохранена: <code>{html.escape(stored)}</code>\n\n" + text,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


# ---------- system prompt ----------

@router.callback_query(F.data == "llm:prompt_open")
async def cb_llm_prompt_open(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    override = await get_system_prompt(session)
    effective = override or DEFAULT_SONGWRITER_SYSTEM_PROMPT
    if isinstance(callback.message, Message):
        header = (
            "📝 <b>System prompt</b>\n\n"
            "Что бот говорит модели перед запросом про песню. "
            "По умолчанию — встроенный songwriter-промпт; можно "
            "переписать под свой вкус.\n\n"
            f"<i>{'Сейчас: КАСТОМНЫЙ' if override else 'Сейчас: ДЕФОЛТ'}</i>\n"
            "━━━━━━━━━━━━\n"
        )
        # Trim for the message body — Telegram caps text at 4096.
        body = effective
        if len(body) > 3000:
            body = body[:3000] + "\n\n…<i>(обрезано в превью)</i>"
        await callback.message.edit_text(
            header + html.escape(body) + "\n━━━━━━━━━━━━",
            reply_markup=llm_prompt_keyboard(has_override=bool(override)),
        )
    await callback.answer()


@router.callback_query(F.data == "llm:prompt_edit")
async def cb_llm_prompt_edit(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await state.set_state(LlmSystemPromptEditing.waiting_text)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✏️ <b>Новый system prompt</b>\n\n"
            f"Пришли новый текст одним сообщением (до {MAX_PROMPT_LEN} символов).\n\n"
            "Бот ожидает, что модель вернёт строгий JSON "
            "<code>{title, style, lyrics, summary}</code> — об этом "
            "твой prompt тоже должен сказать модели, иначе песня-дня "
            "будет ломаться на парсинге.\n\n"
            "Или /cancel.",
            reply_markup=llm_back_keyboard(),
        )
    await callback.answer()


@router.message(
    LlmSystemPromptEditing.waiting_text,
    F.chat.type == ChatType.PRIVATE,
    F.text,
)
async def receive_system_prompt(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    if len(raw) < 20:
        await message.answer(
            "⚠️ Слишком коротко (<20 символов). Это всё-таки songwriter-prompt. "
            "Или /cancel."
        )
        return
    if len(raw) > MAX_PROMPT_LEN:
        await message.answer(
            f"⚠️ Слишком длинно — лимит {MAX_PROMPT_LEN} символов."
        )
        return

    await set_system_prompt(session, raw)
    await state.clear()
    await message.answer("✅ System prompt обновлён.")
    text, kb = await _build_menu(session)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == "llm:prompt_reset")
async def cb_llm_prompt_reset(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    await set_system_prompt(session, None)
    await callback.answer("↩️ Сброшен к дефолту")
    text, kb = await _build_menu(session)
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_text(
                text, reply_markup=kb, disable_web_page_preview=True
            )


# ---------- test request ----------

@router.callback_query(F.data == "llm:test_open")
async def cb_llm_test_open(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    if not await _require_admin(callback, session):
        return
    api_key = await get_api_key(session)
    if not api_key:
        await callback.answer("Сначала задай API-ключ", show_alert=True)
        return
    await state.set_state(LlmTestPrompt.waiting_prompt)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "🧪 <b>Тестовый запрос</b>\n\n"
            f"Пришли user-сообщение (до {MAX_TEST_PROMPT_LEN} символов). "
            "Я отправлю его в выбранную модель с текущим system prompt и "
            "верну сырой ответ. Полезно проверить, что модель отвечает "
            "и формат JSON корректный.\n\n"
            "Пример: <code>Сделай SongDraft про субботнее утро</code>\n\n"
            "Или /cancel.",
            reply_markup=llm_back_keyboard(),
        )
    await callback.answer()


@router.message(
    LlmTestPrompt.waiting_prompt, F.chat.type == ChatType.PRIVATE, F.text
)
async def receive_test_prompt(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    raw = (message.text or "").strip()
    if raw.startswith("/"):
        return
    if len(raw) < 3:
        await message.answer("Слишком коротко. Или /cancel.")
        return
    if len(raw) > MAX_TEST_PROMPT_LEN:
        await message.answer(
            f"⚠️ Слишком длинно — лимит {MAX_TEST_PROMPT_LEN} символов."
        )
        return

    api_key = await get_api_key(session)
    if not api_key:
        await state.clear()
        await message.answer("🔴 API-ключ пропал. Открой /llm и задай его.")
        return

    model = await get_model(session)
    referer = await get_referer(session)
    system_prompt = (
        await get_system_prompt(session)
    ) or DEFAULT_SONGWRITER_SYSTEM_PROMPT

    placeholder = await message.answer(
        f"⏳ Отправляю в <code>{html.escape(model)}</code>…"
    )

    client = OpenRouterClient(api_key, referer=referer)
    try:
        result = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw},
            ],
            temperature=0.7,
            max_tokens=1000,
        )
    except LlmApiError as exc:
        await state.clear()
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                "❌ OpenRouter отклонил запрос: "
                + html.escape(exc.humanized())
            )
        return
    except Exception as exc:  # noqa: BLE001
        await state.clear()
        log.exception("openrouter test prompt failed")
        with contextlib.suppress(Exception):
            await placeholder.edit_text(
                f"❌ Неожиданная ошибка: <code>{html.escape(str(exc))}</code>"
            )
        return

    await state.clear()

    body = result.text
    if len(body) > 3500:
        body = body[:3500] + "\n\n…<i>(обрезано)</i>"
    tokens_line = ""
    if result.prompt_tokens is not None or result.completion_tokens is not None:
        tokens_line = (
            f"\n📊 prompt: <code>{result.prompt_tokens}</code> · "
            f"completion: <code>{result.completion_tokens}</code>"
        )

    parsed = result.parse_json()
    json_status = "✅ JSON parsed" if parsed is not None else "⚠️ не JSON"

    with contextlib.suppress(Exception):
        await placeholder.edit_text(
            f"🧪 <b>Ответ модели</b>\n"
            f"🧠 <code>{html.escape(result.model)}</code>{tokens_line}\n"
            f"🧬 {json_status}\n\n"
            f"<pre>{html.escape(body)}</pre>",
            reply_markup=llm_back_keyboard(),
        )


__all__ = ["router"]
