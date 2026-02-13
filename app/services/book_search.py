"""
Book Search Service — queries Google Books API.
Free, no key required (rate limited to ~1000/day).
"""

import urllib.parse
from dataclasses import dataclass

import httpx


@dataclass
class BookResult:
    title: str
    author: str | None
    cover_url: str | None
    isbn: str | None
    published_year: str | None
    description: str | None


async def search_books(query: str, max_results: int = 8) -> list[BookResult]:
    """
    Search Google Books API by title/author query.
    Returns cleaned, deduplicated results.
    """
    encoded = urllib.parse.quote(query)
    url = f"https://www.googleapis.com/books/v1/volumes?q={encoded}&maxResults={max_results}&printType=books&orderBy=relevance"

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            res = await client.get(url)
            res.raise_for_status()
            data = res.json()
        except Exception:
            return []

    items = data.get("items", [])
    results = []
    seen_titles = set()

    for item in items:
        info = item.get("volumeInfo", {})
        title = info.get("title", "").strip()
        if not title:
            continue

        # Deduplicate by lowercase title
        title_key = title.lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        # Get best author
        authors = info.get("authors", [])
        author = authors[0] if authors else None

        # Get cover — prefer larger images
        images = info.get("imageLinks", {})
        cover_url = (
            images.get("thumbnail")
            or images.get("smallThumbnail")
        )
        # Upgrade to https and larger size
        if cover_url:
            cover_url = cover_url.replace("http://", "https://")
            cover_url = cover_url.replace("zoom=1", "zoom=2")

        # Get ISBN (prefer ISBN_13)
        isbn = None
        for identifier in info.get("industryIdentifiers", []):
            if identifier.get("type") == "ISBN_13":
                isbn = identifier.get("identifier")
                break
            elif identifier.get("type") == "ISBN_10" and not isbn:
                isbn = identifier.get("identifier")

        # Get year
        published = info.get("publishedDate", "")
        year = published[:4] if len(published) >= 4 else None

        # Get short description
        desc = info.get("description", "")
        if len(desc) > 200:
            desc = desc[:197] + "..."

        results.append(BookResult(
            title=title,
            author=author,
            cover_url=cover_url,
            isbn=isbn,
            published_year=year,
            description=desc or None,
        ))

    return results