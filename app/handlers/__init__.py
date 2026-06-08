from aiogram import Dispatcher

from app.handlers import common, quiet_hours


def register_handlers(dp: Dispatcher) -> None:
    # Order matters: admin-scoped routers come before the catch-all
    # `common` so that, e.g., a /quiet message during FSM input is
    # handled by the FSM-aware router and not silently swallowed.
    dp.include_router(quiet_hours.router)
    dp.include_router(common.router)
