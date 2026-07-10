"""Blind-spots service — emotions the user under-tags or has never tagged."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.utils.emotions import (
    BLIND_SPOT_HINTS,
    EMOTIONS_13,
    EMOTIONS_BY_SLUG,
    canonicalize,
)


async def get_blind_spots(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Up to 3 items: never-tagged emotions first, then rare (<5%) emotions."""
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
    )
    entries = list(result.scalars().all())
    total = len(entries)

    if total < 5:
        return []

    # For each canonical emotion, count how many distinct ENTRIES tagged it
    entry_counts: dict[str, int] = {slug: 0 for slug in EMOTIONS_BY_SLUG.keys()}
    for e in entries:
        seen: set[str] = set()
        for em in e.emotions:
            c = canonicalize(em.emotion_id)
            if c and c not in seen:
                seen.add(c)
                entry_counts[c] = entry_counts.get(c, 0) + 1

    never: list[tuple[str, int]] = []
    rare: list[tuple[str, int]] = []
    for e_meta in EMOTIONS_13:
        slug = e_meta["slug"]
        count = entry_counts.get(slug, 0)
        prevalence = count / total
        if count == 0:
            never.append((slug, count))
        elif prevalence < 0.05:
            rare.append((slug, count))

    # Prioritize never-tagged, then rare. Deterministic order within each bucket
    # by EMOTIONS_13 declaration order.
    picks = (never + rare)[:3]

    out = []
    for slug, count in picks:
        meta = EMOTIONS_BY_SLUG.get(slug)
        if not meta:
            continue
        hint = BLIND_SPOT_HINTS.get(slug, {"category": slug, "feeling": slug})
        prevalence = count / total
        if count == 0:
            observation = (
                f"You have never tagged {meta['name']}. "
                f"Either you avoid {hint['category']}, or you do not let yourself {hint['feeling']}. "
                f"Worth thinking about."
            )
        else:
            observation = (
                f"{meta['name']} shows up in less than 5% of your reads. You might want to ask why."
            )
        out.append({
            "emotion": {"slug": slug, "name": meta["name"], "symbol": meta["symbol"]},
            "observation": observation,
            "prevalence": round(prevalence, 4),
        })

    return out
