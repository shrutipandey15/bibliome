import uuid
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry, EntryEmotion
from app.schemas.entry import EntryCreate, EntryUpdate


class InvalidCursor(ValueError):
    """Raised when a pagination cursor cannot be parsed. Router maps this to 400."""


def _encode_cursor(entry: BookEntry) -> str:
    """Opaque keyset cursor: created_at + id, so ties on created_at are stable."""
    return f"{entry.created_at.isoformat()}|{entry.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts_raw, id_raw = cursor.rsplit("|", 1)
        return datetime.fromisoformat(ts_raw), uuid.UUID(id_raw)
    except (ValueError, AttributeError):
        raise InvalidCursor(f"Malformed pagination cursor: {cursor!r}")


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

    Entries are ordered newest-first with a stable tie-break: (created_at, id) DESC.
    The cursor encodes both fields so entries sharing a timestamp (bulk imports,
    same-second creates) are neither skipped nor duplicated across pages.

    Raises InvalidCursor on a malformed cursor (router maps to 400) rather than
    silently restarting from page 1 — that silent restart made clients loop.
    """
    # One COUNT for the total (part of the response contract / shelf display).
    # The former second COUNT (has_next) is gone: we over-fetch by one row instead.
    count_result = await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.user_id == user_id)
    )
    total = count_result.scalar_one()

    query = (
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.desc(), BookEntry.id.desc())
    )

    legacy_offset = cursor is None and page is not None and per_page is not None
    if legacy_offset:
        # Legacy offset mode (kept for backward compat).
        offset = (page - 1) * per_page
        result = await db.execute(query.offset(offset).limit(per_page))
        entries = list(result.scalars().all())
        return entries, total, None

    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        # Composite keyset: strictly "older" than the cursor row in (created_at, id).
        query = query.where(
            tuple_(BookEntry.created_at, BookEntry.id) < (cursor_ts, cursor_id)
        )

    # Over-fetch one row to detect a next page without a second COUNT.
    result = await db.execute(query.limit(limit + 1))
    rows = list(result.scalars().all())

    has_next = len(rows) > limit
    entries = rows[:limit]
    next_cursor = _encode_cursor(entries[-1]) if has_next and entries else None

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