"""Client for the sunoapi.org REST API and DB-backed config helpers.

All Suno configuration (API key, default model, callback URL) lives in the
existing `settings` table and is edited through the bot's admin menu —
NOT via env vars. This way the owner can set everything up from inside
Telegram without redeploying.

API docs: https://docs.sunoapi.org/

Settings keys
-------------
- `suno.api_key`        Bearer token from https://sunoapi.org/api-key
- `suno.model`          Default model: V4 / V4_5 / V4_5PLUS / V4_5ALL / V5 / V5_5
- `suno.callback_url`   Required by the API but unused by us (we poll); a dummy
                        URL is fine. Only override if you actually run a webhook.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings import (
    delete_setting,
    get_setting,
    set_setting,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.sunoapi.org"
DEFAULT_MODEL = "V4_5"
DEFAULT_CALLBACK_URL = "https://example.com/suno-callback"

# Models exposed to the user in the picker UI. Order = display order.
SUPPORTED_MODELS: list[str] = [
    "V5_5",
    "V5",
    "V4_5PLUS",
    "V4_5ALL",
    "V4_5",
    "V4",
]

MODEL_LABELS: dict[str, str] = {
    "V5_5":     "V5_5  · кастомный голос",
    "V5":       "V5  · богатая выразительность",
    "V4_5PLUS": "V4_5+  · до 8 минут",
    "V4_5ALL":  "V4_5all  · до 8 минут",
    "V4_5":     "V4_5  · быстрый и точный",
    "V4":       "V4  · до 4 минут, проверенный",
}

# Settings keys
KEY_API_KEY = "suno.api_key"
KEY_MODEL = "suno.model"
KEY_CALLBACK_URL = "suno.callback_url"
KEY_TARGET_DURATION_SEC = "suno.target_duration_sec"

# Target song duration. Suno doesn't accept an explicit "duration"
# parameter — instead we steer it through prompt hints (and, when
# customMode lyrics are wired in, through how many verses we ask the
# LLM to produce). The default of 150s ≈ 2:30 lands close to a typical
# pop-song length without padding.
DEFAULT_TARGET_DURATION_SEC = 150
DURATION_PRESETS_SEC: tuple[int, ...] = (90, 120, 150, 180, 240)
MIN_TARGET_DURATION_SEC = 60
MAX_TARGET_DURATION_SEC = 480

# Suno task statuses (per `record-info` endpoint).
#
# The OpenAPI spec at https://docs.sunoapi.org/suno-api/get-music-generation-details.md
# enumerates the constants below. The Quickstart prose additionally mentions
# `GENERATING` (non-terminal) and `FAILED` (terminal, generic). We treat
# unknown statuses as non-terminal so the poller keeps trying — better than
# bailing on a value the docs forgot to list.
STATUS_PENDING = "PENDING"
STATUS_TEXT_SUCCESS = "TEXT_SUCCESS"
STATUS_FIRST_SUCCESS = "FIRST_SUCCESS"
STATUS_SUCCESS = "SUCCESS"
STATUS_CREATE_TASK_FAILED = "CREATE_TASK_FAILED"
STATUS_GENERATE_AUDIO_FAILED = "GENERATE_AUDIO_FAILED"
STATUS_CALLBACK_EXCEPTION = "CALLBACK_EXCEPTION"
STATUS_SENSITIVE_WORD_ERROR = "SENSITIVE_WORD_ERROR"
STATUS_FAILED = "FAILED"  # documented only in Quickstart prose

TERMINAL_STATUSES = {
    STATUS_SUCCESS,
    STATUS_CREATE_TASK_FAILED,
    STATUS_GENERATE_AUDIO_FAILED,
    STATUS_CALLBACK_EXCEPTION,
    STATUS_SENSITIVE_WORD_ERROR,
    STATUS_FAILED,
}

# https://docs.sunoapi.org/suno-api/generate-music.md → "Status Codes"
ERROR_CODE_HINTS: dict[int, str] = {
    400: "невалидные параметры",
    401: "ключ не принят (неверный или сброшен)",
    404: "путь или метод неверный",
    405: "превышен rate limit",
    413: "prompt слишком длинный — обрежь и попробуй ещё раз",
    429: "на аккаунте кончились кредиты",
    430: "слишком частые запросы — подожди 10–30 секунд",
    455: "у Suno техработы — попробуй позже",
    500: "ошибка на стороне Suno",
}


# ---------- DB-backed config helpers ----------

async def get_api_key(session: AsyncSession) -> str | None:
    return await get_setting(session, KEY_API_KEY)


async def set_api_key(session: AsyncSession, key: str) -> None:
    await set_setting(session, KEY_API_KEY, key.strip())


async def clear_api_key(session: AsyncSession) -> bool:
    return await delete_setting(session, KEY_API_KEY)


async def get_model(session: AsyncSession) -> str:
    value = await get_setting(session, KEY_MODEL)
    if value and value in SUPPORTED_MODELS:
        return value
    return DEFAULT_MODEL


async def set_model(session: AsyncSession, model: str) -> bool:
    if model not in SUPPORTED_MODELS:
        return False
    await set_setting(session, KEY_MODEL, model)
    return True


async def get_callback_url(session: AsyncSession) -> str:
    value = await get_setting(session, KEY_CALLBACK_URL)
    return value or DEFAULT_CALLBACK_URL


async def get_target_duration_sec(session: AsyncSession) -> int:
    """Target song length in seconds, clamped to the supported range.

    Falls back to :data:`DEFAULT_TARGET_DURATION_SEC` (≈2:30) when the
    setting is absent or unparseable. The value is used to compose a
    prompt hint for Suno (see :func:`format_duration_hint`) — it's not
    sent as a separate API parameter because Suno doesn't accept one.
    """
    raw = await get_setting(session, KEY_TARGET_DURATION_SEC)
    if not raw:
        return DEFAULT_TARGET_DURATION_SEC
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_TARGET_DURATION_SEC
    return max(
        MIN_TARGET_DURATION_SEC,
        min(MAX_TARGET_DURATION_SEC, value),
    )


async def set_target_duration_sec(session: AsyncSession, seconds: int) -> int:
    """Persist the target duration. Returns the value actually stored
    (clamped to the supported range, so callers can echo it back)."""
    clamped = max(
        MIN_TARGET_DURATION_SEC,
        min(MAX_TARGET_DURATION_SEC, int(seconds)),
    )
    await set_setting(session, KEY_TARGET_DURATION_SEC, str(clamped))
    return clamped


def format_duration_label(seconds: int) -> str:
    """Render a duration as ``M:SS`` for menu buttons / status lines."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def format_duration_hint(seconds: int) -> str:
    """English natural-language line appended to a Suno prompt to nudge
    the model toward the target length.

    Suno's prompt parser respects English directives more reliably than
    Russian ones, so we keep this string in English regardless of the
    user's prompt language.
    """
    label = format_duration_label(seconds)
    return (
        f"\n\n[Length: about {label} (~{seconds}s). Keep it concise: "
        "single verse, chorus, single verse, short outro. "
        "No long intro, no extended bridge.]"
    )


def append_duration_hint(prompt: str, seconds: int) -> str:
    """Idempotently append a duration hint to a user prompt.

    If the prompt already ends with a ``[Length: ...]`` directive
    (e.g. user pasted one in by hand) we leave it alone so admins keep
    fine-grained control.
    """
    if "[Length:" in prompt:
        return prompt
    return prompt.rstrip() + format_duration_hint(seconds)


def mask_key(key: str | None) -> str:
    """Safe-to-show / safe-to-log version of an API key."""
    if not key:
        return "(не задан)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


# ---------- HTTP client ----------


class SunoApiError(RuntimeError):
    """Wraps any non-200 response from sunoapi.org or transport errors."""

    def __init__(self, code: int, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg

    def humanized(self) -> str:
        """Russian-language one-liner for showing in the bot UI."""
        hint = ERROR_CODE_HINTS.get(self.code)
        if hint:
            return f"{hint} (код {self.code}: {self.msg})"
        if self.code == 0:
            return f"сеть/таймаут: {self.msg}"
        return f"код {self.code}: {self.msg}"


class SunoApiOrgClient:
    """Thin async client for sunoapi.org. One instance per request is fine.

    Usage:
        client = SunoApiOrgClient(api_key)
        credits = await client.get_credits()
        task_id = await client.generate_music(prompt="A short relaxing piano tune")
        snapshot = await client.get_task(task_id)
    """

    def __init__(self, api_key: str, *, timeout: float = 60.0):
        if not api_key:
            raise ValueError("Suno API key is empty")
        self._api_key = api_key
        self._timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
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
                "suno %s %s network error (key=%s): %s",
                method,
                path,
                mask_key(self._api_key),
                exc,
            )
            raise SunoApiError(0, f"сеть: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            raise SunoApiError(
                resp.status_code,
                (resp.text or "").strip()[:200] or f"HTTP {resp.status_code}",
            )

        if not isinstance(data, dict):
            raise SunoApiError(resp.status_code, f"unexpected payload: {data!r}"[:200])

        code = data.get("code", resp.status_code)
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = resp.status_code

        if code_int != 200:
            raise SunoApiError(code_int, str(data.get("msg") or "unknown"))
        return data

    async def get_credits(self) -> int:
        """Remaining credits on the account.

        The official docs are inconsistent on the path of this endpoint:
        the OpenAPI spec lists `/api/v1/generate/credit`, while the
        Quickstart sample code uses `/api/v1/get-credits`. We try the
        OpenAPI path first and fall back to the Quickstart path on 404.
        """
        try:
            data = await self._request("GET", "/api/v1/generate/credit")
        except SunoApiError as exc:
            if exc.code == 404:
                data = await self._request("GET", "/api/v1/get-credits")
            else:
                raise

        body = data.get("data")
        if isinstance(body, int):
            return body
        if isinstance(body, dict):
            for k in ("credit", "credits", "remaining"):
                if k in body:
                    try:
                        return int(body[k])
                    except (TypeError, ValueError):
                        pass
        try:
            return int(body)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    async def generate_music(
        self,
        *,
        prompt: str,
        model: str,
        callback_url: str,
        custom_mode: bool = False,
        instrumental: bool = False,
        style: str | None = None,
        title: str | None = None,
    ) -> str:
        """Submit a music-generation task. Returns the task_id.

        For the simplest "test from bot" flow use `custom_mode=False,
        instrumental=False` and only pass a `prompt` (≤500 chars). The API
        will auto-generate lyrics from the prompt.
        """
        body: dict[str, Any] = {
            "prompt": prompt,
            "customMode": custom_mode,
            "instrumental": instrumental,
            "model": model,
            "callBackUrl": callback_url,
        }
        if custom_mode:
            if style is not None:
                body["style"] = style
            if title is not None:
                body["title"] = title

        data = await self._request("POST", "/api/v1/generate", json=body)
        payload = data.get("data") or {}
        task_id = payload.get("taskId") or payload.get("task_id")
        if not task_id:
            raise SunoApiError(0, f"в ответе нет taskId: {data}"[:200])
        return str(task_id)

    async def get_task(self, task_id: str) -> "TaskSnapshot":
        """Poll a generation task. Returns a TaskSnapshot with status and
        (when ready) audio/stream URLs."""
        data = await self._request(
            "GET",
            "/api/v1/generate/record-info",
            params={"taskId": task_id},
        )
        payload = data.get("data") or {}
        return TaskSnapshot.from_response(payload)


@dataclass
class TaskSnapshot:
    """Cleaned-up view of a `record-info` response.

    The first track is exposed as the canonical result; raw payload is
    preserved on `.raw` for callers that need both tracks or extra
    metadata.

    The Suno docs are inconsistent about the array key inside `response`:
    the OpenAPI schema uses `sunoData[]` with camelCase fields, while
    the Quickstart prose uses `data[]` with snake_case fields. We accept
    both shapes so the client doesn't break if the API switches.
    """

    status: str
    title: str | None
    audio_url: str | None
    stream_url: str | None
    image_url: str | None
    duration: float | None
    error_message: str | None
    raw: dict[str, Any]

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> "TaskSnapshot":
        status = str(
            payload.get("status")
            or payload.get("statusMsg")
            or STATUS_PENDING
        )
        response = payload.get("response") or {}
        # Accept either OpenAPI `sunoData` (camelCase) or Quickstart `data`
        # (snake_case) shape — see class docstring.
        items = response.get("sunoData")
        if not items:
            items = response.get("data") or []
        first = items[0] if items else {}

        return cls(
            status=status,
            title=_first(first, "title"),
            audio_url=_first(first, "audioUrl", "audio_url"),
            stream_url=_first(first, "streamAudioUrl", "stream_audio_url"),
            image_url=_first(first, "imageUrl", "image_url"),
            duration=_to_float(_first(first, "duration")),
            error_message=_first(payload, "errorMessage", "error_message"),
            raw=payload,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_success(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def is_failure(self) -> bool:
        return self.is_terminal and not self.is_success


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
