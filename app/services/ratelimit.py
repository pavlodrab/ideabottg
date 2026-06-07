"""In-memory rate limiter for idea submissions.

Single shared instance is used by both the DM FSM and the in-chat reply
capture. State is per-process; on restart everyone gets a fresh window,
which is acceptable for a soft anti-spam guard.
"""
import time
from typing import Final

DEFAULT_COOLDOWN_SEC: Final = 30


class IdeaRateLimiter:
    def __init__(self, cooldown_sec: int = DEFAULT_COOLDOWN_SEC) -> None:
        self.cooldown = cooldown_sec
        self._last: dict[int, float] = {}

    def remaining(self, user_id: int) -> int:
        """Return seconds the user must wait, or 0 if allowed."""
        last = self._last.get(user_id, 0.0)
        wait = self.cooldown - (time.monotonic() - last)
        if wait > 0:
            return int(wait) + 1
        return 0

    def record(self, user_id: int) -> None:
        self._last[user_id] = time.monotonic()


idea_rate_limiter = IdeaRateLimiter()
