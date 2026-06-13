"""chat_messages, songs, chats.song_style

Revision ID: 0007_chat_messages_and_songs
Revises: 0006_ideas_origin
Create Date: 2026-06-13

Adds two new tables:

- ``chat_messages``: append-only log of every text message sent in
  registered groups. Used by the daily-song pipeline to summarize the
  day's discussion. Records older than 2 days are pruned hourly by the
  scheduler (see ``app/scheduler.py``).

- ``songs``: every Suno generation that finished successfully. Songs
  are kept indefinitely (no auto-deletion). Suno mp3 URLs expire after
  15 days but we capture each song's Telegram ``file_id`` on first
  ``send_audio`` so playback keeps working.

Also adds ``chats.song_style`` so admins can pick a default style for
each chat through ``/musicmenu``.

Revision chain: this comes AFTER ``0006_ideas_origin`` (PR #24, admin
reply lands in source chat). The two were originally written in
parallel and both said ``down_revision = "0005_voting"``; this one was
re-pointed at merge time to keep the chain linear.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_chat_messages_and_songs"
down_revision: Union[str, None] = "0006_ideas_origin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column("song_style", sa.String(length=500), nullable=True),
    )

    op.create_table(
        "chat_messages",
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
        sa.Column("tg_message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Inline UniqueConstraint so this works on SQLite too (smoke
        # tests). PG-only ALTER TABLE ADD CONSTRAINT would require
        # batch_alter_table here.
        sa.UniqueConstraint(
            "chat_id", "tg_message_id", name="uq_chat_messages_chat_tgmsg"
        ),
    )
    op.create_index(
        "ix_chat_messages_chat_id", "chat_messages", ["chat_id"]
    )
    op.create_index(
        "ix_chat_messages_created_at", "chat_messages", ["created_at"]
    )
    op.create_index(
        "ix_chat_messages_chat_created",
        "chat_messages",
        ["chat_id", "created_at"],
    )

    op.create_table(
        "songs",
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
            sa.ForeignKey("chats.chat_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("suno_task_id", sa.String(length=64), nullable=False),
        sa.Column("suno_audio_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("style", sa.String(length=500), nullable=True),
        sa.Column("model", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("lyrics", sa.Text(), nullable=True),
        sa.Column("audio_url", sa.Text(), nullable=True),
        sa.Column("stream_url", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("tg_audio_file_id", sa.String(length=256), nullable=True),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'success'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "suno_task_id", name="uq_songs_suno_task_id"
        ),
    )
    op.create_index("ix_songs_chat_id", "songs", ["chat_id"])
    op.create_index("ix_songs_requested_by", "songs", ["requested_by"])
    op.create_index("ix_songs_created_at", "songs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_songs_created_at", table_name="songs")
    op.drop_index("ix_songs_requested_by", table_name="songs")
    op.drop_index("ix_songs_chat_id", table_name="songs")
    op.drop_table("songs")

    op.drop_index("ix_chat_messages_chat_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_chat_id", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_column("chats", "song_style")
