"""Quiet hours (night mode) — global, configurable from inside the bot.

Storage model
-------------

The three settings (``enabled``, ``start``, ``end``) live in the existing
``settings`` key-value table so admins can change them at runtime via
``/quiet`` without redeploying. The env vars in :mod:`app.config` are
*initial defaults* used only when the rows are absent from the DB.

Runtime model
-------------

A small in-process cache (:data:`_state`) is populated on startup
(:func:`load_from_db`) and refreshed by :func:`save_to_db` when an admin
edits the values. The check used by the scheduler
(:func:`is_quiet_hours`) reads from the cache and is therefore sync and
allocation-free — important because it runs from every cron fire.

The bot is single-process (long polling), so the cache does not need
cross-process invalidation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Setting

log = logging.getLogger(__name__)

# Setting keys.
KEY_ENABLED = "quiet_hours_enabled"
KEY_START = "quiet_hours_start"
KEY_END = "quiet_hours_end"


@dataclass
class QuietHoursState:
    enabled: bool
    start: str  # HH:MM
    end: str    # HH:MM


# Quick presets exposed in the UI keyboard. Keys are stable callback
# tokens; (start, end) values are the actual HH:MM strings stored.
QUIET_HOURS_PRESETS: dict[str, tuple[str, str]] = {
    "23_08": ("23:00", "08:00"),
    "22_09": ("22:00", "09:00"),
    "00_07": ("00:00", "07:00"),
    "21_09": ("21:00", "09:00"),
}

# Render order + label for the keyboard.
QUIET_HOURS_PRESET_ROWS: list[tuple[str, str]] = [
    ("23_08", "23:00 → 08:00"),
    ("22_09", "22:00 → 09:00"),
    ("00_07", "00:00 → 07:00"),
    ("21_09", "21:00 → 09:00"),
]


# Initialised from env so even before load_from_db() runs the gate
# behaves sensibly (e.g. if scheduler somehow fires during startup).
_state = QuietHoursState(
    enabled=settings.quiet_hours_enabled,
    start=settings.quiet_hours_start,
    end=settings.quiet_hours_end,
)


# ---------- parsing helpers ----------

def _parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"ожидаю HH:MM, получил {value!r}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"не число в {value!r}") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"вне диапазона: {value!r}")
    return time(hour=hour, minute=minute)


def normalize_hhmm(value: str) -> str:
    """Validate and return the canonical zero-padded HH:MM form."""
    t = _parse_hhmm(value)
    return f"{t.hour:02d}:{t.minute:02d}"


# ---------- cache access ----------

def get_state() -> QuietHoursState:
    """Snapshot of the currently active quiet-hours config."""
    return _state


# ---------- persistence ----------

async def load_from_db(session: AsyncSession) -> QuietHoursState:
    """Populate the cache from the ``settings`` table.

    Missing rows fall back to env defaults; malformed HH:MM values fall
    back to env and emit a warning instead of crashing the bot — we'd
    rather have wrong-but-quiet defaults than a startup loop.
    """
    global _state
    rows = await session.execute(
        select(Setting).where(Setting.key.in_([KEY_ENABLED, KEY_START, KEY_END]))
    )
    by_key = {s.key: s.value for s in rows.scalars().all()}

    enabled_raw = by_key.get(KEY_ENABLED)
    start_raw = by_key.get(KEY_START) or settings.quiet_hours_start
    end_raw = by_key.get(KEY_END) or settings.quiet_hours_end

    if enabled_raw is None:
        enabled = settings.quiet_hours_enabled
    else:
        enabled = enabled_raw.strip().lower() in {"true", "1", "yes", "on"}

    try:
        start = normalize_hhmm(start_raw)
        end = normalize_hhmm(end_raw)
    except ValueError:
        log.warning(
            "quiet_hours: invalid HH:MM in DB (start=%r, end=%r); using env defaults",
            start_raw, end_raw,
        )
        start = normalize_hhmm(settings.quiet_hours_start)
        end = normalize_hhmm(settings.quiet_hours_end)

    _state = QuietHoursState(enabled=enabled, start=start, end=end)
    log.info("quiet_hours loaded: %s", _state)
    return _state


async def save_to_db(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    start: str | None = None,
    end: str | None = None,
) -> QuietHoursState:
    """Persist the supplied fields and refresh the cache atomically.

    Only fields explicitly passed are updated; the rest keep their
    current values. We re-validate HH:MM here defensively even though
    callers should already have called :func:`normalize_hhmm`.
    """
    global _state
    new_enabled = _state.enabled if enabled is None else bool(enabled)
    new_start = _state.start if start is None else normalize_hhmm(start)
    new_end = _state.end if end is None else normalize_hhmm(end)

    await _upsert(session, KEY_ENABLED, "true" if new_enabled else "false")
    await _upsert(session, KEY_START, new_start)
    await _upsert(session, KEY_END, new_end)
    await session.commit()

    _state = QuietHoursState(enabled=new_enabled, start=new_start, end=new_end)
    log.info("quiet_hours updated: %s", _state)
    return _state


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    obj = await session.get(Setting, key)
    if obj is None:
        session.add(Setting(key=key, value=value))
    else:
        obj.value = value


# ---------- runtime check used by scheduler ----------

def is_quiet_hours(now: datetime | None = None) -> bool:
    """Return True when *now* falls into the configured quiet window.

    Uses ``settings.tz``. The window may wrap past midnight
    (``23:00 → 08:00``). A degenerate window where start equals end is
    treated as empty — never quiet — because it's the safer default if
    someone misconfigures both bounds to the same value.
    """
    if not _state.enabled:
        return False

    tz = ZoneInfo(settings.tz)
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    start = _parse_hhmm(_state.start)
    end = _parse_hhmm(_state.end)
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
