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

## Project layout

See `app/` for handlers, services, and database models.
Roadmap is tracked in chat.
