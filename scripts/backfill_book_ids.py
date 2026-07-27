"""One-time backfill: resolve every existing entry to a canonical book_id, then
build the per-book emotional aggregates (B8.1 / B8.2).

Entries written before 023 have no book_id. This resolves each one through the
same find-or-create the live write paths use, so a backfilled entry and a newly
written one land on the same canonical row.

Unresolved entries are listed for manual review — an entry with a blank title
resolves to nothing, and that is reported rather than guessed at.

Read-only by default. Pass --commit to write.

    python scripts/backfill_book_ids.py
    python scripts/backfill_book_ids.py --commit
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.database import async_session, engine

engine.echo = False

from app.config import get_settings
from app.models.book import Book
from app.models.book_aggregate import BookEmotionAggregate
from app.models.book_entry import BookEntry
from app.services.aggregate_service import ENGAGED_STATUSES, rebuild_all
from app.services.book_identity import resolve_book


async def rebuild_only() -> int:
    """The B8.3 nightly safety net. Cron this; the hot path stays incremental."""
    async with async_session() as db:
        rebuilt, cleared = await rebuild_all(db)
        await db.commit()
        print(f"Rebuilt {rebuilt} aggregates, cleared {cleared} stale.")
    return 0


async def run(commit: bool) -> int:
    async with async_session() as db:
        entries = (await db.execute(
            select(BookEntry).where(BookEntry.book_id.is_(None)).order_by(BookEntry.title)
        )).scalars().all()

        total = (await db.execute(select(func.count(BookEntry.id)))).scalar() or 0
        print(f"Entries: {total} total, {len(entries)} without a book_id\n")

        resolved, unresolved = 0, []
        created_before = (await db.execute(select(func.count(Book.id)))).scalar() or 0

        for entry in entries:
            book = await resolve_book(db, entry.title, entry.author, entry.isbn, entry.cover_url)
            if book is None:
                unresolved.append(entry)
                continue
            entry.book_id = book.id
            resolved += 1

        created_after = (await db.execute(select(func.count(Book.id)))).scalar() or 0
        print(f"  resolved        : {resolved}")
        print(f"  unresolved      : {len(unresolved)}")
        print(f"  catalog rows new: {created_after - created_before}")
        for e in unresolved[:20]:
            print(f"      UNRESOLVED  {e.id}  title={e.title!r} author={e.author!r}")
        if len(unresolved) > 20:
            print(f"      ... and {len(unresolved) - 20} more")

        # How many distinct books, and how the readers spread across them.
        engaged = (await db.execute(
            select(BookEntry.book_id, BookEntry.user_id)
            .where(BookEntry.book_id.isnot(None), BookEntry.status.in_(ENGAGED_STATUSES))
        )).all()
        readers_per_book = Counter()
        for book_id, user_id in {(b, u) for b, u in engaged}:
            readers_per_book[book_id] += 1

        s = get_settings()
        confirmed = sum(1 for n in readers_per_book.values() if n >= s.AGGREGATE_CONFIRMED_MIN_READERS)
        servable = sum(1 for n in readers_per_book.values() if n >= s.AGGREGATE_PUBLIC_MIN_READERS)
        print(f"\n  books with engaged entries : {len(readers_per_book)}")
        print(f"  reader-count distribution  : {dict(sorted(Counter(readers_per_book.values()).items()))}")
        print(f"  would be 'confirmed' (>={s.AGGREGATE_CONFIRMED_MIN_READERS} readers): {confirmed}")
        print(f"  servable to other users (>={s.AGGREGATE_PUBLIC_MIN_READERS}): {servable}")

        if not commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to apply.")
            return 0

        await db.commit()
        print(f"\nCommitted {resolved} book_id links.")

        print("Building aggregates...")
        rebuilt, cleared = await rebuild_all(db)
        await db.commit()

        n_agg = (await db.execute(select(func.count(BookEmotionAggregate.book_id)))).scalar() or 0
        tiers = (await db.execute(
            select(BookEmotionAggregate.confidence, func.count())
            .group_by(BookEmotionAggregate.confidence)
        )).all()
        print(f"  aggregates built : {rebuilt} (cleared {cleared} stale)")
        print(f"  aggregate rows   : {n_agg}")
        print(f"  confidence tiers : {dict(tiers)}")

    print("\nDone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default is a read-only dry run)")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="skip resolution; just rebuild every aggregate (nightly job)")
    args = ap.parse_args()
    if args.rebuild_only:
        return asyncio.run(rebuild_only())
    return asyncio.run(run(args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
