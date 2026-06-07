from aiogram.fsm.state import State, StatesGroup


class IdeaSubmission(StatesGroup):
    waiting_text = State()
    waiting_anonymity = State()
