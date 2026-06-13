from aiogram import Dispatcher

from app.middlewares.capture import CaptureMiddleware
from app.middlewares.db import DbSessionMiddleware


def register_middlewares(dp: Dispatcher) -> None:
    db = DbSessionMiddleware()
    capture = CaptureMiddleware()

    # Order matters: DbSessionMiddleware runs FIRST (outer) so the
    # session is in data["session"] before CaptureMiddleware reads it.
    # Aiogram registers middleware in chain order: first registered
    # = outermost = called first.
    dp.message.middleware(db)
    dp.message.middleware(capture)

    dp.callback_query.middleware(db)
    dp.my_chat_member.middleware(db)
    dp.chat_member.middleware(db)
