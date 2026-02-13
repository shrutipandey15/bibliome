from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

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
    current_user: User = Depends(get_current_user),
):
    """
    Search for books by title or author.
    Returns title, author, cover URL, ISBN.
    Used by the frontend for autocomplete when adding entries.
    """
    results = await search_books(q)
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