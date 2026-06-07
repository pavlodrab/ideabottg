from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin


async def ensure_owner(session: AsyncSession, owner_id: int) -> Admin:
    """Make sure the configured OWNER_ID exists in admins as is_owner=True."""
    existing = await session.get(Admin, owner_id)
    if existing is None:
        admin = Admin(user_id=owner_id, is_owner=True, receive_ideas=True)
        session.add(admin)
        await session.commit()
        return admin

    if not existing.is_owner:
        existing.is_owner = True
        await session.commit()
    return existing


async def is_admin(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(Admin.user_id).where(Admin.user_id == user_id)
    )
    return result.scalar_one_or_none() is not None


async def is_owner(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(Admin.is_owner).where(Admin.user_id == user_id)
    )
    return bool(result.scalar_one_or_none())


async def get_idea_recipients(session: AsyncSession) -> list[int]:
    """All admins that opted in to receive ideas."""
    result = await session.execute(
        select(Admin.user_id).where(Admin.receive_ideas.is_(True))
    )
    return [row[0] for row in result.all()]


async def list_admins(session: AsyncSession) -> list[Admin]:
    result = await session.execute(
        select(Admin).order_by(Admin.is_owner.desc(), Admin.created_at.asc())
    )
    return list(result.scalars().all())


async def add_admin(
    session: AsyncSession, user_id: int, username: str | None
) -> tuple[Admin, bool]:
    """Add an admin or update username if already present. Returns (admin, created)."""
    existing = await session.get(Admin, user_id)
    if existing is not None:
        if username is not None and existing.username != username:
            existing.username = username
            await session.commit()
        return existing, False

    admin = Admin(
        user_id=user_id,
        username=username,
        is_owner=False,
        receive_ideas=True,
    )
    session.add(admin)
    await session.commit()
    return admin, True


async def remove_admin(session: AsyncSession, user_id: int) -> bool:
    """Remove an admin. Owners can't be removed."""
    existing = await session.get(Admin, user_id)
    if existing is None or existing.is_owner:
        return False
    await session.delete(existing)
    await session.commit()
    return True


async def toggle_receive_ideas(session: AsyncSession, user_id: int) -> Admin | None:
    admin = await session.get(Admin, user_id)
    if admin is None:
        return None
    admin.receive_ideas = not admin.receive_ideas
    await session.commit()
    return admin
