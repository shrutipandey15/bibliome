"""Assemble arc-card data for a finished (or mid-read) entry."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.entry_checkin import EntryCheckin
from app.schemas.arc import ArcBeat, ArcCardResponse
from app.utils.emotions import EMOTIONS_BY_SLUG, canonicalize


def _beat(slug: str | None, label: str) -> ArcBeat | None:
    if not slug:
        return None
    canon = canonicalize(slug) or slug
    meta = EMOTIONS_BY_SLUG.get(canon)
    if not meta:
        return None
    return ArcBeat(slug=canon, symbol=meta["symbol"], color=meta["color"], label=label)


async def get_arc_card(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID
) -> ArcCardResponse | None:
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.checkins))
        .where(BookEntry.id == entry_id, BookEntry.user_id == user_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return None

    # Most recent DNA snapshot for the user — for card's dna_type badge
    dna_result = await db.execute(
        select(DNASnapshot)
        .where(DNASnapshot.user_id == user_id)
        .order_by(DNASnapshot.generated_at.desc())
        .limit(1)
    )
    snapshot = dna_result.scalar_one_or_none()
    dna_type = None
    if snapshot:
        dna_type = snapshot.dna_type_slug or snapshot.personality_type

    checkins_sorted = sorted(entry.checkins or [], key=lambda c: c.created_at)

    arc: list[ArcBeat] = []
    start = _beat(entry.arc_start_emotion_id, "Start")
    middle = _beat(entry.arc_middle_emotion_id, "Middle")
    end = _beat(entry.arc_end_emotion_id, "End")

    if start:
        arc.append(start)
    for ci in checkins_sorted:
        b = _beat(ci.emotion_id, "Check-in")
        if b:
            arc.append(b)
    if middle:
        arc.append(middle)
    if end:
        arc.append(end)

    return ArcCardResponse(
        entry_id=entry.id,
        title=entry.title,
        author=entry.author,
        dna_type=dna_type,
        arc=arc,
        intensity=entry.intensity,
        thought=entry.finish_thought,
    )
