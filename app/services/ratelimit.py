"""Persistent rate limiter for idea submissions.

Uses the existing `ideas` table as the source of truth: the cooldown is
measured against `MAX(created_at) WHERE from_user_id = ?`. This means:

- Cooldowns survive bot restarts and work across multiple bot instances
  sharing the database.
- No extra writes — the limiter is read-only; submission itself is the
  recorded event.
- Failed submissions (length checks etc) don't count, which is what we
  want anyway.
"""
from datetime import datetime, timezone
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Idea

DEFAULT_COOLDOWN_SEC: Final = 30


class IdeaRateLimiter:
    def __init__(self, cooldown_sec: int = DEFAULT_COOLDOWN_SEC) -> None:
        self.cooldown = cooldown_sec

    async def remaining(
        self, session: AsyncSession, user_id: int
    ) -> int:
        """Return seconds the user must wait, or 0 if allowed."""
        result = await session.execute(
            select(func.max(Idea.created_at)).where(Idea.from_user_id == user_id)
        )
        last = result.scalar_one_or_none()
        if last is None:
            return 0

        # SQLite returns naive datetimes; treat as UTC for consistency.
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        delta = (now - last).total_seconds()
        wait = self.cooldown - delta
        if wait <= 0:
            return 0
        return int(wait) + 1


idea_rate_limiter = IdeaRateLimiter()
