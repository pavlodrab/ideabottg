from aiogram.fsm.state import State, StatesGroup


class IdeaSubmission(StatesGroup):
    waiting_text = State()
    waiting_tag = State()
    waiting_anonymity = State()


class PromptEditing(StatesGroup):
    waiting_text = State()


class ScheduleCustom(StatesGroup):
    waiting_cron = State()


class AdminAdd(StatesGroup):
    waiting_user = State()


class AdminReply(StatesGroup):
    waiting_text = State()


class QuietHoursEdit(StatesGroup):
    waiting_window = State()


class SunoApiKeyEditing(StatesGroup):
    waiting_key = State()


class SunoTestPrompt(StatesGroup):
    waiting_prompt = State()


class MusicCustomStyle(StatesGroup):
    waiting_text = State()
