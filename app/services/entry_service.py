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
        cover_url=data.cover_url,
        isbn=data.isbn,
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
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 20,
    cursor: str | None = None,
    page: int | None = None,
    per_page: int | None = None,
) -> tuple[list[BookEntry], int, str | None]:
    """
    List a user's entries. Returns (entries, total_count, next_cursor).

    Supports two modes:
    - Cursor-based (preferred): pass cursor from previous response's next_cursor
    - Offset-based (legacy): pass page + per_page

    Entries are ordered newest-first (created_at DESC).
    Cursor is the created_at ISO timestamp of the last entry in the batch.
    """
    # Count
    count_result = await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.user_id == user_id)
    )
    total = count_result.scalar_one()

    # Build query
    query = (
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.desc(), BookEntry.id.desc())
    )

    if cursor:
        # Cursor = ISO timestamp — fetch entries older than this
        from datetime import datetime, timezone
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError:
            cursor_dt = None

        if cursor_dt:
            query = query.where(BookEntry.created_at < cursor_dt)
        query = query.limit(limit)
    elif page is not None and per_page is not None:
        # Legacy offset mode
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)
    else:
        query = query.limit(limit)

    result = await db.execute(query)
    entries = list(result.scalars().all())

    # Build next cursor from last entry
    next_cursor = None
    if entries:
        last = entries[-1]
        remaining = total - (len(entries) if not cursor and not page else 0)
        # Check if there are more entries after this batch
        has_next_result = await db.execute(
            select(func.count(BookEntry.id)).where(
                BookEntry.user_id == user_id,
                BookEntry.created_at < last.created_at,
            )
        )
        has_next = has_next_result.scalar_one() > 0
        if has_next:
            next_cursor = last.created_at.isoformat()

    return entries, total, next_cursor


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

    # Expire the entry to bust SQLAlchemy's identity map cache,
    # then reload fresh with eager-loaded emotions
    db.expire(entry)
    return await get_entry_by_id(db, entry_id, user_id)
    return entry


async def finish_entry(
    db: AsyncSession,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    start_slug: str,
    middle_slug: str,
    end_slug: str,
    thought: str | None,
    intensity: int,
) -> BookEntry | None:
    """Mark an entry finished: populate arc, thought, intensity; upsert arc emotions."""
    entry = await get_entry_by_id(db, entry_id, user_id)
    if not entry:
        return None

    entry.status = "finished"
    entry.arc_start_emotion_id = start_slug
    entry.arc_middle_emotion_id = middle_slug
    entry.arc_end_emotion_id = end_slug
    entry.finish_thought = thought
    entry.intensity = intensity

    existing_by_slug = {e.emotion_id: e for e in entry.emotions}
    for slug in {start_slug, middle_slug, end_slug}:
        if slug in existing_by_slug:
            existing_by_slug[slug].strength = intensity
        else:
            db.add(EntryEmotion(entry_id=entry.id, emotion_id=slug, strength=intensity))

    await db.flush()
    db.expire(entry)
    return await get_entry_by_id(db, entry_id, user_id)


async def delete_entry(db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Delete an entry. Returns True if deleted, False if not found."""
    entry = await get_entry_by_id(db, entry_id, user_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.flush()
    return True