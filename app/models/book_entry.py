import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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
    public_echo: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[date | None] = mapped_column(Date)
    finished_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="finished")
    arc_start_emotion_id: Mapped[str | None] = mapped_column(String(30))
    arc_middle_emotion_id: Mapped[str | None] = mapped_column(String(30))
    arc_end_emotion_id: Mapped[str | None] = mapped_column(String(30))
    finish_thought: Mapped[str | None] = mapped_column(String(120))
    shelf_position: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("intensity >= 1 AND intensity <= 10", name="check_intensity_range"),
        CheckConstraint(
            "status IN ('want_to_read','reading','finished')",
            name="check_entry_status",
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
    )

    # Relationships
    entry: Mapped["BookEntry"] = relationship("BookEntry", back_populates="emotions")