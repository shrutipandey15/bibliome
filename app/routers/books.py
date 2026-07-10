from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.services.book_search import search_books
from app.utils.cache import search_cache

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
    cache_key = f"search:{q.lower().strip()}"
    cached = search_cache.get(cache_key)
    if cached:
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
    search_cache.set(cache_key, serialized)

    return BookSearchResponse(results=serialized, query=q, cached=False)