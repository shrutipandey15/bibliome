import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter

entries_limiter = RateLimiter(max_requests=60, window_seconds=60, prefix="entries")
from app.models.book_entry import BookEntry, EntryEmotion
from app.models.user import User
from app.schemas.entry import (
    EntryCreate,
    EntryListResponse,
    EntryResponse,
    EntryUpdate,
    EmotionOut,
)
from app.services.entry_service import (
    create_entry,
    delete_entry,
    get_entry_by_id,
    list_entries,
    update_entry,
)
from app.services.background import recalculate_dna
from app.services.book_search import bump_popularity
from app.services.room_decorations import compute_unlocks

logger = logging.getLogger("bookdna.entries")

async def _feed_catalog(title: str, author: str | None, cover_url: str | None, isbn: str | None):
    """Background task: add/update book in catalog using its own db session."""
    try:
        async with async_session() as db:
            await bump_popularity(db, title, author, cover_url, isbn)
            await db.commit()
    except Exception as e:
        logger.debug("Catalog feed failed (non-critical): %s", e)


router = APIRouter(prefix="/entries", tags=["entries"])


def _entry_to_response(entry) -> EntryResponse:
    """Convert a BookEntry ORM object to an EntryResponse schema."""
    return EntryResponse(
        id=entry.id,
        title=entry.title,
        author=entry.author,
        cover_url=entry.cover_url,
        isbn=entry.isbn,
        intensity=entry.intensity,
        quote=entry.quote,
        public_echo=entry.public_echo,
        notes=entry.notes,
        emotions=[
            EmotionOut(emotion_id=e.emotion_id, strength=e.strength)
            for e in entry.emotions
        ],
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get("", response_model=EntryListResponse)
async def get_entries(
    cursor: str | None = Query(default=None, description="ISO timestamp cursor from previous next_cursor"),
    limit: int = Query(default=20, ge=1, le=100),
    page: int | None = Query(default=None, ge=1, description="Legacy offset pagination"),
    per_page: int | None = Query(default=None, ge=1, le=100, description="Legacy offset pagination"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List book entries. Supports cursor-based (preferred) and offset-based (legacy) pagination.

    Cursor mode: pass `cursor` from previous response's `next_cursor`.
    Offset mode: pass `page` + `per_page` (backward compat).
    """
    entries, total, next_cursor = await list_entries(
        db, current_user.id,
        limit=limit,
        cursor=cursor,
        page=page,
        per_page=per_page,
    )
    return EntryListResponse(
        entries=[_entry_to_response(e) for e in entries],
        total=total,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
        page=page,
        per_page=per_page,
    )


@router.post("", response_model=EntryResponse, status_code=status.HTTP_201_CREATED)
async def create_new_entry(
    request: Request,
    data: EntryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new book entry."""
    await entries_limiter.check(request)
    entry = await create_entry(db, current_user.id, data)
    current_user.dna_dirty = True
    await db.flush()

    # Piggyback: check for new room decoration unlocks
    room_unlocks_new = []
    if current_user.room_unlocks is not None:
        old_set = set(current_user.room_unlocks)
        r = await db.execute(
            select(func.count(BookEntry.id)).where(BookEntry.user_id == current_user.id)
        )
        entry_count = r.scalar() or 0

        has_i10 = data.intensity == 10
        has_2am = any(e.emotion_id == "2am" for e in data.emotions)

        if not has_i10:
            r2 = await db.execute(
                select(func.count(BookEntry.id)).where(
                    BookEntry.user_id == current_user.id, BookEntry.intensity == 10
                )
            )
            has_i10 = (r2.scalar() or 0) > 0
        if not has_2am:
            r3 = await db.execute(
                select(func.count(EntryEmotion.id))
                .join(BookEntry, EntryEmotion.entry_id == BookEntry.id)
                .where(BookEntry.user_id == current_user.id, EntryEmotion.emotion_id == "2am")
            )
            has_2am = (r3.scalar() or 0) > 0

        updated = compute_unlocks(current_user, entry_count, has_i10, has_2am)
        room_unlocks_new = [u for u in updated if u not in old_set]
        if room_unlocks_new:
            current_user.room_unlocks = updated

    # Background: feed catalog + recalculate DNA (separate sessions, can't break entry)
    asyncio.create_task(_feed_catalog(entry.title, entry.author, entry.cover_url, entry.isbn))
    asyncio.create_task(recalculate_dna(current_user.id))
    response = _entry_to_response(entry)
    response.room_unlocks_new = room_unlocks_new
    return response


@router.get("/{entry_id}", response_model=EntryResponse)
async def get_single_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single book entry."""
    entry = await get_entry_by_id(db, entry_id, current_user.id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return _entry_to_response(entry)


@router.put("/{entry_id}", response_model=EntryResponse)
async def update_existing_entry(
    request: Request,
    entry_id: uuid.UUID,
    data: EntryUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a book entry."""
    await entries_limiter.check(request)
    entry = await update_entry(db, entry_id, current_user.id, data)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    current_user.dna_dirty = True
    await db.flush()
    asyncio.create_task(recalculate_dna(current_user.id))
    return _entry_to_response(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_existing_entry(
    request: Request,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a book entry."""
    await entries_limiter.check(request)
    deleted = await delete_entry(db, entry_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    current_user.dna_dirty = True
    await db.flush()
    asyncio.create_task(recalculate_dna(current_user.id))