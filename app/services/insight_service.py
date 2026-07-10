"""Rule-based Mirror insight sentence generator (v1).

`generate_insight()` is isolated so it can be swapped for an LLM version later.
"""

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.user import User
from app.utils.emotions import EMOTIONS_BY_SLUG, canonicalize


def current_week_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-{iso_week:02d}"


def _display_name(slug: str) -> str:
    meta = EMOTIONS_BY_SLUG.get(slug)
    return meta["name"] if meta else slug.title()


def generate_insight(user: User, entries: list[BookEntry]) -> str | None:
    """Pick the first matching rule. `entries` must include `.emotions` preloaded."""
    if len(entries) < 3:
        return None

    now = datetime.now(timezone.utc)
    d30 = now - timedelta(days=30)
    d60 = now - timedelta(days=60)

    def _ts(e: BookEntry) -> datetime:
        # finished_at is a date; fall back to created_at for sorting/filtering
        if e.finished_at:
            return datetime.combine(e.finished_at, datetime.min.time(), tzinfo=timezone.utc)
        return e.created_at

    finished_last_30 = [
        e for e in entries
        if e.finished_at and _ts(e) >= d30 and e.status == "finished"
    ]
    finished_prev_30 = [
        e for e in entries
        if e.finished_at and d60 <= _ts(e) < d30 and e.status == "finished"
    ]
    entries_last_30 = [e for e in entries if _ts(e) >= d30]
    entries_prev_30 = [e for e in entries if d60 <= _ts(e) < d30]

    # Collect canonical emotion tags per period
    def _tags(es: list[BookEntry]) -> list[str]:
        out: list[str] = []
        for e in es:
            for em in e.emotions:
                c = canonicalize(em.emotion_id)
                if c:
                    out.append(c)
        return out

    tags_last_30 = _tags(entries_last_30)
    tags_prev_30 = set(_tags(entries_prev_30))

    # Rule 1: single emotion >50% of all tags in last 30 days
    if tags_last_30:
        counts = Counter(tags_last_30)
        top, top_count = counts.most_common(1)[0]
        if top_count / len(tags_last_30) > 0.5:
            return (
                f"{_display_name(top)} shows up in almost everything you read. "
                f"You are not avoiding it — you are sitting with it. That is rare."
            )

    # Rule 2: >=5 books finished in last 30 days
    if len(finished_last_30) >= 5:
        return f"{len(finished_last_30)} books this month. You have been somewhere else entirely."

    # Rule 3: <=1 book finished in last 30 but prior pattern of more
    if len(finished_last_30) <= 1 and len(finished_prev_30) >= 3:
        return "You have been away. Something must be pulling you back now."

    # Rule 4: an emotion with count >=3 in last 30 that wasn't in prior 30
    if tags_last_30:
        counts = Counter(tags_last_30)
        for slug, c in counts.most_common():
            if c >= 3 and slug not in tags_prev_30:
                return f"You are tagging {_display_name(slug)} again. It has been a while."

    # Rule 5: fallback when >=3 entries
    return "Every book you finish is telling us more about you."


async def get_or_cache_insight(db: AsyncSession, user: User) -> tuple[str | None, str]:
    """Return (sentence, week_key), using per-week cache on the user row."""
    week = current_week_key()

    if user.cached_insight_week == week:
        return user.cached_insight, week

    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user.id)
    )
    entries = list(result.scalars().all())

    sentence = generate_insight(user, entries)

    user.cached_insight = sentence
    user.cached_insight_week = week
    await db.flush()

    return sentence, week


def _period_phrase(days_ago: int) -> str:
    if days_ago < 45:
        return "last month"
    if days_ago < 75:
        return "two months ago"
    if days_ago < 105:
        return "three months ago"
    return "a few months ago"


def generate_weekly_memory(entries: list[BookEntry]) -> str | None:
    """Look at older entries (30+ days) and surface one observation.

    `entries` must include `.emotions` preloaded.
    """
    now = datetime.now(timezone.utc)
    d30 = now - timedelta(days=30)
    d60 = now - timedelta(days=60)
    d120 = now - timedelta(days=120)

    def _ts(e: BookEntry) -> datetime:
        if e.finished_at:
            return datetime.combine(e.finished_at, datetime.min.time(), tzinfo=timezone.utc)
        return e.created_at

    # Rule 1: an emotion tagged exactly twice across 2 different books in last 60 days
    last_60 = [e for e in entries if _ts(e) >= d60 and _ts(e) < d30]
    emotion_to_books: dict[str, list[BookEntry]] = {}
    for e in last_60:
        seen_in_entry: set[str] = set()
        for em in e.emotions:
            c = canonicalize(em.emotion_id)
            if not c or c in seen_in_entry:
                continue
            seen_in_entry.add(c)
            emotion_to_books.setdefault(c, []).append(e)

    for slug, books in emotion_to_books.items():
        if len(books) == 2 and books[0].id != books[1].id:
            b1, b2 = books[0].title, books[1].title
            days_ago = (now - _ts(books[0])).days
            return (
                f"Last {_period_phrase(days_ago)} you tagged {_display_name(slug)} twice — "
                f"once for {b1}, once for {b2}. You might have a type."
            )

    # Rule 2: entry from 60–120 days ago with high-intensity Grief + no Catharsis tagged since
    old_grief_entries = []
    for e in entries:
        ts = _ts(e)
        if d120 <= ts < d60:
            has_grief = any(
                canonicalize(em.emotion_id) == "grief" and em.strength >= 7
                for em in e.emotions
            )
            if has_grief:
                old_grief_entries.append((e, ts))

    if old_grief_entries:
        # Any catharsis tagged since d60?
        catharsis_since = any(
            _ts(e) >= d60 and any(canonicalize(em.emotion_id) == "catharsis" for em in e.emotions)
            for e in entries
        )
        if not catharsis_since:
            e, ts = old_grief_entries[0]
            days_ago = (now - ts).days
            return (
                f"You finished {e.title} {_period_phrase(days_ago)} ago and tagged Grief. "
                f"You have not tagged Catharsis since. Are you still carrying it?"
            )

    return None


async def get_or_cache_weekly_memory(db: AsyncSession, user: User) -> tuple[str | None, str]:
    """Return (memory, week_key), cached per ISO week on the user row."""
    week = current_week_key()

    if user.cached_weekly_memory_week == week:
        return user.cached_weekly_memory, week

    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user.id)
    )
    entries = list(result.scalars().all())

    memory = generate_weekly_memory(entries)

    user.cached_weekly_memory = memory
    user.cached_weekly_memory_week = week
    await db.flush()

    return memory, week
