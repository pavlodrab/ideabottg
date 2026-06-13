"""daily_songs run-ledger

Revision ID: 0009_daily_songs
Revises: 0008_chats_song_schedule
Create Date: 2026-06-13

Adds the ``daily_songs`` table — a per-(chat, date) ledger of scheduled
song-of-the-day runs. One row per chat per local date (unique), driving:

- **dedup**: the unique ``(chat_id, date_local)`` index means a cron
  misfire / coalesce / restart-replay / manual overlap can't produce a
  second song for the same day.
- **status lifecycle**: queued → generating → done / skipped / failed,
  with ``error`` and ``suno_task_id`` captured for observability.
- **stale sweep**: rows stuck in queued/generating after a restart are
  marked failed on scheduler start (F8.3).

``song_id`` links to the produced ``songs`` row when generation
succeeds (nullable for skipped/failed/lyrics-only runs).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_daily_songs"
down_revision: Union[str, None] = "0008_chats_song_schedule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_songs",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            sa.BigInteger(),
            sa.ForeignKey("chats.chat_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date_local", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("suno_task_id", sa.String(length=64), nullable=True),
        sa.Column(
            "song_id",
            sa.Integer(),
            sa.ForeignKey("songs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("style", sa.Text(), nullable=True),
        sa.Column("n_messages", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "chat_id", "date_local", name="uq_daily_songs_chat_date"
        ),
    )
    op.create_index("ix_daily_songs_chat_id", "daily_songs", ["chat_id"])
    op.create_index("ix_daily_songs_status", "daily_songs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_daily_songs_status", table_name="daily_songs")
    op.drop_index("ix_daily_songs_chat_id", table_name="daily_songs")
    op.drop_table("daily_songs")
