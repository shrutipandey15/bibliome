"""Per-book emotional aggregate — "for readers in general, this book does X" (B8.2).

One row per canonical book, rebuilt incrementally as readers tag it. This is the
table the deviation engine and the recommender read from.

Every row carries a ``confidence`` tier, and that is a product feature rather
than a caveat: a book tagged by one reader is one opinion, and saying otherwise
is the Barnum failure at the book level (B8.4).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 0 real readers — an LLM/seed prior, never reader data (B8.5).
CONFIDENCE_PREDICTED = "predicted"
CONFIDENCE_EMERGING = "emerging"
CONFIDENCE_CONFIRMED = "confirmed"
CONFIDENCE_TIERS = (CONFIDENCE_PREDICTED, CONFIDENCE_EMERGING, CONFIDENCE_CONFIRMED)

# Where the row came from, kept separate from how much it should be trusted.
# An ``llm`` row is disposable scaffolding: the first real reader overwrites it.
SOURCE_READERS = "readers"
SOURCE_LLM = "llm"
SOURCES = (SOURCE_READERS, SOURCE_LLM)


class BookEmotionAggregate(Base):
    __tablename__ = "book_emotion_aggregates"

    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )

    # Distinct readers, not entries: one reader logging a re-read must not look
    # like two independent confirmations.
    reader_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # { slug: {mean_strength, count, tagged_by_fraction} }. Both halves matter —
    # "80% of readers felt devastation at avg 8" is a different claim from
    # "one reader felt it at 10".
    emotion_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # { yes, no, not_sure } as fractions of readers who gave a verdict.
    verdict_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    dnf_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    confidence: Mapped[str] = mapped_column(String(20), nullable=False, default=CONFIDENCE_EMERGING)

    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_READERS)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IN ('predicted','emerging','confirmed')",
            name="check_aggregate_confidence",
        ),
        CheckConstraint(
            "source IN ('readers','llm')",
            name="check_aggregate_source",
        ),
        CheckConstraint("reader_count >= 0", name="check_aggregate_reader_count"),
        CheckConstraint("dnf_rate >= 0 AND dnf_rate <= 1", name="check_aggregate_dnf_rate"),
    )

    book: Mapped["Book"] = relationship("Book")
