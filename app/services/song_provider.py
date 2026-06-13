"""Song-generation provider abstraction (spec design §3.6).

A ``SongProvider`` turns a ``SongDraft`` into audio. Two concrete
implementations:

- :class:`SunoApiOrgProvider` — the default, wrapping the existing
  :class:`app.services.suno.SunoApiOrgClient` (customMode submit + poll).
- :class:`LyricsOnlyProvider` — the fallback used when Suno is
  unavailable or times out: it returns a result with no audio so the
  orchestrator posts the lyrics as text (requirement F5.4).

The active provider is chosen by the ``suno.provider`` DB setting
(default ``sunoapi_org``), see :func:`get_song_provider`. The
``self_hosted`` (gcui-art/suno-api) provider from the design is left as
a genuine TODO — it needs a running self-hosted service to target — and
the factory raises a clear error if it's selected without that wiring.

Consumed by :mod:`app.services.daily_song` (the scheduled orchestrator).
The manual ``/song_now`` flow still uses ``song_pipeline`` directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.settings import get_setting
from app.services.song_pipeline import SongDraft
from app.services.suno import (
    SunoApiOrgClient,
    TaskSnapshot,
    get_api_key as get_suno_api_key,
    get_callback_url,
    get_model as get_suno_model,
)

log = logging.getLogger(__name__)

# DB setting key + allowed values.
KEY_PROVIDER = "suno.provider"
PROVIDER_SUNOAPI_ORG = "sunoapi_org"
PROVIDER_SELF_HOSTED = "self_hosted"
PROVIDER_LYRICS_ONLY = "lyrics_only"
DEFAULT_PROVIDER = PROVIDER_SUNOAPI_ORG


@dataclass
class SongResult:
    """Terminal result of a generation. ``audio_url is None`` means a
    lyrics-only outcome (no mp3)."""

    audio_url: str | None
    stream_url: str | None = None
    image_url: str | None = None
    duration: float | None = None
    title: str | None = None

    @property
    def is_lyrics_only(self) -> bool:
        return self.audio_url is None


@runtime_checkable
class SongProvider(Protocol):
    name: str

    async def submit(self, draft: SongDraft) -> str:
        """Start generation, return a task id."""
        ...

    async def poll(self, task_id: str) -> SongResult | None:
        """Return the result when terminal, else ``None`` (still running).

        Raises on a terminal *failure* so the orchestrator can fall back.
        """
        ...


class SongProviderError(RuntimeError):
    """Terminal provider failure (so the orchestrator falls back)."""


class SunoApiOrgProvider:
    """Wraps :class:`SunoApiOrgClient` in customMode (our title/style/lyrics)."""

    name = PROVIDER_SUNOAPI_ORG

    def __init__(
        self, *, api_key: str, model: str, callback_url: str
    ) -> None:
        self._client = SunoApiOrgClient(api_key)
        self._model = model
        self._callback_url = callback_url

    async def submit(self, draft: SongDraft) -> str:
        return await self._client.generate_music(
            prompt=draft.lyrics,
            model=self._model,
            callback_url=self._callback_url,
            custom_mode=True,
            instrumental=False,
            style=draft.style,
            title=draft.title,
        )

    async def poll(self, task_id: str) -> SongResult | None:
        snapshot: TaskSnapshot = await self._client.get_task(task_id)
        if not snapshot.is_terminal:
            return None
        if snapshot.is_failure:
            raise SongProviderError(
                snapshot.error_message or snapshot.status
            )
        return SongResult(
            audio_url=snapshot.audio_url,
            stream_url=snapshot.stream_url,
            image_url=snapshot.image_url,
            duration=snapshot.duration,
            title=snapshot.title,
        )


class LyricsOnlyProvider:
    """Fallback: no audio, the orchestrator posts the lyrics as text."""

    name = PROVIDER_LYRICS_ONLY

    async def submit(self, draft: SongDraft) -> str:
        return "lyrics-only"

    async def poll(self, task_id: str) -> SongResult | None:
        # Synchronously "done" with no audio.
        return SongResult(audio_url=None)


async def get_song_provider(session: AsyncSession) -> SongProvider:
    """Build the active provider from the ``suno.provider`` setting.

    Defaults to sunoapi.org. ``lyrics_only`` is explicit opt-in (testing
    without spending Suno credits). ``self_hosted`` isn't wired yet and
    raises a clear error rather than silently misbehaving.
    """
    name = (await get_setting(session, KEY_PROVIDER)) or DEFAULT_PROVIDER
    if name == PROVIDER_LYRICS_ONLY:
        return LyricsOnlyProvider()
    if name == PROVIDER_SELF_HOSTED:
        raise SongProviderError(
            "self_hosted provider не сконфигурирован (нет SUNO_API_BASE-обёртки)"
        )
    # default: sunoapi_org
    api_key = await get_suno_api_key(session)
    if not api_key:
        raise SongProviderError("Suno API-ключ не задан")
    model = await get_suno_model(session)
    callback_url = await get_callback_url(session)
    return SunoApiOrgProvider(
        api_key=api_key, model=model, callback_url=callback_url
    )


__all__ = [
    "DEFAULT_PROVIDER",
    "KEY_PROVIDER",
    "LyricsOnlyProvider",
    "SongProvider",
    "SongProviderError",
    "SongResult",
    "SunoApiOrgProvider",
    "get_song_provider",
]
