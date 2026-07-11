import uuid
from datetime import date, datetime

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry, EntryEmotion
from app.schemas.entry import EntryCreate, EntryUpdate
from app.utils.emotions import canonicalize


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


def _default_finished_at(status: str, finished_at: date | None) -> date | None:
    """A finished book needs a finished_at so the calendar/mirror can key on it
    (P5-7). If the client didn't supply one, default to today; non-finished
    statuses carry no finish date.
    """
    if status == "finished":
        return finished_at or date.today()
    return finished_at


async def create_entry(db: AsyncSession, user_id: uuid.UUID, data: EntryCreate) -> BookEntry:
    """Create a new book entry with emotions."""
    status = data.status or "finished"
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
        status=status,
        started_at=data.started_at,
        finished_at=_default_finished_at(status, data.finished_at),
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
    q: str | None = None,
    emotion: str | None = None,
) -> tuple[list[BookEntry], int, str | None]:
    """
    List a user's entries. Returns (entries, total_count, next_cursor).

    Supports two modes:
    - Cursor-based (preferred): pass cursor from previous response's next_cursor
    - Offset-based (legacy): pass page + per_page

    Optional in-library filters (B2.9): `q` matches title/author (case-insensitive
    substring); `emotion` keeps only entries tagged with that (canonicalized) slug.
    Both filters apply to the total count and the keyset window alike.

    Entries are ordered newest-first with a stable tie-break: (created_at, id) DESC.
    The cursor encodes both fields so entries sharing a timestamp (bulk imports,
    same-second creates) are neither skipped nor duplicated across pages.

    Raises InvalidCursor on a malformed cursor (router maps to 400) rather than
    silently restarting from page 1 — that silent restart made clients loop.
    """
    # Filters shared by the count and the page query.
    filters = [BookEntry.user_id == user_id]
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(BookEntry.title.ilike(like), BookEntry.author.ilike(like)))
    if emotion:
        canon = canonicalize(emotion) or emotion
        filters.append(BookEntry.emotions.any(EntryEmotion.emotion_id == canon))

    # One COUNT for the total (part of the response contract / shelf display).
    # The former second COUNT (has_next) is gone: we over-fetch by one row instead.
    count_result = await db.execute(select(func.count(BookEntry.id)).where(*filters))
    total = count_result.scalar_one()

    query = (
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(*filters)
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

    # Keep finished_at consistent with status (P5-7): a book that just became
    # finished gets today's date if none was supplied; clearing to a non-finished
    # status drops the finish date.
    if "status" in update_data or "finished_at" in update_data:
        if entry.status == "finished" and entry.finished_at is None:
            entry.finished_at = date.today()
        elif entry.status != "finished" and "status" in update_data and "finished_at" not in update_data:
            entry.finished_at = None

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
    """Mark an entry finished: populate arc, thought, intensity; add any arc
    emotions not already tagged. Ownership is enforced by get_entry_by_id.
    """
    entry = await get_entry_by_id(db, entry_id, user_id)
    if not entry:
        return None

    # Canonicalize arc slugs so legacy inputs still land on the right emotion.
    start_slug = canonicalize(start_slug) or start_slug
    middle_slug = canonicalize(middle_slug) or middle_slug
    end_slug = canonicalize(end_slug) or end_slug

    entry.status = "finished"
    entry.arc_start_emotion_id = start_slug
    entry.arc_middle_emotion_id = middle_slug
    entry.arc_end_emotion_id = end_slug
    entry.finish_thought = thought
    entry.intensity = intensity
    if entry.finished_at is None:
        entry.finished_at = date.today()

    # Add arc emotions that weren't already tagged, but DO NOT overwrite the
    # per-emotion strengths the user set at creation (P2-4). New arc emotions
    # default to the overall finish intensity.
    existing_by_slug = {e.emotion_id: e for e in entry.emotions}
    for slug in {start_slug, middle_slug, end_slug}:
        if slug not in existing_by_slug:
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