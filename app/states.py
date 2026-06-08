"""Aiogram FSM states used by admin handlers."""

from aiogram.fsm.state import State, StatesGroup


class QuietHoursEdit(StatesGroup):
    waiting_window = State()
