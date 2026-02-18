from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services.book_search import search_books

router = APIRouter(prefix="/books", tags=["books"])


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


@router.get("/search", response_model=BookSearchResponse)
async def search(
    q: str = Query(min_length=2, max_length=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Smart book search: local catalog first, then external APIs.
    Results are merged, deduplicated, and ranked by relevance.
    """
    results = await search_books(q, db=db)
    return BookSearchResponse(
        results=[
            BookSearchResult(
                title=r.title,
                author=r.author,
                cover_url=r.cover_url,
                isbn=r.isbn,
                published_year=r.published_year,
                description=r.description,
            )
            for r in results
        ],
        query=q,
    )