"""Tests for /music command parsing and the prompt songwriter message."""
import pytest

from app.handlers.song_admin import parse_music_command
from app.services.song_pipeline import _build_prompt_user_message


@pytest.mark.parametrize(
    "raw,idea,style",
    [
        ("Андрюха крутой чек пук стиль панк", "Андрюха крутой чек пук", "панк"),
        ("песня про кофе в стиле lo-fi", "песня про кофе", "lo-fi"),
        ("song about friday style punk rock", "song about friday", "punk rock"),
        ("просто текст без стиля", "просто текст без стиля", None),
        ("  trimmed  ", "trimmed", None),
    ],
)
def test_parse_music_command(raw, idea, style):
    assert parse_music_command(raw) == (idea, style)


def test_parse_music_command_style_marker_without_idea():
    # No idea before the marker -> whole text is the idea, no style.
    idea, style = parse_music_command("стиль панк")
    assert idea == "стиль панк"
    assert style is None


def test_parse_music_command_takes_last_marker():
    # "стиль жизни" in the idea shouldn't be mistaken — the regex is
    # non-greedy and anchored at the end, so the final marker wins.
    idea, style = parse_music_command("стиль жизни это кайф стиль рэп")
    assert idea == "стиль жизни это кайф"
    assert style == "рэп"


def test_build_prompt_user_message_with_style():
    msg = _build_prompt_user_message("привет мир", 150, "панк")
    assert "привет мир" in msg
    assert "СТИЛЬ ЗАДАН" in msg
    assert "панк" in msg
    assert "JSON" in msg


def test_build_prompt_user_message_without_style():
    msg = _build_prompt_user_message("привет мир", 150, None)
    assert "привет мир" in msg
    assert "выбери САМ" in msg
