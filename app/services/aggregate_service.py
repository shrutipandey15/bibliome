"""Book emotional aggregates: compute, refresh, read (B8.2–B8.4).

    "for readers in general, this book does X"

Computed per book and bounded by that book's reader count — never a global sweep
per request (the P4-2 trap). The hot path is ``recompute_book``, scheduled
post-commit for the one book whose tags changed.
"""

import logging
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session
from app.models.book_aggregate import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_EMERGING,
    BookEmotionAggregate,
)
from app.models.book_entry import BookEntry, EntryEmotion
from app.utils.emotions import canonicalize

logger = logging.getLogger("bookdna.aggregate")

# A want_to_read has no emotional data yet; only books someone actually engaged
# with can say what the book does to people. Re-reads and pauses count — the
# reader has read it. Abandonment is signal, not absence, so it counts too.
ENGAGED_STATUSES = ("finished", "abandoned", "paused", "reread")
DNF_STATUSES = ("abandoned",)


def _confidence_for(reader_count: int) -> str:
    s = get_settings()
    if reader_count >= s.AGGREGATE_CONFIRMED_MIN_READERS:
        return CONFIDENCE_CONFIRMED
    return CONFIDENCE_EMERGING


def build_profile(rows: list[tuple]) -> dict:
    """Pure aggregation over (user_id, status, verdict, emotion_id, strength) rows.

    One row per (entry, emotion); entries with no emotions appear once with
    emotion_id=None so they still count toward reader_count / dnf_rate.
    Separated from the DB so it can be tested directly.
    """
    readers: set[uuid.UUID] = set()
    dnf_readers: set[uuid.UUID] = set()
    verdicts: dict[uuid.UUID, str] = {}
    # emotion -> reader -> [strengths]; keyed by reader so one person tagging a
    # re-read twice can't inflate the count.
    per_emotion: dict[str, dict[uuid.UUID, list[int]]] = defaultdict(lambda: defaultdict(list))

    for user_id, status, verdict, emotion_id, strength in rows:
        readers.add(user_id)
        if status in DNF_STATUSES:
            dnf_readers.add(user_id)
        if verdict:
            verdicts[user_id] = verdict
        if emotion_id:
            slug = canonicalize(emotion_id)
            if slug:  # dead legacy vocabulary canonicalizes to None — skip it
                per_emotion[slug][user_id].append(strength or 5)

    reader_count = len(readers)
    if not reader_count:
        return {
            "reader_count": 0, "emotion_profile": {}, "verdict_profile": {}, "dnf_rate": 0.0,
        }

    emotion_profile = {}
    for slug, by_reader in per_emotion.items():
        # Mean of each reader's mean, so a reader is one vote regardless of how
        # many entries they have for the book.
        reader_means = [sum(v) / len(v) for v in by_reader.values()]
        emotion_profile[slug] = {
            "mean_strength": round(sum(reader_means) / len(reader_means), 2),
            "count": len(by_reader),
            "tagged_by_fraction": round(len(by_reader) / reader_count, 3),
        }

    verdict_profile = {}
    if verdicts:
        total = len(verdicts)
        for value in ("yes", "no", "not_sure"):
            n = sum(1 for v in verdicts.values() if v == value)
            verdict_profile[value] = round(n / total, 3)

    return {
        "reader_count": reader_count,
        "emotion_profile": emotion_profile,
        "verdict_profile": verdict_profile,
        "dnf_rate": round(len(dnf_readers) / reader_count, 3),
    }


async def _load_rows(db: AsyncSession, book_id: uuid.UUID) -> list[tuple]:
    result = await db.execute(
        select(
            BookEntry.user_id,
            BookEntry.status,
            BookEntry.verdict,
            EntryEmotion.emotion_id,
            EntryEmotion.strength,
        )
        .outerjoin(EntryEmotion, EntryEmotion.entry_id == BookEntry.id)
        .where(BookEntry.book_id == book_id, BookEntry.status.in_(ENGAGED_STATUSES))
    )
    return list(result.all())


async def compute_aggregate(db: AsyncSession, book_id: uuid.UUID) -> dict:
    """Compute (without writing) the aggregate for one book."""
    profile = build_profile(await _load_rows(db, book_id))
    profile["confidence"] = _confidence_for(profile["reader_count"])
    return profile


async def recompute_book(db: AsyncSession, book_id: uuid.UUID) -> dict | None:
    """Recompute and upsert one book's aggregate. Returns the stored profile.

    Deletes the row when a book drops to zero engaged readers, so a stale profile
    can never outlive the data behind it.
    """
    profile = await compute_aggregate(db, book_id)

    if profile["reader_count"] == 0:
        existing = await db.get(BookEmotionAggregate, book_id)
        if existing:
            await db.delete(existing)
        return None

    await db.execute(
        pg_insert(BookEmotionAggregate)
        .values(book_id=book_id, **profile)
        .on_conflict_do_update(index_elements=["book_id"], set_=profile)
    )
    return profile


async def refresh_book_aggregate(book_id: uuid.UUID | None) -> None:
    """Post-commit background entry point: recompute one book in its own session.

    Mirrors ``background.recalculate_dna`` — scheduled via BackgroundTasks so it
    runs after the triggering request's transaction is durable (B1.6 / P2-1), and
    never raises into the caller.
    """
    if book_id is None:
        return
    try:
        async with async_session() as db:
            async with db.begin():
                await recompute_book(db, book_id)
        from app.utils.cache import invalidate_book_aggregate

        await invalidate_book_aggregate(book_id)
    except Exception as e:
        logger.error("Aggregate refresh failed for book %s: %s", book_id, e)


async def rebuild_all(db: AsyncSession) -> tuple[int, int]:
    """Nightly safety net / backfill: rebuild every book with engaged entries.

    Returns (books_rebuilt, books_cleared). Iterates book-by-book rather than
    loading every entry at once.
    """
    book_ids = (await db.execute(
        select(BookEntry.book_id)
        .where(BookEntry.book_id.isnot(None), BookEntry.status.in_(ENGAGED_STATUSES))
        .distinct()
    )).scalars().all()

    rebuilt = 0
    for book_id in book_ids:
        if await recompute_book(db, book_id):
            rebuilt += 1

    # Drop aggregates whose last engaged entry disappeared. With no engaged
    # entries left at all, every aggregate is stale — an unguarded notin_([])
    # would match nothing and silently strand them.
    stmt = select(BookEmotionAggregate.book_id)
    if book_ids:
        stmt = stmt.where(BookEmotionAggregate.book_id.notin_(book_ids))
    stale = (await db.execute(stmt)).scalars().all()
    for book_id in stale:
        row = await db.get(BookEmotionAggregate, book_id)
        if row:
            await db.delete(row)

    return rebuilt, len(stale)
