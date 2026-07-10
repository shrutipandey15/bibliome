import uuid

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
    await db.flush()
    return entry
