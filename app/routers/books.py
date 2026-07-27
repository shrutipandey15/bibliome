import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.book import Book
from app.models.book_aggregate import BookEmotionAggregate
from app.models.book_entry import BookEntry
from app.models.user import User
from app.schemas.aggregate import BookProfileResponse, BookProfileWithheld
from app.services.book_search import search_books
from app.utils.cache import book_search_cache

router = APIRouter(prefix="/books", tags=["books"])

search_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="book_search")


class BookSearchResult(BaseModel):
    title: str
    author: str | None
    cover_url: str | None
    isbn: str | None
    published_year: str | None
    description: str | None


class BookSearchResponse(BaseModel):
    results: list[BookSearchResult]
    query: str
    cached: bool = False


@router.get("/search", response_model=BookSearchResponse)
async def search(
    q: str = Query(min_length=2, max_length=200),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Smart book search: local catalog first, then external APIs.
    Results are merged, deduplicated, and ranked by relevance.
    Cached for 5 minutes per unique query.
    """
    await search_limiter.check(request)
    cache_key = q.lower().strip()
    cached = await book_search_cache.get(cache_key)
    if cached is not None:
        return BookSearchResponse(results=cached, query=q, cached=True)

    results = await search_books(q, db=db)
    serialized = [
        BookSearchResult(
            title=r.title,
            author=r.author,
            cover_url=r.cover_url,
            isbn=r.isbn,
            published_year=r.published_year,
            description=r.description,
        )
        for r in results
    ]
    await book_search_cache.set(cache_key, serialized)

    return BookSearchResponse(results=serialized, query=q, cached=False)

CONFIDENCE_LABELS = {
    "predicted": "predicted · no reader has confirmed this",
    "emerging": "early readings",
    "confirmed": "confirmed by readers",
}


@router.get(
    "/{book_id}/profile",
    response_model=BookProfileResponse | BookProfileWithheld,
)
async def book_profile(
    book_id: uuid.UUID,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What this book does to readers in general (B8.6).

    Aggregate only — this never exposes which reader tagged what. Below the
    configured reader floor the profile is withheld entirely rather than
    coarsened, because with one or two readers there is nothing to coarsen: the
    "aggregate" would just be their own tagging.

    The reader's own shelf is the one exception — you may always see the profile
    of a book you have read, since your own data is most of what's in it.
    """
    await search_limiter.check(request)

    book = (await db.execute(select(Book).where(Book.id == book_id))).scalars().first()
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found")

    agg = await db.get(BookEmotionAggregate, book_id)
    settings = get_settings()
    floor = settings.AGGREGATE_PUBLIC_MIN_READERS

    if agg is None or agg.reader_count < settings.AGGREGATE_EMERGING_MIN_READERS:
        return BookProfileWithheld(
            book_id=book.id, title=book.title, author=book.author,
            reason="No reader has tagged this book yet.",
            readers_needed=settings.AGGREGATE_EMERGING_MIN_READERS,
        )

    if agg.reader_count < floor:
        has_read_it = (await db.execute(
            select(BookEntry.id).where(
                BookEntry.book_id == book_id, BookEntry.user_id == current_user.id
            ).limit(1)
        )).scalars().first()
        if not has_read_it:
            return BookProfileWithheld(
                book_id=book.id, title=book.title, author=book.author,
                reason=(
                    "Too few readers have tagged this book to describe it without "
                    "identifying them. Be one of the first to confirm it."
                ),
                readers_needed=floor - agg.reader_count,
            )

    return BookProfileResponse(
        book_id=book.id,
        title=book.title,
        author=book.author,
        reader_count=agg.reader_count,
        emotion_profile=agg.emotion_profile,
        verdict_profile=agg.verdict_profile,
        dnf_rate=agg.dnf_rate,
        confidence=agg.confidence,
        confidence_label=CONFIDENCE_LABELS.get(agg.confidence, agg.confidence),
        source=agg.source,
        updated_at=agg.updated_at,
    )
