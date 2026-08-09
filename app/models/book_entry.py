import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BookEntry(Base):
    __tablename__ = "book_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    isbn: Mapped[str | None] = mapped_column(String(13))
    intensity: Mapped[int] = mapped_column(
        Integer, default=5
    )
    quote: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[date | None] = mapped_column(Date)
    finished_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Canonical book identity (B8.1). Nullable: an entry whose title resolves to
    # nothing still belongs on the shelf, it just cannot feed the aggregate.
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="finished")
    # "Would you read it again?" — yes | no | not_sure (nullable).
    verdict: Mapped[str | None] = mapped_column(String(10))
    # Why a book was abandoned — only set when status == 'abandoned' (nullable).
    dnf_reason: Mapped[str | None] = mapped_column(String(20))
    arc_start_emotion_id: Mapped[str | None] = mapped_column(String(30))
    arc_middle_emotion_id: Mapped[str | None] = mapped_column(String(30))
    arc_end_emotion_id: Mapped[str | None] = mapped_column(String(30))
    finish_thought: Mapped[str | None] = mapped_column(String(120))
    shelf_position: Mapped[int | None] = mapped_column(Integer)
    # How far in, 0–100. NULL means "hasn't said", which is not the same as 0% and
    # must not render as a bar at zero. Only meaningful while a book is open.
    progress: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("intensity >= 1 AND intensity <= 10", name="check_intensity_range"),
        CheckConstraint(
            "status IN ('want_to_read','reading','finished','abandoned','paused','reread')",
            name="check_entry_status",
        ),
        CheckConstraint(
            "verdict IS NULL OR verdict IN ('yes','no','not_sure')",
            name="check_entry_verdict",
        ),
        CheckConstraint(
            "dnf_reason IS NULL OR dnf_reason IN "
            "('bored','too_much','badly_written','wrong_time','lost_me','drifted')",
            name="check_entry_dnf_reason",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="ck_book_entries_progress_range",
        ),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="entries")
    emotions: Mapped[list["EntryEmotion"]] = relationship(
        "EntryEmotion", back_populates="entry", cascade="all, delete-orphan"
    )
    checkins: Mapped[list["EntryCheckin"]] = relationship(
        "EntryCheckin", back_populates="entry", cascade="all, delete-orphan"
    )


class EntryEmotion(Base):
    __tablename__ = "entry_emotions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("book_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    emotion_id: Mapped[str] = mapped_column(String(30), nullable=False)
    strength: Mapped[int] = mapped_column(Integer, default=5)

    __table_args__ = (
        UniqueConstraint("entry_id", "emotion_id", name="uq_entry_emotion"),
        CheckConstraint("strength >= 1 AND strength <= 10", name="check_strength_range"),
        # Resonance matches readers by joining this column across users.
        Index("ix_entry_emotions_emotion_id", "emotion_id"),
    )

    # Relationships
    entry: Mapped["BookEntry"] = relationship("BookEntry", back_populates="emotions")