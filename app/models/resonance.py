"""Resonance: two readers who felt the same thing about the same book.

The match is a *pair* row, not a per-user row — one row serves both readers, so
there is exactly one place where the state of a connection lives and no way for
the two sides to disagree about it. The pair is canonically ordered
(``user_a < user_b``) so a match can only ever be created once per book.

Identity is deliberately absent from everything the match surfaces until both
sides have said yes. The row holds both user ids because it must; the read paths
(``resonance_service.match_view``) never emit them before ``connected``.

Nothing here is counted in public. There is no "N readers matched this book"
query, and adding one would be a blueprint violation, not a feature.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# suggested → the batch job found it, nobody has acted
# pending   → one side reached out; their note is sealed until the other answers
# connected → both sides said yes; identity is revealed and a thread exists
# declined  → either side said no (or withdrew); never re-suggested for this pair
MATCH_STATUSES = ("suggested", "pending", "connected", "declined")

# strong = a shared emotion felt at a similar intensity; light = merely shared.
STRENGTH_STRONG = "strong"
STRENGTH_LIGHT = "light"

THREAD_STATUSES = ("open", "closed")


class ResonanceMatch(Base):
    __tablename__ = "resonance_matches"
    __table_args__ = (
        UniqueConstraint("user_a", "user_b", "book_id", name="uq_resonance_pair_book"),
        # Both parties read their own inbox off these.
        Index("ix_resonance_user_a_status", "user_a", "status"),
        Index("ix_resonance_user_b_status", "user_b", "status"),
        CheckConstraint("user_a < user_b", name="check_resonance_pair_order"),
        CheckConstraint(
            "status IN ('suggested','pending','connected','declined')",
            name="check_resonance_status",
        ),
        CheckConstraint("strength IN ('strong','light')", name="check_resonance_strength"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Canonically ordered pair — see check_resonance_pair_order. Use
    # resonance_service.ordered_pair() to build these, never raw assignment.
    user_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # [{emotion_id, strength_a, strength_b, close}] — strengths are named for the
    # *canonical* sides, so a reader's own number is picked by which side they are.
    shared_emotions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    strength: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="suggested")

    # Who reached first, and the note they left. The note is sealed: it is never
    # served to the other side until the match is connected.
    initiator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    initiator_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    responder_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    declined_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # First time this match was actually shown to anyone. Keeps the surfaced set
    # stable: a reader's three suggestions don't reshuffle under them each load.
    surfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    thread: Mapped["ResonanceThread | None"] = relationship(
        "ResonanceThread", back_populates="match", uselist=False, cascade="all, delete-orphan"
    )

    def other_id(self, viewer_id: uuid.UUID) -> uuid.UUID:
        return self.user_b if viewer_id == self.user_a else self.user_a

    def involves(self, user_id: uuid.UUID) -> bool:
        return user_id in (self.user_a, self.user_b)


class ResonanceThread(Base):
    """A private two-person thread. Exists only once a match is connected."""

    __tablename__ = "resonance_threads"
    __table_args__ = (
        UniqueConstraint("match_id", name="uq_thread_match"),
        CheckConstraint("status IN ('open','closed')", name="check_thread_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resonance_matches.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    match: Mapped["ResonanceMatch"] = relationship("ResonanceMatch", back_populates="thread")


class ResonanceMessage(Base):
    """Free text. No topic anchor, no moderation gate beyond report/block — once
    two people have both said yes, this is their conversation, not ours."""

    __tablename__ = "resonance_messages"
    __table_args__ = (Index("ix_resonance_messages_thread", "thread_id", "created_at", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resonance_threads.id", ondelete="CASCADE"), nullable=False
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
