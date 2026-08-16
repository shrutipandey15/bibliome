import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book_entry import BookEntry
from app.models.entry_checkin import EntryCheckin


async def get_owned_entry(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID
) -> BookEntry | None:
    result = await db.execute(
        select(BookEntry).where(
            BookEntry.id == entry_id, BookEntry.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def list_checkins(db: AsyncSession, entry_id: uuid.UUID) -> list[EntryCheckin]:
    """All check-ins for an entry, oldest first (the read's emotional timeline)."""
    result = await db.execute(
        select(EntryCheckin)
        .where(EntryCheckin.entry_id == entry_id)
        .order_by(EntryCheckin.created_at.asc())
    )
    return list(result.scalars().all())


async def create_checkin(
    db: AsyncSession,
    entry_id: uuid.UUID,
    emotion_slug: str,
    note: str | None,
) -> EntryCheckin:
    checkin = EntryCheckin(
        entry_id=entry_id,
        emotion_id=emotion_slug,
        note=note,
    )
    db.add(checkin)
    await db.flush()
    await db.refresh(checkin)
    return checkin


async def update_status(
    db: AsyncSession, entry: BookEntry, status: str
) -> BookEntry:
    entry.status = status
    # Keep finished_at consistent so the calendar/mirror key correctly (P5-7):
    # entering 'finished' stamps today if unset; leaving it drops the date.
    #
    # `reread` is the exception, and it has to be. A reread is evidence the book
    # WAS finished, not evidence it wasn't — clearing the date on the way in
    # erased the original finish from the calendar and the mirror, silently, on
    # a status change the reader would read as celebratory. Every other
    # non-finished status genuinely means "not finished", so it still clears.
    if status == "finished":
        if entry.finished_at is None:
            entry.finished_at = date.today()
    elif status != "reread":
        entry.finished_at = None
    await db.flush()
    return entry
