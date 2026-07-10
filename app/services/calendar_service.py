"""Emotional calendar — monthly emotion distribution for the Identity tab."""

import uuid
from calendar import month_abbr
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.utils.emotions import EMOTIONS_BY_SLUG, canonicalize


def _month_range(end: date, months: int) -> list[tuple[int, int]]:
    """Return (year, month) tuples, oldest first, ending at `end`'s month."""
    result = []
    y, m = end.year, end.month
    for _ in range(months):
        result.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    result.reverse()
    return result


def _entry_month(e: BookEntry) -> tuple[int, int] | None:
    """Attribution month: finished_at if set, else created_at."""
    if e.finished_at:
        return (e.finished_at.year, e.finished_at.month)
    if e.created_at:
        return (e.created_at.year, e.created_at.month)
    return None


async def get_emotional_calendar(
    db: AsyncSession, user_id: uuid.UUID, months: int
) -> dict:
    months = max(1, min(months, 36))

    today = datetime.now(timezone.utc).date()
    ranges = _month_range(today, months)
    range_set = set(ranges)

    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
    )
    entries = result.scalars().all()

    # month -> slug -> summed strength
    buckets: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in entries:
        key = _entry_month(e)
        if key is None or key not in range_set:
            continue
        for em in e.emotions:
            canon = canonicalize(em.emotion_id)
            if not canon:
                continue
            buckets[key][canon] += float(em.strength)

    out_months = []
    for y, m in ranges:
        month_key = f"{y:04d}-{m:02d}"
        label = f"{month_abbr[m]} {y}"
        weights = buckets.get((y, m), {})
        total = sum(weights.values())
        segments = []
        if total > 0:
            # Sort by weight desc for stable output
            for slug in sorted(weights, key=lambda s: (-weights[s], s)):
                meta = EMOTIONS_BY_SLUG.get(slug)
                if not meta:
                    continue
                segments.append({
                    "emotion": {"slug": slug, "color": meta["color"]},
                    "weight": round(weights[slug] / total, 4),
                })
        out_months.append({
            "month_key": month_key,
            "label": label,
            "segments": segments,
        })

    return {"months": out_months}
