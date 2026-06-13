"""OpenRouter LLM client + DB-backed configuration.

Used by the daily-song pipeline to (eventually) summarize a chat's
day and produce a ``SongDraft`` (title + style + lyrics). For the
current PR scope only the client and DB helpers exist — the
summarizer/songwriter callers land in a follow-up phase.

API docs: https://openrouter.ai/docs

Settings keys (all stored in the existing ``settings`` table)
------------------------------------------------------------

- ``llm.api_key``           Bearer token from https://openrouter.ai/keys
- ``llm.model``             Model slug, e.g.
                            ``google/gemini-2.0-flash-exp:free`` (default)
- ``llm.system_prompt``     Optional system prompt override. When unset
                            the songwriter picks a sensible default.
- ``llm.referer``           Optional ``HTTP-Referer`` header for
                            OpenRouter's analytics. Defaults to the
                            repo URL — set to your own domain to
                            de-anonymise usage in the OpenRouter UI.

Why OpenRouter
--------------

- OpenAI-compatible chat-completions API → same shape no matter which
  underlying model is selected.
- Free tier covers the daily-song use case for low-volume bots
  (Gemini 2.0 Flash exp:free, Llama 3.3 70B:free, etc).
- Single key, many models — admins switch models from inside Telegram
  without redeploying.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings import (
    delete_setting,
    get_setting,
    set_setting,
)

log = logging.getLogger(__name__)


BASE_URL = "https://openrouter.ai/api/v1"

# Default referrer reported to OpenRouter analytics. Public attribution
# is fine — the project repo is open-source. Override with
# ``llm.referer`` in DB if you want your own domain attributed.
DEFAULT_REFERER = "https://github.com/pavlodrab/ideabottg"
DEFAULT_X_TITLE = "ideabottg daily-song"

# Default model. Free tier on OpenRouter — no balance required.
# Owners can switch to a paid model at any time from /musicmenu.
DEFAULT_MODEL = "google/gemini-2.0-flash-exp:free"

# Curated catalogue surfaced in the /musicmenu model picker.
# Free models live at the top so the bot is usable without funding the
# OpenRouter wallet first. ``label`` is what appears on the button;
# ``slug`` is what gets sent to the API.
SUPPORTED_MODELS: list[tuple[str, str]] = [
    ("google/gemini-2.0-flash-exp:free",      "Gemini 2.0 Flash · free"),
    ("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B · free"),
    ("google/gemini-flash-1.5-8b",             "Gemini 1.5 Flash 8B · cheap"),
    ("anthropic/claude-3-haiku",               "Claude 3 Haiku · cheap"),
    ("openai/gpt-4o-mini",                     "GPT-4o mini · cheap"),
    ("anthropic/claude-3-5-sonnet",            "Claude 3.5 Sonnet · pro"),
    ("openai/gpt-4o",                          "GPT-4o · pro"),
]
SUPPORTED_MODEL_SLUGS: set[str] = {slug for slug, _ in SUPPORTED_MODELS}
MODEL_LABEL_BY_SLUG: dict[str, str] = {slug: label for slug, label in SUPPORTED_MODELS}


# Settings keys.
KEY_API_KEY = "llm.api_key"
KEY_MODEL = "llm.model"
KEY_SYSTEM_PROMPT = "llm.system_prompt"
KEY_REFERER = "llm.referer"


# Default system prompt for the songwriter step. Stays in this module
# so we have a single source of truth and the admin UI can show it as a
# placeholder when the per-deployment override is unset.
DEFAULT_SONGWRITER_SYSTEM_PROMPT = (
    "Ты — songwriter, который превращает дневной чат в короткую песню "
    "(2-3 минуты).\n\n"
    "На входе — выжимка обсуждений за день: темы, шутки, активные "
    "участники, общее настроение. На выходе верни СТРОГО JSON без "
    "комментариев и префиксов, ровно следующего вида:\n"
    "{\n"
    '  "title":  "<до 60 символов, 2-4 слова>",\n'
    '  "style":  "<жанр + 3-5 ключевых слов настроения, английский, '
    'до 200 символов>",\n'
    '  "lyrics": "<[Verse]\\n…\\n[Chorus]\\n…\\n[Verse]\\n…, '
    'до 600 символов, НА ЯЗЫКЕ ВХОДНОГО ТЕКСТА>",\n'
    '  "summary": "<1 предложение для лога — почему именно такая песня>"\n'
    "}\n\n"
    "Правила:\n"
    "- ЯЗЫК: пиши lyrics на том же языке, на котором написан входной "
    "чат/текст. Если вход на русском — лирика на русском, на английский "
    "НЕ переводи. Поле style — наоборот, всегда на английском (Suno так "
    "лучше понимает жанр).\n"
    "- Структура лирики ровно: 1 куплет + припев + 1 куплет. "
    "Без bridge, без длинного outro — иначе песня будет дольше 3 минут.\n"
    "- Не цитируй сообщения дословно. Это художественный пересказ.\n"
    "- Имена/ники используй только если органично; никаких user_id, "
    "телефонов, e-mail.\n"
    "- Стиль (style) выбирай так, чтобы он соответствовал тону выжимки. "
    "Если день был грустный — indie folk / lo-fi; угарный — pop punk / "
    "synthwave; философский — ambient / neo-classical; и так далее."
)


# ---------- DB-backed config helpers ----------

async def get_api_key(session: AsyncSession) -> str | None:
    return await get_setting(session, KEY_API_KEY)


async def set_api_key(session: AsyncSession, key: str) -> None:
    await set_setting(session, KEY_API_KEY, key.strip())


async def clear_api_key(session: AsyncSession) -> bool:
    return await delete_setting(session, KEY_API_KEY)


async def get_model(session: AsyncSession) -> str:
    value = await get_setting(session, KEY_MODEL)
    if value:
        return value
    return DEFAULT_MODEL


async def set_model(session: AsyncSession, model: str) -> str:
    """Persist the selected model. Returns what was stored.

    We deliberately do NOT reject unknown slugs — OpenRouter's catalogue
    grows weekly, and curated lists go stale. The admin UI surfaces the
    curated list as quick picks but a free-text custom slug is also
    valid; the next chat-completion call will surface whatever error
    OpenRouter returns for an unknown model.
    """
    cleaned = model.strip()
    await set_setting(session, KEY_MODEL, cleaned)
    return cleaned


async def get_system_prompt(session: AsyncSession) -> str | None:
    """Returns the configured override or ``None`` if the default
    (:data:`DEFAULT_SONGWRITER_SYSTEM_PROMPT`) should be used."""
    value = await get_setting(session, KEY_SYSTEM_PROMPT)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


async def set_system_prompt(
    session: AsyncSession, prompt: str | None
) -> None:
    """Save a custom system prompt. Pass ``None`` to clear and fall
    back to the in-code default."""
    if prompt is None:
        await delete_setting(session, KEY_SYSTEM_PROMPT)
        return
    await set_setting(session, KEY_SYSTEM_PROMPT, prompt.strip())


async def get_referer(session: AsyncSession) -> str:
    value = await get_setting(session, KEY_REFERER)
    return value or DEFAULT_REFERER


def mask_key(key: str | None) -> str:
    """Safe-to-show / safe-to-log version of an API key.

    Mirrors :func:`app.services.suno.mask_key` so log lines look the
    same whether they came from the Suno or LLM client.
    """
    if not key:
        return "(не задан)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


# ---------- HTTP client ----------


class LlmApiError(RuntimeError):
    """Wraps any non-200 response from OpenRouter or transport errors.

    Mirrors :class:`app.services.suno.SunoApiError` so handlers can
    treat both the same way (``.humanized()`` for an inline error
    message).
    """

    def __init__(self, code: int, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg

    def humanized(self) -> str:
        hints: dict[int, str] = {
            0:   f"сеть/таймаут: {self.msg}",
            401: "ключ не принят (неверный или сброшен)",
            402: "недостаточно средств на OpenRouter",
            403: "доступ запрещён (модель требует другого тарифа)",
            404: "модель не найдена — проверь slug",
            408: "таймаут запроса",
            413: "слишком длинный prompt — обрежь и попробуй ещё раз",
            429: "rate limit OpenRouter — подожди минуту",
            500: "ошибка на стороне OpenRouter",
            502: "OpenRouter не смог достучаться до апстрима",
            503: "OpenRouter временно недоступен",
        }
        hint = hints.get(self.code)
        if hint:
            return f"{hint} (код {self.code}: {self.msg})"
        return f"код {self.code}: {self.msg}"


@dataclass
class LlmKeyInfo:
    """Subset of OpenRouter's ``/auth/key`` payload we surface in UI."""

    label: str | None = None
    usage: float | None = None
    limit: float | None = None
    limit_remaining: float | None = None
    is_free_tier: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    """Cleaned-up chat-completion response."""

    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    raw: dict[str, Any]

    def parse_json(self) -> Any:
        """Try to parse ``text`` as JSON. Returns ``None`` on failure
        instead of raising — the songwriter retry loop relies on this."""
        try:
            return json.loads(self.text)
        except (TypeError, ValueError):
            return None


class OpenRouterClient:
    """Thin async client for OpenRouter chat-completions.

    One instance per request is fine; nothing is cached between calls.
    The ``referer`` argument sets ``HTTP-Referer`` for OpenRouter's
    analytics — pass the value from :func:`get_referer` so admins can
    customise it without redeploying.
    """

    def __init__(
        self,
        api_key: str,
        *,
        referer: str = DEFAULT_REFERER,
        x_title: str = DEFAULT_X_TITLE,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter API key is empty")
        self._api_key = api_key
        self._referer = referer or DEFAULT_REFERER
        self._x_title = x_title
        self._timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": self._referer,
            "X-Title": self._x_title,
        }

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method, url, headers=self.headers, **kwargs
                )
        except httpx.HTTPError as exc:
            log.warning(
                "openrouter %s %s network error (key=%s): %s",
                method,
                path,
                mask_key(self._api_key),
                exc,
            )
            raise LlmApiError(0, f"сеть: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            raise LlmApiError(
                resp.status_code,
                (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}",
            )

        if not isinstance(data, dict):
            raise LlmApiError(
                resp.status_code, f"unexpected payload: {data!r}"[:200]
            )

        if resp.status_code != 200:
            # OpenRouter returns ``{"error": {"message": "...", "code": N}}``
            # on errors. Pull whatever's there for the human-readable msg.
            err = data.get("error") or {}
            msg = (
                err.get("message")
                or data.get("message")
                or f"HTTP {resp.status_code}"
            )
            raise LlmApiError(resp.status_code, str(msg))
        return data

    # ----- public API -----

    async def get_key_info(self) -> LlmKeyInfo:
        """Hit ``/auth/key`` and return what we know about this key.

        Used at API-key-set time to (a) validate the key works, (b)
        show the admin their current balance/limit. Cheap and fast — no
        token cost.
        """
        data = await self._request("GET", "/auth/key")
        body = data.get("data") or {}
        return LlmKeyInfo(
            label=body.get("label"),
            usage=_to_float(body.get("usage")),
            limit=_to_float(body.get("limit")),
            limit_remaining=_to_float(body.get("limit_remaining")),
            is_free_tier=bool(body.get("is_free_tier"))
            if "is_free_tier" in body
            else None,
            raw=body,
        )

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Standard OpenAI-compatible chat-completion.

        ``response_format`` accepts OpenAI's ``{"type": "json_object"}``
        which most OpenRouter-routed models honour. The bot still
        validates the JSON in user code (see :meth:`ChatResult.parse_json`)
        because not every routed model is strict about the contract.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format

        data = await self._request("POST", "/chat/completions", json=body)
        choices = data.get("choices") or []
        if not choices:
            raise LlmApiError(
                0, f"в ответе нет choices: {str(data)[:200]}"
            )

        first = choices[0]
        message = first.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            # Some routed models can return a list of "parts" — flatten.
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                content = str(content or "")

        usage = data.get("usage") or {}
        return ChatResult(
            text=content,
            model=str(data.get("model") or model),
            prompt_tokens=_to_int(usage.get("prompt_tokens")),
            completion_tokens=_to_int(usage.get("completion_tokens")),
            raw=data,
        )


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------- summary helpers (read-only, used by /musicmenu) ----------

@dataclass
class LlmSettingsSnapshot:
    """One-shot read of every LLM-related setting for menu rendering.

    Doing this in a single helper means handlers don't sprinkle four
    ``await get_*`` calls inline and risk forgetting one when the menu
    grows.
    """

    api_key: str | None
    model: str
    system_prompt: str | None  # None = use DEFAULT_SONGWRITER_SYSTEM_PROMPT
    referer: str

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def effective_system_prompt(self) -> str:
        return self.system_prompt or DEFAULT_SONGWRITER_SYSTEM_PROMPT


async def load_settings(session: AsyncSession) -> LlmSettingsSnapshot:
    return LlmSettingsSnapshot(
        api_key=await get_api_key(session),
        model=await get_model(session),
        system_prompt=await get_system_prompt(session),
        referer=await get_referer(session),
    )
