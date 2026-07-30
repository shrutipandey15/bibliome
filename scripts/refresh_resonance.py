"""Nightly resonance sweep: recompute every reader's candidate matches.

The per-entry background task keeps a writing reader current, but it only ever
refreshes the *writer*. When A logs a book that resonates with B's shelf, B's own
suggestions are stale until either B writes something or this runs. Cron it
nightly; it is idempotent, so a double run costs time and creates nothing.

Read-only by default. Pass --commit to write.

    python scripts/refresh_resonance.py
    python scripts/refresh_resonance.py --commit
    python scripts/refresh_resonance.py --commit --user <uuid>
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import async_session, engine

engine.echo = False

from app.services.resonance_service import (  # noqa: E402
    find_candidate_matches,
    refresh_all_matches,
    refresh_matches_for_user,
)


async def dry_run(user_id: uuid.UUID | None) -> None:
    """Report what would be created without writing anything."""
    async with async_session() as db:
        if user_id is not None:
            candidates = await find_candidate_matches(db, user_id)
            print(f"user {user_id}: {len(candidates)} candidate match(es)")
            for c in candidates:
                shared = ", ".join(
                    f"{s.emotion_id}({s.mine}/{s.theirs}{'*' if s.close else ''})"
                    for s in c.shared
                )
                print(f"  book {c.book_id}  {c.strength:<6} score={c.score:<6} {shared}")
            return

        # Whole-instance dry run: count candidates without persisting them.
        from sqlalchemy import select

        from app.models.book_entry import BookEntry
        from app.services.aggregate_service import ENGAGED_STATUSES

        user_ids = (
            await db.execute(
                select(BookEntry.user_id)
                .where(BookEntry.book_id.isnot(None), BookEntry.status.in_(ENGAGED_STATUSES))
                .group_by(BookEntry.user_id)
            )
        ).scalars().all()

        total = 0
        for uid in user_ids:
            total += len(await find_candidate_matches(db, uid))
        print(f"{len(user_ids)} reader(s), {total} candidate match(es) — nothing written")


async def commit_run(user_id: uuid.UUID | None) -> None:
    async with async_session() as db:
        async with db.begin():
            if user_id is not None:
                created = await refresh_matches_for_user(db, user_id)
                print(f"user {user_id}: {created} new match(es)")
            else:
                readers, created = await refresh_all_matches(db)
                print(f"{readers} reader(s) swept, {created} new match(es)")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write matches (default: dry run)")
    parser.add_argument("--user", help="restrict to one reader's UUID")
    args = parser.parse_args()

    user_id = uuid.UUID(args.user) if args.user else None
    try:
        if args.commit:
            await commit_run(user_id)
        else:
            await dry_run(user_id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
