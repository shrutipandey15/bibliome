"""
Book Catalog — canonical book records that grow as users search and add books.
Used as the primary search source; external APIs are fallback for unknown books.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        UniqueConstraint("title_normalized", "author_normalized", name="uq_books_title_author"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Display fields
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    published_year: Mapped[str | None] = mapped_column(String(4))
    description: Mapped[str | None] = mapped_column(Text)

    # Normalized fields for fast search (lowercase, stripped punctuation).
    # author_normalized is NOT NULL ("" when unknown) so the unique constraint
    # below actually dedupes author-less books (NULLs would compare distinct).
    title_normalized: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    author_normalized: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")

    # Identifiers
    isbn_13: Mapped[str | None] = mapped_column(String(13), unique=True, index=True)
    isbn_10: Mapped[str | None] = mapped_column(String(10), unique=True, index=True)

    # Metadata
    source: Mapped[str] = mapped_column(String(20), default="google")  # google, openlibrary, user
    popularity: Mapped[int] = mapped_column(Integer, default=0)  # how many users added this
    cover_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )