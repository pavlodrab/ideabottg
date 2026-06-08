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
- Deployed on Railway (Nixpacks, no Docker)

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

python main.py
```

## Railway deployment

1. Create a new Railway project, add a Postgres plugin.
   `DATABASE_URL` will be injected automatically.
2. Set `BOT_TOKEN` and `OWNER_ID` env vars in the service settings.
3. Deploy from this repo. Railway will use Nixpacks (Python is detected
   via `requirements.txt` + `runtime.txt`) and run the `startCommand`
   from `railway.toml` (migrations, then `python main.py`).

## Quiet hours (night mode)

To avoid pinging chats and admins at night, the bot suppresses all
*scheduled* messages — prompt posts and admin digests — while the
current time (in `TZ`) falls inside the configured window. Replies to
direct user commands are not affected.

**Configure from the bot.** Open `/menu` → "🌙 Тишина" or send `/quiet`
directly. From there an admin can:

- toggle the whole feature on/off,
- pick a quick preset (`23:00 → 08:00`, `22:00 → 09:00`, `00:00 →
  07:00`, `21:00 → 09:00`),
- enter a custom window in `HH:MM-HH:MM` form.

Live values are persisted in the `settings` key-value table, so they
survive restarts and don't need a redeploy.

The env vars below are only used as **initial defaults** — they seed
the values on the very first run when the table is empty:

```env
QUIET_HOURS_ENABLED=true
QUIET_HOURS_START=23:00
QUIET_HOURS_END=08:00
```

The window may wrap midnight (`23:00 → 08:00` = quiet from 23:00 until
08:00 next day).

Implementation note: cron jobs still fire on schedule; the quiet-hours
gate just makes the job exit early with a log line. Digest watermarks
(`last_digest_at`) are *not* advanced when a fire is skipped, so the
next non-quiet run covers the skipped window.

## Project layout

See `app/` for handlers, services, and database models.
Roadmap is tracked in chat.
