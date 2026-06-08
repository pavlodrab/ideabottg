"""Aiogram middleware that opens an :class:`AsyncSession` per update.

Registered on ``dp.update.middleware`` so every message, callback query
and other update gets a session injected as ``data["session"]``. The
session is closed automatically when the handler returns.

Handlers receive it via the standard aiogram dependency injection — just
add ``session: AsyncSession`` to the signature.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self._session_maker = session_maker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_maker() as session:
            data["session"] = session
            return await handler(event, data)
