from typing import NamedTuple


class SchedulePreset(NamedTuple):
    key: str
    label: str
    cron: str


PRESETS: list[SchedulePreset] = [
    SchedulePreset("daily_09", "Каждый день 09:00", "0 9 * * *"),
    SchedulePreset("daily_12", "Каждый день 12:00", "0 12 * * *"),
    SchedulePreset("daily_18", "Каждый день 18:00", "0 18 * * *"),
    SchedulePreset("weekdays_12", "По будням 12:00", "0 12 * * 1-5"),
    SchedulePreset("monday_10", "Понедельник 10:00", "0 10 * * 1"),
    SchedulePreset("hourly", "Каждый час", "0 * * * *"),
    SchedulePreset("every_3h", "Каждые 3 часа", "0 */3 * * *"),
    SchedulePreset("every_6h", "Каждые 6 часов", "0 */6 * * *"),
]

PRESETS_BY_KEY: dict[str, SchedulePreset] = {p.key: p for p in PRESETS}
PRESETS_BY_CRON: dict[str, SchedulePreset] = {p.cron: p for p in PRESETS}


def humanize_cron(cron: str | None) -> str:
    if not cron:
        return "не задано"
    preset = PRESETS_BY_CRON.get(cron.strip())
    if preset:
        return preset.label
    return f"<code>{cron}</code>"
