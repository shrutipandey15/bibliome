"""Echo — the single public surface (blueprint Feature 1, Phase 3).

An Echo is always anchored to a book and/or a canonical emotion; there is no
freeform posting. That structural anchoring is the anti-toxicity mechanism and
keeps moderation tractable (context is always known).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# active = visible; held = auto-throttled / awaiting review; removed = taken down.
ECHO_STATUSES = ("active", "held", "removed")
ECHO_VISIBILITIES = ("community", "public")
REACTION_KINDS = ("felt_this", "changed_my_mind", "adding_to_list")


class Echo(Base):
    __tablename__ = "echoes"
    __table_args__ = (
        Index("ix_echoes_created", "created_at"),
        Index("ix_echoes_emotion_created", "primary_emotion", "created_at"),
        Index("ix_echoes_book", "book_key"),
        Index("ix_echoes_author", "author_id"),
        Index("ix_echoes_prompt", "prompt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Book anchor: denormalized title/author (+ a normalized key for grouping the
    # "A Book" feed) and an optional catalog id. At least one anchor (book or
    # emotion) is required, enforced in the service.
    book_key: Mapped[str | None] = mapped_column(String(400), nullable=True)
    book_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    book_author: Mapped[str | None] = mapped_column(String(200), nullable=True)

    primary_emotion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    secondary_emotion: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Optional campfire anchor (B6.5): the weekly Prompt this echo answers, so the
    # feed can group answers to the same question. SET NULL if the prompt is removed.
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True
    )

    body: Mapped[str] = mapped_column(String(500), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, server_default="community")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    author: Mapped["User"] = relationship("User")
    replies: Mapped[list["EchoReply"]] = relationship(
        "EchoReply", back_populates="echo", cascade="all, delete-orphan"
    )
    reactions: Mapped[list["EchoReaction"]] = relationship(
        "EchoReaction", back_populates="echo", cascade="all, delete-orphan"
    )


class EchoReply(Base):
    __tablename__ = "echo_replies"
    __table_args__ = (Index("ix_echo_replies_echo_created", "echo_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    echo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("echoes.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    echo: Mapped["Echo"] = relationship("Echo", back_populates="replies")
    author: Mapped["User"] = relationship("User")


class EchoReaction(Base):
    """Private reactions: the author sees an aggregate, the public sees nothing."""

    __tablename__ = "echo_reactions"
    __table_args__ = (
        UniqueConstraint("echo_id", "user_id", "kind", name="uq_echo_reaction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    echo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("echoes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    echo: Mapped["Echo"] = relationship("Echo", back_populates="reactions")
