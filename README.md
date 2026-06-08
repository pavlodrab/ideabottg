# IdeaBot

Telegram bot for collecting ideas from chat participants.
The bot posts a customizable prompt into chats on a schedule, and
forwards submitted ideas privately to the bot owners (admins).

## Stack

- Python 3.11
- aiogram 3.x
- SQLAlchemy 2 (async) + Postgres
- Alembic for migrations
- APScheduler for prompt scheduling
- Deployed on Railway via Docker

## Local setup

```bash
cp .env.example .env
# fill in BOT_TOKEN, OWNER_ID, DATABASE_URL

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create the first migration once models are ready, then upgrade:
alembic revision --autogenerate -m "init"
alembic upgrade head

python -m app.main
```

## Railway deployment

1. Create a new Railway project, add a Postgres plugin.
   `DATABASE_URL` will be injected automatically.
2. Set `BOT_TOKEN` and `OWNER_ID` env vars in the service settings.
3. Deploy from this repo. Railway will use the `Dockerfile` and the
   `startCommand` from `railway.toml` (runs migrations, then the bot).

## Quiet hours (night mode)

The bot can be silenced during night hours so it does not post scheduled
prompts or other proactive messages while everyone is asleep. Direct
replies to user commands always work, regardless of the time.

**Configure from the bot.** Send `/quiet` to the bot in private chat
(admin-only). The panel lets an admin:

- toggle the whole feature on/off,
- pick a quick preset (`23:00 → 08:00`, `22:00 → 09:00`, `00:00 →
  07:00`, `21:00 → 09:00`),
- enter a custom window in `HH:MM-HH:MM` form.

Live values are persisted in the `settings` key-value table, so they
survive restarts and don't need a redeploy.

The env vars below are only used as **initial defaults** — they seed
the values on the very first run when the table rows are absent:

```env
QUIET_HOURS_ENABLED=true
QUIET_HOURS_START=23:00
QUIET_HOURS_END=08:00
```

Times are `HH:MM` interpreted in `TZ`. The window may wrap midnight
(`23:00 → 08:00` = quiet from 23:00 until 08:00 next day).

Scheduled jobs and any future broadcast code should gate proactive sends
through `app.services.quiet_hours.should_send_proactive()`.

## Project layout

See `app/` for handlers, services, and database models.
Roadmap is tracked in chat.
