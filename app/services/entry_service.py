import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry, EntryEmotion
from app.schemas.entry import EntryCreate, EntryUpdate


async def create_entry(db: AsyncSession, user_id: uuid.UUID, data: EntryCreate) -> BookEntry:
    """Create a new book entry with emotions."""
    entry = BookEntry(
        user_id=user_id,
        title=data.title,
        author=data.author,
        intensity=data.intensity,
        quote=data.quote,
        public_echo=data.public_echo,
        notes=data.notes,
        started_at=data.started_at,
        finished_at=data.finished_at,
    )
    db.add(entry)
    await db.flush()  # Get the entry.id

    # Add emotions
    for emo in data.emotions:
        entry_emotion = EntryEmotion(
            entry_id=entry.id,
            emotion_id=emo.emotion_id,
            strength=emo.strength,
        )
        db.add(entry_emotion)

    await db.flush()

    # Reload with emotions
    return await get_entry_by_id(db, entry.id, user_id)


async def get_entry_by_id(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID
) -> BookEntry | None:
    """Get a single entry (with emotions) belonging to a user."""
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.id == entry_id, BookEntry.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_entries(
    db: AsyncSession, user_id: uuid.UUID, page: int = 1, per_page: int = 20
) -> tuple[list[BookEntry], int]:
    """List a user's entries with pagination. Returns (entries, total_count)."""
    # Count
    count_result = await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.user_id == user_id)
    )
    total = count_result.scalar_one()

    # Fetch
    offset = (page - 1) * per_page
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    entries = list(result.scalars().all())

    return entries, total


async def update_entry(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID, data: EntryUpdate
) -> BookEntry | None:
    """Update an existing entry. Returns None if not found."""
    entry = await get_entry_by_id(db, entry_id, user_id)
    if not entry:
        return None

    # Update scalar fields
    update_data = data.model_dump(exclude_unset=True, exclude={"emotions"})
    for field, value in update_data.items():
        setattr(entry, field, value)

    # Update emotions if provided
    if data.emotions is not None:
        # Remove existing emotions
        for existing in entry.emotions:
            await db.delete(existing)
        await db.flush()

        # Add new emotions
        new_emotions = []
        for emo in data.emotions:
            entry_emotion = EntryEmotion(
                entry_id=entry.id,
                emotion_id=emo.emotion_id,
                strength=emo.strength,
            )
            db.add(entry_emotion)
            new_emotions.append(entry_emotion)

        await db.flush()

    # Reload
    return await get_entry_by_id(db, entry_id, user_id)


async def delete_entry(db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Delete an entry. Returns True if deleted, False if not found."""
    entry = await get_entry_by_id(db, entry_id, user_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.flush()
    return True
