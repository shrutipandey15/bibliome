"""Library import: Goodreads / StoryGraph CSV export → entries (B2.7).

Cold-start path so a new user isn't staring at an empty shelf. Imported books
carry no emotions and no fabricated intensity — just title/author/isbn/status and
whatever real dates the export had. Deduped against the user's existing library.
"""

import csv
import io
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book_entry import BookEntry
from app.services.book_search import normalize

# Goodreads "Exclusive Shelf" / StoryGraph "Read Status" → our status vocabulary.
_STATUS_MAP = {
    "read": "finished",
    "currently-reading": "reading",
    "currently reading": "reading",
    "to-read": "want_to_read",
    "to read": "want_to_read",
    "did-not-finish": "finished",
    "did not finish": "finished",
    "dnf": "finished",
}

_DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y")
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def _parse_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _clean_isbn(raw: str | None) -> str | None:
    if not raw:
        return None
    # Goodreads wraps ISBNs like ="9780..." — strip the spreadsheet armor.
    s = raw.strip().lstrip("=").strip('"').replace("-", "").replace(" ", "")
    return s[:13] if len(s) in (10, 13) and s.isalnum() else None


def parse_import_csv(content: str) -> tuple[list[dict], list[str]]:
    """Parse a Goodreads or StoryGraph export. Returns (books, errors)."""
    errors: list[str] = []
    reader = csv.DictReader(io.StringIO(content))
    headers = {(h or "").strip() for h in (reader.fieldnames or [])}
    if not headers:
        return [], ["Empty or unreadable CSV."]

    is_goodreads = "Exclusive Shelf" in headers or "Book Id" in headers

    books: list[dict] = []
    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            if is_goodreads:
                title = (row.get("Title") or "").strip()
                author = (row.get("Author") or "").strip() or None
                isbn = _clean_isbn(row.get("ISBN13") or row.get("ISBN"))
                shelf = (row.get("Exclusive Shelf") or "read").strip().lower()
                finished = _parse_date(row.get("Date Read"))
            else:  # StoryGraph
                title = (row.get("Title") or "").strip()
                author = (row.get("Authors") or row.get("Author") or "").strip() or None
                isbn = _clean_isbn(row.get("ISBN/UID"))
                shelf = (row.get("Read Status") or "read").strip().lower()
                finished = _parse_date(row.get("Last Date Read"))

            if not title:
                continue

            status = _STATUS_MAP.get(shelf, "finished")
            books.append({
                "title": title[:300],
                "author": author[:200] if author else None,
                "isbn": isbn,
                "status": status,
                # Keep the real finish date (may be None). Deliberately NOT defaulted
                # to today — a book read years ago must not land in this month's calendar.
                "finished_at": finished if status == "finished" else None,
            })
        except Exception as e:  # one bad row shouldn't sink the whole import
            errors.append(f"Row {i}: {e}")

    return books, errors


def _dedupe_keys(title: str | None, author: str | None, isbn: str | None) -> set[str]:
    keys = {f"t:{normalize(title or '')}|{normalize(author or '')}"}
    if isbn:
        keys.add(f"isbn:{isbn}")
    return keys


async def import_entries(db: AsyncSession, user_id, books: list[dict]) -> tuple[int, int]:
    """Create entries for parsed books, skipping duplicates. Returns (imported, skipped)."""
    result = await db.execute(
        select(BookEntry.title, BookEntry.author, BookEntry.isbn).where(BookEntry.user_id == user_id)
    )
    seen: set[str] = set()
    for title, author, isbn in result.all():
        seen |= _dedupe_keys(title, author, isbn)

    imported = skipped = 0
    for b in books:
        keys = _dedupe_keys(b["title"], b["author"], b["isbn"])
        if keys & seen:  # already in library, or a duplicate within this file
            skipped += 1
            continue
        db.add(BookEntry(
            user_id=user_id,
            title=b["title"],
            author=b["author"],
            isbn=b["isbn"],
            status=b["status"],
            finished_at=b["finished_at"],
            intensity=5,
        ))
        seen |= keys
        imported += 1

    await db.flush()
    return imported, skipped
