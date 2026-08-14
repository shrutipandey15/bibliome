"""One-shot backfill: recompute every cached DNA payload with the calibrated scorer.

Every ``cached_dna_v2`` and ``cached_dna_profile`` in the database was written by
the uncentered scorer. Until they are recomputed the DNA tab and every share link
serve labels from an engine that no longer exists — precisely the split-brain the
one-engine rule (P0-1) exists to prevent. The cache is not a performance detail
here; it is what readers actually see.

Run:  python -m scripts.backfill_dna_cache --dry-run    # report, change nothing
      python -m scripts.backfill_dna_cache

NO NOTIFICATIONS. ``compute_and_cache`` recomputes and stores; it does not
snapshot and does not notify — ``maybe_snapshot_and_notify`` is a separate call
and this script never makes it. That is deliberate and load-bearing: a third of
readers change archetype from the vector change alone, and telling several hundred
people their identity shifted because we fixed our own arithmetic would be a lie.
If you ever add a call here, gate the notify.

BATCHED. One transaction per batch, not one across the whole table. A backfill
that holds a single transaction over every user takes a long lock, and if it dies
at 90% it rolls back the other 90% too.
"""

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.database import async_session
from app.models.user import User
from app.services.dna_service import compute_and_cache
from app.utils.cache import invalidate_dna

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill")


def _archetype_name(payload: dict | None) -> str | None:
    """The label a payload is currently showing, or None if it abstained."""
    if not payload:
        return None
    arch = payload.get("archetype")
    return arch.get("name") if arch else None


async def _user_ids() -> list:
    """Everyone carrying a stale payload. Read once, up front: the set is small
    and paging by offset while the rows underneath are being rewritten is a good
    way to skip users."""
    async with async_session() as db:
        return list((await db.execute(
            select(User.id).where(User.cached_dna_v2.isnot(None)).order_by(User.id)
        )).scalars().all())


async def run(batch_size: int, dry_run: bool) -> None:
    ids = await _user_ids()
    log.info("%d user(s) with a cached DNA payload%s",
             len(ids), "  [DRY RUN — nothing will be written]" if dry_run else "")
    if not ids:
        return

    changed: list[tuple[str, str | None, str | None]] = []
    lost, gained, failed = 0, 0, 0
    done = 0

    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        async with async_session() as db:
            # Explicit transaction rather than `async with db.begin()`, because
            # that context manager commits on a clean exit and --dry-run needs the
            # work computed and then thrown away.
            trans = await db.begin()
            try:
                users = (await db.execute(
                    select(User).where(User.id.in_(chunk))
                )).scalars().all()

                for user in users:
                    before = _archetype_name(user.cached_dna_v2)
                    # Savepoint per user: a failed flush poisons the session until
                    # something rolls back, so without this one bad shelf would
                    # take the rest of its batch down with it.
                    sp = await db.begin_nested()
                    try:
                        v2 = await compute_and_cache(db, user)
                        await sp.commit()
                    except Exception as e:
                        await sp.rollback()
                        failed += 1
                        log.warning("  FAILED %s: %s", user.username, e)
                        continue
                    after = _archetype_name(v2)
                    if before != after:
                        changed.append((user.username, before, after))
                        if after is None:
                            lost += 1
                        elif before is None:
                            gained += 1
                    done += 1

                await (trans.rollback() if dry_run else trans.commit())
            except Exception:
                await trans.rollback()
                raise

        if not dry_run:
            for uid in chunk:
                await invalidate_dna(uid)

        log.info("  batch %d-%d done", start + 1, start + len(chunk))

    log.info("\nrecomputed:        %d", done)
    if failed:
        log.info("failed:            %d", failed)
    log.info("changed archetype: %d  (%.1f%%)", len(changed),
             100.0 * len(changed) / done if done else 0.0)
    log.info("  of which newly abstaining: %d", lost)
    log.info("  of which newly labelled:   %d", gained)
    if changed:
        log.info("\n%-24s %-24s %s", "user", "before", "after")
        for username, before, after in changed:
            log.info("%-24s %-24s %s", username, before or "(none)", after or "(none)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    await run(args.batch_size, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
