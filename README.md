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

Configure via env vars (defaults shown):

```env
QUIET_HOURS_ENABLED=true
QUIET_HOURS_START=23:00
QUIET_HOURS_END=08:00
```

Times are `HH:MM` interpreted in `TZ`. The window may wrap midnight
(`23:00 → 08:00`). Set `QUIET_HOURS_ENABLED=false` to disable entirely.

Scheduled jobs and any future broadcast code should gate proactive sends
through `app.services.quiet_hours.should_send_proactive()`.

## Project layout

See `app/` for handlers, services, and database models.
Roadmap is tracked in chat.
