"""Catalog write / dedupe tests (B2.8 / P4-6)."""

import pytest
from sqlalchemy import func, select

from app.models.book import Book
from app.services.book_search import bump_popularity

pytestmark = pytest.mark.asyncio


async def test_bump_popularity_dedupes_by_normalized_title_author(db):
    await bump_popularity(db, "The Road", "Cormac McCarthy")
    # Same book with different casing/punctuation normalizes to the same row.
    await bump_popularity(db, "the road!!", "cormac  mccarthy")
    await db.commit()

    count = (await db.execute(select(func.count(Book.id)))).scalar()
    assert count == 1
    book = (await db.execute(select(Book))).scalar_one()
    assert book.popularity == 2


async def test_authorless_books_dedupe(db):
    # NULL/empty authors must still dedupe (author_normalized == "").
    await bump_popularity(db, "Untitled", None)
    await bump_popularity(db, "untitled", None)
    await db.commit()
    count = (await db.execute(select(func.count(Book.id)))).scalar()
    assert count == 1


async def test_different_books_are_separate_rows(db):
    await bump_popularity(db, "Book One", "Author A")
    await bump_popularity(db, "Book Two", "Author A")
    await db.commit()
    count = (await db.execute(select(func.count(Book.id)))).scalar()
    assert count == 2
