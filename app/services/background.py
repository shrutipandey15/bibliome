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
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.book_entry import BookEntry
from app.models.user import User
from app.services.dna_engine import calculate_personality

logger = logging.getLogger("bookdna.background")

# Track in-flight recalculations per user — prevents redundant concurrent DB work
_recalc_running: set[uuid.UUID] = set()


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
                # Fetch user
                user_result = await db.execute(
                    select(User).where(User.id == user_id)
                )
                user = user_result.scalar_one_or_none()
                if not user:
                    return

                # Fetch entries
                entries_result = await db.execute(
                    select(BookEntry)
                    .options(selectinload(BookEntry.emotions))
                    .where(BookEntry.user_id == user_id)
                    .order_by(BookEntry.created_at.asc())
                )
                entries = entries_result.scalars().all()

                entry_dicts = [
                    {
                        "id": str(e.id),
                        "title": e.title,
                        "author": e.author,
                        "intensity": e.intensity,
                        "emotions": [em.emotion_id for em in e.emotions],
                        "created_at": e.created_at,
                        "finished_at": e.finished_at,
                    }
                    for e in entries
                ]

                # Calculate
                result = calculate_personality(entry_dicts)
                result["book_count"] = len(entry_dicts)

                # Cache
                user.cached_dna_profile = result
                user.dna_dirty = False

                user.personality_type = (
                    result["personality"]["name"] if result.get("personality") else None
                )

                logger.debug(
                    "Recalculated DNA for user %s (%d books)",
                    user.username, len(entry_dicts),
                )


        from app.utils.cache import invalidate_dna
        await invalidate_dna(user_id)

    except Exception as e:
        logger.error("Background DNA recalculation failed for user %s: %s", user_id, e)
    finally:
        _recalc_running.discard(user_id)