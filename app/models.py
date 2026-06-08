from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
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
    # Where the user actually typed the idea — used so admin replies can
    # be sent back to that very chat as a Telegram-reply to the original
    # message. For DM submissions this is the user's private chat with
    # the bot; for in-chat replies to the bot's prompt it's the group.
    from_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    from_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
