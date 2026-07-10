import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.entry_checkin import EntryCheckin
from app.schemas.mirror import (
    EmotionMini,
    LandscapeItem,
    RightNowBook,
    RightNowCheckin,
    RightNowResponse,
)
from app.utils.emotions import EMOTIONS_BY_SLUG, canonicalize


def _dominant_emotion(entry: BookEntry) -> EmotionMini | None:
    """Pick the emotion with highest strength; tiebreak by slug for determinism."""
    if not entry.emotions:
        return None
    ranked = sorted(
        entry.emotions,
        key=lambda e: (-e.strength, e.emotion_id),
    )
    top = ranked[0]
    slug = canonicalize(top.emotion_id) or top.emotion_id
    meta = EMOTIONS_BY_SLUG.get(slug)
    if not meta:
        return None
    return EmotionMini(slug=slug, symbol=meta["symbol"], color=meta["color"])


async def get_landscape(db: AsyncSession, user_id: uuid.UUID) -> list[LandscapeItem]:
    """Return up to 50 entries in status finished/reading, most recently finished first."""
    stmt = (
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(
            BookEntry.user_id == user_id,
            BookEntry.status.in_(("finished", "reading")),
        )
        .order_by(BookEntry.finished_at.desc().nulls_last())
        .limit(50)
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return [
        LandscapeItem(
            entry_id=e.id,
            book_title=e.title,
            book_author=e.author,
            dominant_emotion=_dominant_emotion(e),
            finished_at=e.finished_at,
            status=e.status,
        )
        for e in entries
    ]


def _emotion_mini(slug: str) -> EmotionMini | None:
    canon = canonicalize(slug) or slug
    meta = EMOTIONS_BY_SLUG.get(canon)
    if not meta:
        return None
    return EmotionMini(slug=canon, symbol=meta["symbol"], color=meta["color"])


async def get_right_now(db: AsyncSession, user_id: uuid.UUID) -> RightNowResponse | None:
    """Return the currently-reading book + last check-in, or None."""
    stmt = (
        select(BookEntry)
        .where(BookEntry.user_id == user_id, BookEntry.status == "reading")
        .order_by(BookEntry.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    entry = result.scalar_one_or_none()
    if entry is None:
        return None

    ci_stmt = (
        select(EntryCheckin)
        .where(EntryCheckin.entry_id == entry.id)
        .order_by(EntryCheckin.created_at.desc())
        .limit(1)
    )
    ci_result = await db.execute(ci_stmt)
    checkin = ci_result.scalar_one_or_none()

    last_checkin = None
    if checkin is not None:
        emotion = _emotion_mini(checkin.emotion_id)
        if emotion is not None:
            last_checkin = RightNowCheckin(
                emotion=emotion,
                note=checkin.note,
                created_at=checkin.created_at,
            )

    return RightNowResponse(
        book=RightNowBook(
            entry_id=entry.id,
            title=entry.title,
            author=entry.author,
            cover_url=entry.cover_url,
        ),
        last_checkin=last_checkin,
    )
