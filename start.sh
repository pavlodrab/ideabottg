#!/bin/sh
# Loud, explicit start sequence. Each step echoes BEFORE running so even
# silent crashes leave a fingerprint in the deploy log.

set -e

echo "==> [start.sh] container started"
echo "==> [start.sh] python: $(python --version)"
echo "==> [start.sh] cwd: $(pwd)"
echo "==> [start.sh] DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo NO)"
echo "==> [start.sh] BOT_TOKEN set: $([ -n "$BOT_TOKEN" ] && echo yes || echo NO)"
echo "==> [start.sh] OWNER_ID set: $([ -n "$OWNER_ID" ] && echo yes || echo NO)"

echo "==> [start.sh] running alembic upgrade head"
alembic upgrade head
echo "==> [start.sh] alembic finished, starting bot"

exec python -m app.main
