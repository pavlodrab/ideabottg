"""Helpers around the :class:`~app.models.Admin` table.

Two responsibilities:

* :func:`ensure_owner` — idempotently writes the configured ``OWNER_ID``
  into the table on every startup; if the row already exists, makes
  sure ``is_owner`` is True.
* :func:`is_admin` — quick existence check used by admin-only handlers.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin


async def ensure_owner(session: AsyncSession, owner_id: int) -> Admin:
    """Make sure the configured owner exists in the admins table."""
    admin = await session.get(Admin, owner_id)
    if admin is None:
        admin = Admin(user_id=owner_id, is_owner=True, receive_ideas=True)
        session.add(admin)
    elif not admin.is_owner:
        admin.is_owner = True
    await session.commit()
    return admin


async def is_admin(session: AsyncSession, user_id: int) -> bool:
    """Return True iff *user_id* is registered as an admin (or owner)."""
    admin = await session.get(Admin, user_id)
    return admin is not None
