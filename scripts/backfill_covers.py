"""
Backfill cover_url / isbn on entries that were created without them (B2.7 imports,
the xlsx journal import — anything that didn't come through the frontend's book
picker, which is what normally supplies a cover).

Matching is deliberately strict: a wrong cover is worse than the app's fallback
art, so a candidate is only accepted when both the title and the author match
closely. Anything ambiguous is reported and left alone.

Every accepted URL goes through the app's own SSRF allowlist
(app.utils.url_safety.validate_cover_url) and is then fetched to confirm it is a
real image — Open Library happily hands back a 1x1 placeholder for covers it does
not actually have.

Also repairs the `books` catalog row for each match. That matters: entries
created without a cover leave cover-less `source='user'` rows behind, and those
outrank external results in search (local +10, exact title +100), so they shadow
the real covers for everyone.

Read-only by default. Pass --commit to write.

    python scripts/backfill_covers.py --user-email you@example.com
    python scripts/backfill_covers.py --user-email you@example.com --limit 5
    python scripts/backfill_covers.py --user-email you@example.com --commit
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select

from app.database import async_session, engine

engine.echo = False  # keep the report readable under ENVIRONMENT=development

from app.models.book import Book
from app.models.book_entry import BookEntry
from app.models.user import User
from app.services.book_search import (
    BookResult,
    _search_google,
    _search_openlibrary,
    _title_similarity,
    normalize,
)
from app.utils.url_safety import validate_cover_url

# A cover is only attached when the candidate clears both bars. These are well
# above the 0.75/0.6 the in-app search uses for merging, because search offers a
# human a list to choose from and this script chooses unattended.
MIN_TITLE_SIM = 0.85
MIN_AUTHOR_SIM = 0.60

# Open Library serves a tiny placeholder rather than 404ing for missing covers.
MIN_COVER_BYTES = 1000

CONCURRENCY = 4
DELAY_SECONDS = 0.34  # be a polite client; these are free public APIs


# Unauthenticated Google Books is IP-rate-limited and 429s almost immediately.
# Once it has clearly given up, stop asking — it only costs latency and log noise.
_google_strikes = 0
_GOOGLE_GIVE_UP_AFTER = 3


def _query_title(title: str) -> str:
    """Strip the shelf-keeping decorations a catalog won't know about.

    "The Idiot (Batuman)" -> "The Idiot"   (a disambiguator we added, not part of
                                            the published title)
    "Discworld: The Fifth Elephant" -> "The Fifth Elephant"  (series prefix)
    """
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    if ":" in cleaned:
        head, tail = cleaned.split(":", 1)
        # Only treat it as a series prefix when the tail is substantial — never
        # mangle a real subtitle-bearing title down to nothing.
        if len(tail.strip()) >= 8:
            cleaned = tail.strip()
    return cleaned or title


async def _candidates(client: httpx.AsyncClient, title: str, author: str | None) -> list[BookResult]:
    """External lookups only — the local catalog is exactly what we're repairing."""
    global _google_strikes

    # Query on the title ALONE. Open Library's free-text `q` degrades badly when
    # the author is concatenated on: "Mexican Gothic Silvia Moreno-Garcia" returns
    # nothing, while "Mexican Gothic" returns the exact book. The author is still
    # enforced afterwards in _pick().
    query = _query_title(title)
    results: list[BookResult] = []
    # Open Library intermittently returns an empty body under concurrency; one
    # retry is the difference between a clean 1.00/1.00 match and a false MISS.
    for attempt in range(2):
        try:
            results.extend(await _search_openlibrary(client, query, limit=5))
        except Exception:
            pass
        if results:
            break
        if attempt == 0:
            await asyncio.sleep(1.0)

    if _google_strikes < _GOOGLE_GIVE_UP_AFTER:
        try:
            google = await _search_google(client, query, limit=5)
            if google:
                _google_strikes = 0
                results.extend(google)
            else:
                _google_strikes += 1
        except Exception:
            _google_strikes += 1

    return results


def _pick(title: str, author: str | None, results: list[BookResult]) -> tuple[BookResult | None, str]:
    """Return (best, reason). `best` is None when nothing clears the bars."""
    # Compare against the same cleaned title we searched with, so our own
    # disambiguators ("The Idiot (Batuman)") don't sink an otherwise exact match.
    target = _query_title(title)
    scored = []
    for r in results:
        if not r.cover_url:
            continue
        t_sim = _title_similarity(r.title, target)
        if t_sim < MIN_TITLE_SIM:
            continue
        a_sim = None
        if author and r.author:
            a_sim = _title_similarity(r.author, author)
            if a_sim < MIN_AUTHOR_SIM:
                continue
        elif author and not r.author:
            continue  # we know the author; a candidate that doesn't isn't verifiable
        scored.append((t_sim + (a_sim or 0), t_sim, a_sim, r))

    if not scored:
        with_cover = sum(1 for r in results if r.cover_url)
        return None, f"no confident match ({len(results)} results, {with_cover} with covers)"
    scored.sort(key=lambda s: s[0], reverse=True)
    _, t_sim, a_sim, best = scored[0]
    return best, f"title {t_sim:.2f}" + (f", author {a_sim:.2f}" if a_sim is not None else "")


async def _cover_is_real(client: httpx.AsyncClient, url: str) -> bool:
    """Confirm the URL serves an actual image of plausible size."""
    try:
        res = await client.get(url, timeout=8.0, follow_redirects=True)
        if res.status_code != 200:
            return False
        if not res.headers.get("content-type", "").startswith("image/"):
            return False
        return len(res.content) >= MIN_COVER_BYTES
    except Exception:
        return False


async def _resolve(client: httpx.AsyncClient, sem: asyncio.Semaphore, entry: BookEntry) -> dict:
    async with sem:
        await asyncio.sleep(DELAY_SECONDS)
        results = await _candidates(client, entry.title, entry.author)
        best, reason = _pick(entry.title, entry.author, results)
        if best is None:
            return {"entry": entry, "ok": False, "reason": reason}

        try:
            url = validate_cover_url(best.cover_url)
        except ValueError as e:
            return {"entry": entry, "ok": False, "reason": f"cover host rejected: {e}"}
        if not url:
            return {"entry": entry, "ok": False, "reason": "no cover url"}

        if not await _cover_is_real(client, url):
            return {"entry": entry, "ok": False, "reason": "cover URL is a placeholder or dead"}

        return {
            "entry": entry, "ok": True, "reason": reason, "cover_url": url,
            "isbn": best.isbn, "isbn_10": best._isbn_10, "matched": f"{best.title} — {best.author}",
            "source": best._source,
        }


async def run(user_email: str, limit: int | None, commit: bool) -> int:
    async with async_session() as db:
        user = (
            await db.execute(select(User).where(User.email == user_email.lower().strip()))
        ).scalar_one_or_none()
        if user is None:
            print(f"No user with email {user_email!r}.", file=sys.stderr)
            return 1

        stmt = (
            select(BookEntry)
            .where(BookEntry.user_id == user.id, BookEntry.cover_url.is_(None))
            .order_by(BookEntry.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        entries = (await db.execute(stmt)).scalars().all()

        print(f"User: {user.username} <{user.email}>")
        print(f"Entries missing a cover: {len(entries)}"
              + (f" (limited to {limit})" if limit else "") + "\n")
        if not entries:
            print("Nothing to do.")
            return 0

        sem = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(headers={"User-Agent": "bookdna-cover-backfill/1.0"}) as client:
            resolved = await asyncio.gather(*(_resolve(client, sem, e) for e in entries))

        found = [r for r in resolved if r["ok"]]
        missed = [r for r in resolved if not r["ok"]]

        for r in found:
            e = r["entry"]
            print(f"  OK    {e.title[:34]:<34} <- {r['matched'][:44]:<44} [{r['source']}, {r['reason']}]")
        for r in missed:
            print(f"  MISS  {r['entry'].title[:34]:<34} {r['reason']}")

        print(f"\n  matched: {len(found)}   unmatched: {len(missed)}")

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to apply.")
            return 0

        for r in found:
            e = r["entry"]
            e.cover_url = r["cover_url"]
            if r["isbn"] and not e.isbn:
                e.isbn = r["isbn"][:13]

            # Repair the catalog row so this cover is reused by search instead of
            # the cover-less row shadowing it.
            book = (await db.execute(
                select(Book).where(
                    Book.title_normalized == normalize(e.title),
                    Book.author_normalized == (normalize(e.author) if e.author else None),
                )
            )).scalars().first()
            if book:
                book.cover_url = book.cover_url or r["cover_url"]
                book.cover_verified = True
                # books.isbn_13 / isbn_10 are UNIQUE, and the catalog already holds
                # rows harvested from earlier searches. Claim an ISBN only if no
                # other row owns it, or the whole backfill rolls back on a collision.
                if r["isbn"] and len(r["isbn"]) == 13 and not book.isbn_13:
                    taken = (await db.execute(
                        select(Book.id).where(Book.isbn_13 == r["isbn"], Book.id != book.id)
                    )).scalars().first()
                    if not taken:
                        book.isbn_13 = r["isbn"]
                if r["isbn_10"] and not book.isbn_10:
                    taken = (await db.execute(
                        select(Book.id).where(Book.isbn_10 == r["isbn_10"], Book.id != book.id)
                    )).scalars().first()
                    if not taken:
                        book.isbn_10 = r["isbn_10"]

        await db.commit()
        print(f"\nCommitted {len(found)} covers.")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user-email", required=True)
    ap.add_argument("--limit", type=int, default=None, help="only process the N most recent")
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default is a read-only dry run)")
    args = ap.parse_args()
    return asyncio.run(run(args.user_email, args.limit, args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
