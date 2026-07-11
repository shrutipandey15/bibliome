"""
Smart Book Search Engine
========================

Three-layer architecture:
  1. LOCAL CATALOG  (< 5ms)  — books table with trigram fuzzy search
  2. EXTERNAL APIs  (~300ms) — Google Books + Open Library in parallel
  3. MERGE + RANK            — deduplicate, score, sort, return

The catalog grows automatically:
  - Search results from external APIs get stored
  - When users add books, popularity counter increments
  - Covers are verified in background and cached

The more people use the app, the faster search gets.
"""

import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from time import monotonic

import httpx
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("bookdna.search")

@dataclass
class BookResult:
    title: str
    author: str | None
    cover_url: str | None
    isbn: str | None
    published_year: str | None
    description: str | None
    _score: float = field(default=0.0, repr=False)
    _source: str = field(default="", repr=False)
    _isbn_10: str | None = field(default=None, repr=False)

_NOISE_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace runs to one space.

    Collapsing whitespace matters for catalog dedupe (P4-6): "cormac  mccarthy"
    and "cormac mccarthy" must map to the same normalized key.
    """
    cleaned = _NOISE_RE.sub("", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _title_similarity(a: str, b: str) -> float:
    """0.0-1.0 similarity between two strings."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _is_same_book(a: BookResult, b: BookResult) -> bool:
    """Check if two results represent the same book."""
    # ISBN match is definitive
    if a.isbn and b.isbn:
        if a.isbn == b.isbn:
            return True
        # Also check isbn_10 cross-match
        if a._isbn_10 and a._isbn_10 == b._isbn_10:
            return True
    # Fuzzy title + author
    title_sim = _title_similarity(a.title, b.title)
    if title_sim < 0.75:
        return False
    if a.author and b.author:
        return _title_similarity(a.author, b.author) > 0.6
    return title_sim > 0.85

def _score_result(result: BookResult, query: str, popularity: int = 0) -> float:
    """
    Score a search result for ranking. Higher = better.

    Weights:
      Exact title match     +100
      Starts with query      +50
      Contains query          +30
      Per overlapping word    +10
      Sequence similarity     +20 (scaled 0-1)
      Has cover               +15
      Has ISBN                 +5
      Has description          +3
      Popularity              +2 per user (capped at 20)
      Local source            +10
    """
    score = 0.0
    q = normalize(query)
    t = normalize(result.title)
    q_words = set(q.split())
    t_words = set(t.split())

    # Title matching
    if t == q:
        score += 100
    elif t.startswith(q):
        score += 50
    elif q in t:
        score += 30

    # Word overlap
    score += len(q_words & t_words) * 10

    # Sequence similarity
    score += _title_similarity(result.title, query) * 20

    # Metadata completeness
    if result.cover_url:
        score += 15
    if result.isbn:
        score += 5
    if result.description:
        score += 3

    # Popularity (capped)
    score += min(popularity, 10) * 2

    # Source bonus
    if result._source == "local":
        score += 10

    return score

async def _search_local(db: AsyncSession, query: str, limit: int = 5) -> list[BookResult]:
    """
    Search the books catalog using PostgreSQL trigram similarity.
    Returns instantly from local database — no network calls.
    """
    from app.models.book import Book

    q_normalized = normalize(query)
    if not q_normalized:
        return []

    # Use trigram similarity for fuzzy matching
    # similarity() returns 0.0-1.0, we want > 0.2 for broad matching
    stmt = (
        select(Book)
        .where(
            or_(
                func.similarity(Book.title_normalized, q_normalized) > 0.2,
                Book.title_normalized.ilike(f"%{q_normalized}%"),
            )
        )
        .order_by(
            func.similarity(Book.title_normalized, q_normalized).desc(),
            Book.popularity.desc(),
        )
        .limit(limit)
    )

    try:
        result = await db.execute(stmt)
        rows = result.scalars().all()
    except Exception as e:
        logger.warning("Local search failed (pg_trgm may not be installed): %s", e)
        # Fallback: simple ILIKE search without trigram
        try:
            fallback_stmt = (
                select(Book)
                .where(Book.title_normalized.ilike(f"%{q_normalized}%"))
                .order_by(Book.popularity.desc())
                .limit(limit)
            )
            result = await db.execute(fallback_stmt)
            rows = result.scalars().all()
        except Exception:
            return []

    return [
        BookResult(
            title=row.title,
            author=row.author,
            cover_url=row.cover_url,
            isbn=row.isbn_13 or row.isbn_10,
            published_year=row.published_year,
            description=row.description,
            _source="local",
            _isbn_10=row.isbn_10,
        )
        for row in rows
    ]

async def _search_google(client: httpx.AsyncClient, query: str, limit: int = 8) -> list[BookResult]:
    """Search Google Books with intitle: prefix for better relevance."""
    from app.config import get_settings

    encoded = urllib.parse.quote(f"intitle:{query}")
    url = (
        f"https://www.googleapis.com/books/v1/volumes"
        f"?q={encoded}&maxResults={limit}&printType=books&orderBy=relevance"
    )
    api_key = get_settings().GOOGLE_BOOKS_API_KEY
    if api_key:
        url += f"&key={urllib.parse.quote(api_key)}"

    try:
        res = await client.get(url, timeout=5.0)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        logger.warning("Google Books failed: %s", e)
        return []

    results = []
    seen = set()

    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        title = info.get("title", "").strip()
        if not title:
            continue

        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        authors = info.get("authors", [])
        author = authors[0] if authors else None

        images = info.get("imageLinks", {})
        cover_url = images.get("thumbnail") or images.get("smallThumbnail")
        if cover_url:
            cover_url = cover_url.replace("http://", "https://").replace("zoom=1", "zoom=2")

        isbn_13 = isbn_10 = None
        for ident in info.get("industryIdentifiers", []):
            id_type = ident.get("type")
            id_val = ident.get("identifier")
            if id_type == "ISBN_13":
                isbn_13 = id_val
            elif id_type == "ISBN_10":
                isbn_10 = id_val

        published = info.get("publishedDate", "")
        year = published[:4] if len(published) >= 4 else None

        desc = info.get("description", "")
        if len(desc) > 200:
            desc = desc[:197] + "..."

        results.append(BookResult(
            title=title,
            author=author,
            cover_url=cover_url,
            isbn=isbn_13 or isbn_10,
            published_year=year,
            description=desc or None,
            _source="google",
            _isbn_10=isbn_10,
        ))

    return results

async def _search_openlibrary(client: httpx.AsyncClient, query: str, limit: int = 5) -> list[BookResult]:
    """Search Open Library. Good for ISBNs, older books, and backup covers."""
    encoded = urllib.parse.quote(query)
    url = (
        f"https://openlibrary.org/search.json"
        f"?q={encoded}&limit={limit}"
        f"&fields=title,author_name,isbn,first_publish_year,cover_i,key"
    )

    try:
        res = await client.get(url, timeout=5.0)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        logger.warning("Open Library failed: %s", e)
        return []

    results = []
    for doc in data.get("docs", []):
        title = doc.get("title", "").strip()
        if not title:
            continue

        authors = doc.get("author_name", [])
        author = authors[0] if authors else None

        # Cover: prefer cover_i (their internal ID — reliable)
        cover_url = None
        cover_i = doc.get("cover_i")
        if cover_i:
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-M.jpg"

        # ISBNs
        isbns = doc.get("isbn", [])
        isbn_13 = isbn_10 = None
        for i in isbns:
            if len(i) == 13 and not isbn_13:
                isbn_13 = i
            elif len(i) == 10 and not isbn_10:
                isbn_10 = i

        # If no cover from cover_i, try ISBN-based cover
        if not cover_url and isbn_13:
            cover_url = f"https://covers.openlibrary.org/b/isbn/{isbn_13}-M.jpg"
        elif not cover_url and isbn_10:
            cover_url = f"https://covers.openlibrary.org/b/isbn/{isbn_10}-M.jpg"

        year = doc.get("first_publish_year")

        results.append(BookResult(
            title=title,
            author=author,
            cover_url=cover_url,
            isbn=isbn_13 or isbn_10,
            published_year=str(year) if year else None,
            description=None,  # OL search doesn't return descriptions
            _source="openlibrary",
            _isbn_10=isbn_10,
        ))

    return results

def _merge_and_rank(
    all_results: list[BookResult],
    query: str,
    limit: int = 8,
) -> list[BookResult]:
    """
    Deduplicate across sources, merge metadata, score, and rank.
    When the same book appears from multiple sources:
      - Keep the best cover (Google > Open Library cover_i > OL ISBN)
      - Take the most complete metadata
      - Combine ISBNs
    """
    merged: list[BookResult] = []

    for result in all_results:
        matched = False
        for existing in merged:
            if _is_same_book(existing, result):
                # Merge: fill gaps with the new result's data
                if not existing.cover_url and result.cover_url:
                    existing.cover_url = result.cover_url
                if not existing.isbn and result.isbn:
                    existing.isbn = result.isbn
                if not existing._isbn_10 and result._isbn_10:
                    existing._isbn_10 = result._isbn_10
                if not existing.description and result.description:
                    existing.description = result.description
                if not existing.author and result.author:
                    existing.author = result.author
                if not existing.published_year and result.published_year:
                    existing.published_year = result.published_year
                matched = True
                break

        if not matched:
            merged.append(result)

    # Score all results
    for r in merged:
        r._score = _score_result(r, query)

    merged.sort(key=lambda r: r._score, reverse=True)
    return merged[:limit]

async def _store_in_catalog(db: AsyncSession, results: list[BookResult]) -> None:
    """
    Store search results in the books catalog (upsert).
    New books get inserted; existing books get updated if we have better data.
    """
    from app.models.book import Book

    for r in results:
        if not r.title:
            continue

        title_norm = normalize(r.title)
        author_norm = normalize(r.author) if r.author else ""
        isbn_13 = r.isbn if r.isbn and len(r.isbn) == 13 else None
        isbn_10 = r._isbn_10 or (r.isbn if r.isbn and len(r.isbn) == 10 else None)

        # Check if book exists by ISBN or normalized title+author
        existing = None

        if isbn_13:
            result = await db.execute(select(Book).where(Book.isbn_13 == isbn_13))
            existing = result.scalar_one_or_none()

        if not existing and isbn_10:
            result = await db.execute(select(Book).where(Book.isbn_10 == isbn_10))
            existing = result.scalar_one_or_none()

        if not existing and title_norm:
            result = await db.execute(
                select(Book).where(
                    Book.title_normalized == title_norm,
                    Book.author_normalized == author_norm,
                )
            )
            existing = result.scalar_one_or_none()

        if existing:
            # Update with better data
            if not existing.cover_url and r.cover_url:
                existing.cover_url = r.cover_url
            if not existing.isbn_13 and isbn_13:
                existing.isbn_13 = isbn_13
            if not existing.isbn_10 and isbn_10:
                existing.isbn_10 = isbn_10
            if not existing.description and r.description:
                existing.description = r.description
            if not existing.published_year and r.published_year:
                existing.published_year = r.published_year
        else:
            # Insert new — ON CONFLICT DO NOTHING so a concurrent writer (the
            # request-triggered catalog feed) racing the same book can't create a
            # duplicate or blow up this flush (P4-6).
            stmt = (
                pg_insert(Book)
                .values(
                    title=r.title,
                    author=r.author,
                    cover_url=r.cover_url,
                    title_normalized=title_norm,
                    author_normalized=author_norm,
                    isbn_13=isbn_13,
                    isbn_10=isbn_10,
                    published_year=r.published_year,
                    description=r.description,
                    source=r._source or "google",
                )
                .on_conflict_do_nothing(index_elements=["title_normalized", "author_normalized"])
            )
            try:
                await db.execute(stmt)
            except Exception as e:
                logger.debug("Catalog insert skipped: %s", e)


async def bump_popularity(
    db: AsyncSession,
    title: str,
    author: str | None,
    cover_url: str | None = None,
    isbn: str | None = None,
) -> None:
    """
    Increment popularity when a user adds a book.
    If the book isn't in the catalog yet, create it.
    """
    from app.models.book import Book

    title_norm = normalize(title)
    author_norm = normalize(author) if author else ""

    result = await db.execute(
        select(Book).where(
            Book.title_normalized == title_norm,
            Book.author_normalized == author_norm,
        )
    )
    book = result.scalar_one_or_none()

    if book:
        book.popularity = (book.popularity or 0) + 1
        # Update with better data if available
        if not book.cover_url and cover_url:
            book.cover_url = cover_url
        if not book.isbn_13 and isbn and len(isbn) == 13:
            book.isbn_13 = isbn
        if not book.isbn_10 and isbn and len(isbn) == 10:
            book.isbn_10 = isbn
    else:
        # Insert, or bump popularity if a concurrent writer created it first (P4-6).
        isbn_13 = isbn if isbn and len(isbn) == 13 else None
        isbn_10 = isbn if isbn and len(isbn) == 10 else None
        stmt = (
            pg_insert(Book)
            .values(
                title=title,
                author=author,
                cover_url=cover_url,
                title_normalized=title_norm,
                author_normalized=author_norm,
                isbn_13=isbn_13,
                isbn_10=isbn_10,
                source="user",
                popularity=1,
            )
            .on_conflict_do_update(
                index_elements=["title_normalized", "author_normalized"],
                set_={"popularity": Book.popularity + 1},
            )
        )
        await db.execute(stmt)


# ═══════════════════════════════════════════
# Main Search Function
# ═══════════════════════════════════════════

async def search_books(
    query: str,
    db: AsyncSession | None = None,
    max_results: int = 8,
) -> list[BookResult]:
    """
    Smart multi-source book search.

    Flow:
      1. Search local catalog (instant)
      2. If local has 3+ good results → return immediately
      3. Otherwise → query Google + Open Library in parallel
      4. Merge all results, deduplicate, rank
      5. Store new books in catalog (background, non-blocking)
    """
    start = monotonic()
    query = query.strip()
    if not query:
        return []

    # ── Layer 1: Local catalog ──
    local_results: list[BookResult] = []
    if db:
        try:
            local_results = await _search_local(db, query)
        except Exception as e:
            logger.warning("Local search error: %s", e)

    # Check if local results are sufficient
    q_norm = normalize(query)
    good_local = [
        r for r in local_results
        if _title_similarity(r.title, query) > 0.5
    ]

    if len(good_local) >= 3:
        # Local catalog has enough — skip external APIs
        elapsed = (monotonic() - start) * 1000
        logger.info("Search '%s': %d local hits (%.0fms) — skipped APIs", query, len(good_local), elapsed)
        # Still score and rank
        for r in good_local:
            r._score = _score_result(r, query)
        good_local.sort(key=lambda r: r._score, reverse=True)
        return good_local[:max_results]

    # ── Layer 2: External APIs in parallel ──
    external_results: list[BookResult] = []
    async with httpx.AsyncClient() as client:
        tasks = [
            _search_google(client, query),
            _search_openlibrary(client, query, limit=5),
        ]
        api_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(api_results):
            source = ["google", "openlibrary"][i]
            if isinstance(result, Exception):
                logger.warning("API %s failed: %s", source, result)
            else:
                external_results.extend(result)

    # ── Layer 3: Merge + Rank ──
    all_results = local_results + external_results
    merged = _merge_and_rank(all_results, query, limit=max_results)

    # ── Store new external results in catalog (background, own session) ──
    if external_results:
        async def _bg_store():
            try:
                from app.database import async_session as _session_factory
                async with _session_factory() as _db:
                    await _store_in_catalog(_db, external_results)
                    await _db.commit()
            except Exception as e:
                logger.debug("Catalog bg store failed: %s", e)
        asyncio.create_task(_bg_store())

    elapsed = (monotonic() - start) * 1000
    logger.info(
        "Search '%s': %d local + %d external → %d merged (%.0fms)",
        query, len(local_results), len(external_results), len(merged), elapsed,
    )

    return merged