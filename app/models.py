from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Admin(Base):
    """Telegram users allowed to manage the bot and receive ideas."""

    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    receive_ideas: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delivery_mode: Mapped[str] = mapped_column(
        String(16), default="stream", nullable=False
    )
    digest_cron: Mapped[str] = mapped_column(
        String(64), default="0 9 * * 1", nullable=False
    )
    last_digest_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chat(Base):
    """Telegram chat where the bot collects ideas."""

    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_prompt_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    auto_publish: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    song_style: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ideas: Mapped[list["Idea"]] = relationship(back_populates="chat")


class Idea(Base):
    """Idea submitted by a chat participant."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.chat_id", ondelete="SET NULL"), nullable=True
    )
    from_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_anonymous: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="new", nullable=False)
    tag: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    published_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chat: Mapped[Chat | None] = relationship(back_populates="ideas")
    votes: Mapped[list["IdeaVote"]] = relationship(
        back_populates="idea", cascade="all, delete-orphan"
    )


class IdeaVote(Base):
    """A single up/down vote by a chat participant on a published idea."""

    __tablename__ = "idea_votes"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    idea: Mapped[Idea] = relationship(back_populates="votes")


class Setting(Base):
    """Global key-value bot settings (defaults, timezone, etc)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChatMessage(Base):
    """Captured text message from a watched group chat.

    Used by the daily-song pipeline to summarize the day's discussion.
    Records older than 2 days are pruned hourly by the scheduler
    (see ``app/scheduler.py::IdeaScheduler._run_retention``).

    Capture happens in ``app/middlewares/capture.py`` for every text
    message in a registered, non-paused group chat (excluding bots,
    commands, and non-text messages).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "tg_message_id", name="uq_chat_messages_chat_tgmsg"
        ),
        Index(
            "ix_chat_messages_chat_created", "chat_id", "created_at"
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    chat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chats.chat_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class Song(Base):
    """One generated song (sunoapi.org) record.

    Songs are kept indefinitely — no auto-deletion. Suno's own mp3
    URLs expire after 15 days, so on first ``send_audio`` we capture
    Telegram's ``file_id`` (``tg_audio_file_id``); after that we can
    re-deliver the audio forever without re-fetching from Suno.

    ``chat_id`` is nullable because tests done by an admin via /suno in
    DM aren't tied to any group chat. ``requested_by`` is the user_id
    who triggered the generation (admin for test-gen, scheduler for
    the daily-song flow when it lands).
    """

    __tablename__ = "songs"
    __table_args__ = (
        UniqueConstraint("suno_task_id", name="uq_songs_suno_task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    chat_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chats.chat_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    suno_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    suno_audio_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    style: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    lyrics: Mapped[str | None] = mapped_column(Text, nullable=True)

    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    tg_audio_file_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )

    requested_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="success", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
