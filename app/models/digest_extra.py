"""Weekly digest phase 2: the Wikidata lookup cache and weekly-question answers."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BookWikidata(Base):
    """What Wikidata knows about a (title, author), looked up once."""

    __tablename__ = "book_wikidata"

    key: Mapped[str] = mapped_column(String(520), primary_key=True)   # "normalized title|normalized author"
    found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qid: Mapped[str | None] = mapped_column(String(20))
    wiki_title: Mapped[str | None] = mapped_column(String(300))
    pub_date: Mapped[date | None] = mapped_column(Date)
    pub_precision: Mapped[int | None] = mapped_column(SmallInteger)   # Wikidata: 11 day, 10 month, 9 year
    author_name: Mapped[str | None] = mapped_column(String(200))
    author_born: Mapped[date | None] = mapped_column(Date)
    author_died: Mapped[date | None] = mapped_column(Date)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionAnswer(Base):
    """A reader's answer to one week's question. Never shown with who wrote it."""

    __tablename__ = "question_answers"
    __table_args__ = (
        UniqueConstraint("user_id", "period", name="uq_answer_week"),
        CheckConstraint("status IN ('none', 'pending', 'approved', 'rejected')", name="ck_answer_status"),
        CheckConstraint("book_title IS NOT NULL OR note IS NOT NULL", name="ck_answer_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(40), nullable=False)
    book_title: Mapped[str | None] = mapped_column(String(300))
    book_author: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(280))
    # Others may see it, anonymously. Off unless the reader ticks it.
    share: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false", default=False)
    # The written note's review: 'none' (no note, or not shared), 'pending', 'approved', 'rejected'.
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="none", default="none")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
