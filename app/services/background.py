"""
Background DNA recalculation.

Scheduled via FastAPI ``BackgroundTasks`` so it runs *after* the request's
transaction has committed (B1.6 / P2-1). Because it opens its own session only
once the triggering entry is durably committed, it always reads the post-write
state — it can no longer cache a profile that excludes the entry that triggered
it. Cache invalidation goes through the single ``invalidate_dna`` helper (B1.3).
"""

import logging
import uuid

from sqlalchemy import select

from app.database import async_session
from app.models.user import User
from app.services.dna_service import compute_and_cache, maybe_snapshot_and_notify
from app.services.resonance_service import refresh_matches_for_user

logger = logging.getLogger("bookdna.background")

# Track in-flight recalculations per user — prevents redundant concurrent DB work
_recalc_running: set[uuid.UUID] = set()
_resonance_running: set[uuid.UUID] = set()


async def recalculate_dna(user_id: uuid.UUID) -> None:
    """
    Recalculate and cache DNA profile for a user.
    Runs in background — uses its own DB session.
    Deduplicates: if a recalculation is already running for this user, skips.
    """
    if user_id in _recalc_running:
        logger.debug("DNA recalc already running for user %s, skipping duplicate", user_id)
        return
    _recalc_running.add(user_id)
    try:
        async with async_session() as db:
            async with db.begin():
                user = (await db.execute(
                    select(User).where(User.id == user_id)
                )).scalar_one_or_none()
                if not user:
                    return

                # Recompute both payloads (private Phase-7 + public signature) and
                # capture a snapshot if the reader has moved far enough (B7.4).
                v2 = await compute_and_cache(db, user)
                await maybe_snapshot_and_notify(db, user)

                logger.debug(
                    "Recalculated DNA for user %s (%d books)",
                    user.username, v2.get("book_count", 0),
                )

        from app.utils.cache import invalidate_dna
        await invalidate_dna(user_id)

    except Exception as e:
        logger.error("Background DNA recalculation failed for user %s: %s", user_id, e)
    finally:
        _recalc_running.discard(user_id)


async def recompute_resonance(user_id: uuid.UUID) -> None:
    """Refresh this reader's resonance matches after they wrote an entry.

    Runs post-commit in its own session for the same reason as the DNA recalc:
    the entry that triggered it has to be durable before the self-join can see
    it. Matching is never done on the read path — this task and the nightly
    sweep (``scripts/refresh_resonance.py``) are the only writers.

    Failure here is non-fatal by design: a missing suggestion is a quiet gap the
    next sweep closes, not a reason to fail the write that triggered it.
    """
    if user_id in _resonance_running:
        logger.debug("Resonance refresh already running for user %s, skipping", user_id)
        return
    _resonance_running.add(user_id)
    try:
        async with async_session() as db:
            async with db.begin():
                created = await refresh_matches_for_user(db, user_id)
            if created:
                logger.debug("Resonance: %d new match(es) for user %s", created, user_id)
    except Exception as e:
        logger.error("Resonance refresh failed for user %s: %s", user_id, e)
    finally:
        _resonance_running.discard(user_id)