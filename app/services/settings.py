"""Generic key-value store backed by the existing `settings` table.

Used for runtime-editable, owner-managed bot settings that live in DB
(rather than env vars). Per-feature modules pick a unique key prefix
(e.g. `suno.api_key`, `suno.model`) and use these helpers.

The table is `Setting(key TEXT PK, value TEXT NULL)` — see `app/models.py`.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting


async def get_setting(session: AsyncSession, key: str) -> str | None:
    """Return the value for `key`, or None if the key is absent or unset."""
    row = await session.get(Setting, key)
    if row is None:
        return None
    return row.value


async def set_setting(
    session: AsyncSession, key: str, value: str | None
) -> None:
    """Insert or update a setting. Commits on success."""
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def delete_setting(session: AsyncSession, key: str) -> bool:
    """Remove a setting. Returns True if a row was deleted."""
    row = await session.get(Setting, key)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
