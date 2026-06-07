from aiogram.fsm.state import State, StatesGroup


class IdeaSubmission(StatesGroup):
    waiting_text = State()
    waiting_anonymity = State()


class PromptEditing(StatesGroup):
    waiting_text = State()


class ScheduleCustom(StatesGroup):
    waiting_cron = State()


class AdminAdd(StatesGroup):
    waiting_user = State()
