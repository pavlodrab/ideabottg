"""Quiet hours (a.k.a. night mode).

Suppresses *proactive* bot messages — scheduled prompts, broadcasts,
reminders — during the configured night window. Reactive replies to user
commands are NOT affected: if a user writes to the bot at night, the bot
must still respond.

Usage::

    from app.services.quiet_hours import should_send_proactive

    if should_send_proactive():
        await bot.send_message(chat_id, "...")
    else:
        log.info("Skipping proactive send: quiet hours")
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.config import settings


def _parse_hhmm(value: str) -> time:
    """Parse an ``HH:MM`` string into a :class:`datetime.time`.

    Raises :class:`ValueError` if the format is invalid — surfacing
    misconfiguration early at startup is preferred over silent fallbacks.
    """
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Expected HH:MM, got {value!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    return time(hour=hour, minute=minute)


def is_quiet_hours(now: datetime | None = None) -> bool:
    """Return True if *now* falls into the configured quiet window.

    The window uses the bot's configured timezone (``settings.tz``) and
    may wrap over midnight, e.g. ``23:00 → 08:00``. If start equals end,
    the window is considered empty (never quiet).

    :param now: optional override, useful for tests. If naive, it is
        assumed to be in the bot's timezone.
    """
    if not settings.quiet_hours_enabled:
        return False

    tz = ZoneInfo(settings.tz)
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    start = _parse_hhmm(settings.quiet_hours_start)
    end = _parse_hhmm(settings.quiet_hours_end)
    current = now.time()

    if start == end:
        return False

    if start < end:
        # Same-day window, e.g. 09:00 → 18:00.
        return start <= current < end

    # Wrap-around midnight, e.g. 23:00 → 08:00.
    return current >= start or current < end


def should_send_proactive(now: datetime | None = None) -> bool:
    """Convenience inverse of :func:`is_quiet_hours`."""
    return not is_quiet_hours(now)
