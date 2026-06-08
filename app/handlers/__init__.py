from aiogram import Dispatcher

from app.handlers import (
    admin_menu,
    admin_users,
    chats,
    common,
    ideas,
    ideas_browser,
    quiet_hours,
    voting,
)


def register_handlers(dp: Dispatcher) -> None:
    # ideas first so deep-link `/start idea_<id>` wins over plain /start
    dp.include_router(ideas.router)
    dp.include_router(ideas_browser.router)
    dp.include_router(voting.router)
    dp.include_router(admin_menu.router)
    dp.include_router(admin_users.router)
    dp.include_router(quiet_hours.router)
    dp.include_router(chats.router)
    dp.include_router(common.router)
