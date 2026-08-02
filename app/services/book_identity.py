"""Book identity: resolving a free-text entry to a canonical ``books`` row (B8.1).

Entries have always stored ``title``/``author`` as loose strings, so two readers
logging the same book produced two unconnected rows. Nothing per-book can be
aggregated without a shared identity, so every entry now carries ``book_id``.

The resolution order is deliberately strict-to-loose:

    1. ISBN (exact, reliable)
    2. (title_normalized, author_normalized) — the catalog's own unique key
    3. create a new ``Book`` with source="user"

There is intentionally NO fuzzy matching here. Fuzzy is right for *search*, where
a human picks from a ranked list; it is wrong for *identity*, where a wrong merge
silently fuses two books' emotional profiles and there is no human in the loop.
The catalog proves the danger: "Powerless" is three different books by Lauren
Roberts, Matthew Cody and Elsie Silver. Title-only merging would destroy them.

Near-duplicates that survive this (author spelled "George Eliot" vs "George
Elliot", or credited to a film producer) stay separate. That is the honest
failure mode — a fractured aggregate under-counts, a wrongly merged one lies.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.services.book_search import normalize

logger = logging.getLogger("bibliome.identity")


def _clean_isbn(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().replace("-", "").replace(" ", "")
    return s if len(s) in (10, 13) and s.isalnum() else None


async def find_book(
    db: AsyncSession,
    title: str,
    author: str | None = None,
    isbn: str | None = None,
) -> Book | None:
    """Find the canonical book for these details, without creating one."""
    isbn = _clean_isbn(isbn)
    if isbn:
        column = Book.isbn_13 if len(isbn) == 13 else Book.isbn_10
        found = (await db.execute(select(Book).where(column == isbn))).scalars().first()
        if found:
            return found

    if not title or not title.strip():
        return None

    return (await db.execute(
        select(Book).where(
            Book.title_normalized == normalize(title),
            Book.author_normalized == normalize(author or ""),
        )
    )).scalars().first()


async def resolve_book(
    db: AsyncSession,
    title: str,
    author: str | None = None,
    isbn: str | None = None,
    cover_url: str | None = None,
) -> Book | None:
    """Find-or-create the canonical book. Returns None only for a blank title.

    Safe against a concurrent writer creating the same row first: the insert
    upserts on the catalog's unique key and we re-read (P4-6, same discipline as
    ``bump_popularity``).
    """
    if not title or not title.strip():
        return None

    existing = await find_book(db, title, author, isbn)
    if existing:
        # Opportunistically enrich a sparse row while we're here.
        isbn_clean = _clean_isbn(isbn)
        if cover_url and not existing.cover_url:
            existing.cover_url = cover_url
        if isbn_clean and len(isbn_clean) == 13 and not existing.isbn_13:
            if not await _isbn_taken(db, Book.isbn_13, isbn_clean, existing.id):
                existing.isbn_13 = isbn_clean
        if isbn_clean and len(isbn_clean) == 10 and not existing.isbn_10:
            if not await _isbn_taken(db, Book.isbn_10, isbn_clean, existing.id):
                existing.isbn_10 = isbn_clean
        return existing

    title_norm = normalize(title)
    author_norm = normalize(author or "")
    isbn_clean = _clean_isbn(isbn)

    await db.execute(
        pg_insert(Book)
        .values(
            title=title[:300],
            author=(author or None) and author[:200],
            cover_url=cover_url,
            title_normalized=title_norm,
            author_normalized=author_norm,
            isbn_13=isbn_clean if isbn_clean and len(isbn_clean) == 13 else None,
            isbn_10=isbn_clean if isbn_clean and len(isbn_clean) == 10 else None,
            source="user",
            popularity=0,
        )
        .on_conflict_do_nothing(index_elements=["title_normalized", "author_normalized"])
    )
    await db.flush()

    return (await db.execute(
        select(Book).where(
            Book.title_normalized == title_norm,
            Book.author_normalized == author_norm,
        )
    )).scalars().first()


async def _isbn_taken(db: AsyncSession, column, value: str, self_id: uuid.UUID) -> bool:
    """books.isbn_13/isbn_10 are UNIQUE — never claim one another row owns."""
    return (await db.execute(
        select(Book.id).where(column == value, Book.id != self_id)
    )).scalars().first() is not None
