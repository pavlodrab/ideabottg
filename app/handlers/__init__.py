from aiogram import Dispatcher

from app.handlers import (
    admin_menu,
    admin_users,
    chats,
    common,
    ideas,
    ideas_browser,
    llm_admin,
    logs,
    music,
    musicmenu_admin,
    quiet_hours,
    song_admin,
    suno_admin,
    voting,
)


def register_handlers(dp: Dispatcher) -> None:
    # ideas first so deep-link `/start idea_<id>` wins over plain /start
    dp.include_router(ideas.router)
    dp.include_router(ideas_browser.router)
    dp.include_router(voting.router)

    # musicmenu_admin is included BEFORE admin_menu so that the
    # `home` callback used by older keyboards (suno menu, qh panel,
    # chats list footer, etc.) lands on the new unified screen
    # rather than the legacy home_keyboard. Same goes for `/menu` —
    # cmd_menu is still defined in admin_menu.py and falls through
    # only because /musicmenu is the new canonical entry.
    dp.include_router(musicmenu_admin.router)

    dp.include_router(admin_menu.router)
    dp.include_router(admin_users.router)
    dp.include_router(quiet_hours.router)
    dp.include_router(suno_admin.router)
    dp.include_router(llm_admin.router)
    dp.include_router(logs.router)
    # song_admin owns `/song_now` and the `mm:gen_pick / mm:gen:* /
    # music:gen_now:*` callbacks. Order before `music` matters — the
    # `music:gen_now:*` callback is namespaced under `music:` only by
    # name, but its handler lives here.
    dp.include_router(song_admin.router)
    dp.include_router(music.router)
    dp.include_router(chats.router)
    dp.include_router(common.router)
