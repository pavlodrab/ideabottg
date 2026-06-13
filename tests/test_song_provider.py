"""Tests for the SongProvider abstraction + factory selection."""
import pytest

from app.services.settings import set_setting
from app.services.song_pipeline import SongDraft
from app.services.song_provider import (
    LyricsOnlyProvider,
    SongProviderError,
    SunoApiOrgProvider,
    get_song_provider,
)
from app.services.suno import set_api_key


def _draft():
    return SongDraft(title="T", style="S", lyrics="L", summary="")


@pytest.mark.asyncio
async def test_lyrics_only_provider_returns_no_audio():
    p = LyricsOnlyProvider()
    task_id = await p.submit(_draft())
    assert task_id == "lyrics-only"
    result = await p.poll(task_id)
    assert result is not None
    assert result.is_lyrics_only is True
    assert result.audio_url is None


@pytest.mark.asyncio
async def test_factory_lyrics_only_setting(session):
    await set_setting(session, "suno.provider", "lyrics_only")
    provider = await get_song_provider(session)
    assert isinstance(provider, LyricsOnlyProvider)


@pytest.mark.asyncio
async def test_factory_default_sunoapi_org_with_key(session):
    await set_api_key(session, "sk-test-123456")
    provider = await get_song_provider(session)
    assert isinstance(provider, SunoApiOrgProvider)
    assert provider.name == "sunoapi_org"


@pytest.mark.asyncio
async def test_factory_default_without_key_raises(session):
    with pytest.raises(SongProviderError):
        await get_song_provider(session)


@pytest.mark.asyncio
async def test_factory_self_hosted_not_wired(session):
    await set_setting(session, "suno.provider", "self_hosted")
    with pytest.raises(SongProviderError):
        await get_song_provider(session)
