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

# Suno task statuses (per `record-info` endpoint)
STATUS_PENDING = "PENDING"
STATUS_TEXT_SUCCESS = "TEXT_SUCCESS"
STATUS_FIRST_SUCCESS = "FIRST_SUCCESS"
STATUS_SUCCESS = "SUCCESS"
STATUS_CREATE_TASK_FAILED = "CREATE_TASK_FAILED"
STATUS_GENERATE_AUDIO_FAILED = "GENERATE_AUDIO_FAILED"
STATUS_CALLBACK_EXCEPTION = "CALLBACK_EXCEPTION"
STATUS_SENSITIVE_WORD_ERROR = "SENSITIVE_WORD_ERROR"

TERMINAL_STATUSES = {
    STATUS_SUCCESS,
    STATUS_CREATE_TASK_FAILED,
    STATUS_GENERATE_AUDIO_FAILED,
    STATUS_CALLBACK_EXCEPTION,
    STATUS_SENSITIVE_WORD_ERROR,
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
        """Remaining credits on the account."""
        data = await self._request("GET", "/api/v1/generate/credit")
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

    The first track in `sunoData` is exposed as the canonical result;
    the raw payload is preserved on `.raw` for callers that need both
    tracks or extra metadata.
    """

    status: str
    title: str | None
    audio_url: str | None
    stream_url: str | None
    image_url: str | None
    duration: float | None
    raw: dict[str, Any]

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> "TaskSnapshot":
        status = str(
            payload.get("status")
            or payload.get("statusMsg")
            or STATUS_PENDING
        )
        response = payload.get("response") or {}
        items = response.get("sunoData") or []
        first = items[0] if items else {}

        return cls(
            status=status,
            title=_first(first, "title"),
            audio_url=_first(first, "audioUrl", "audio_url"),
            stream_url=_first(first, "streamAudioUrl", "stream_audio_url"),
            image_url=_first(first, "imageUrl", "image_url"),
            duration=_to_float(_first(first, "duration")),
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
