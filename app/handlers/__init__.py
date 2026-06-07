from aiogram import Dispatcher

from app.handlers import chats, common


def register_handlers(dp: Dispatcher) -> None:
    dp.include_router(common.router)
    dp.include_router(chats.router)
