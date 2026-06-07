from aiogram import Dispatcher

from app.handlers import chats, common, ideas


def register_handlers(dp: Dispatcher) -> None:
    # ideas first so deep-link `/start idea_<id>` wins over plain /start
    dp.include_router(ideas.router)
    dp.include_router(chats.router)
    dp.include_router(common.router)
