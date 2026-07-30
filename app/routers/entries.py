import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter

entries_limiter = RateLimiter(max_requests=60, window_seconds=60, prefix="entries")
from app.models.book_entry import BookEntry
from app.models.user import User
from app.schemas.entry import (
    EntryCreate,
    EntryFinish,
    EntryListResponse,
    EntryResponse,
    EntryUpdate,
    EmotionOut,
    ImportResponse,
    ShelfPositionUpdate,
)
from app.services.import_service import MAX_IMPORT_BYTES, import_entries, parse_import_csv
from app.schemas.arc import ArcCardResponse
from app.schemas.checkin import CheckinCreate, CheckinResponse, StatusUpdate
from app.services.arc_card_service import get_arc_card
from app.services.entry_service import (
    InvalidCursor,
    create_entry,
    delete_entry,
    finish_entry,
    get_entry_by_id,
    list_entries,
    update_entry,
)
from app.services.checkin_service import (
    create_checkin,
    get_owned_entry,
    list_checkins,
    update_status,
)
from app.services.aggregate_service import refresh_book_aggregate
from app.services.background import recalculate_dna, recompute_resonance
from app.services.book_search import bump_popularity
from app.utils.emotions import VALID_SLUGS

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
        book_id=entry.book_id,
        title=entry.title,
        author=entry.author,
        cover_url=entry.cover_url,
        isbn=entry.isbn,
        intensity=entry.intensity,
        quote=entry.quote,
        notes=entry.notes,
        emotions=[
            EmotionOut(emotion_id=e.emotion_id, strength=e.strength)
            for e in entry.emotions
        ],
        started_at=entry.started_at,
        finished_at=entry.finished_at,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        status=entry.status,
        verdict=entry.verdict,
        dnf_reason=entry.dnf_reason,
        arc_start_emotion_id=entry.arc_start_emotion_id,
        arc_middle_emotion_id=entry.arc_middle_emotion_id,
        arc_end_emotion_id=entry.arc_end_emotion_id,
        finish_thought=entry.finish_thought,
    )


@router.get("", response_model=EntryListResponse)
async def get_entries(
    cursor: str | None = Query(default=None, description="Opaque cursor from previous next_cursor"),
    limit: int = Query(default=20, ge=1, le=100),
    page: int | None = Query(default=None, ge=1, description="Legacy offset pagination"),
    per_page: int | None = Query(default=None, ge=1, le=100, description="Legacy offset pagination"),
    q: str | None = Query(default=None, max_length=200, description="Filter by title/author substring"),
    emotion: str | None = Query(default=None, max_length=30, description="Filter by tagged emotion slug"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List book entries. Supports cursor-based (preferred) and offset-based (legacy) pagination,
    plus optional in-library `q` (title/author) and `emotion` filters.

    Cursor mode: pass `cursor` from previous response's `next_cursor`.
    Offset mode: pass `page` + `per_page` (backward compat).
    """
    try:
        entries, total, next_cursor = await list_entries(
            db, current_user.id,
            limit=limit,
            cursor=cursor,
            page=page,
            per_page=per_page,
            q=q,
            emotion=emotion,
        )
    except InvalidCursor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor.",
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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new book entry."""
    await entries_limiter.check(request)
    entry = await create_entry(db, current_user.id, data)
    current_user.dna_dirty = True
    await db.flush()

    # Background (post-commit, once get_db has committed this request's transaction):
    # feed the catalog and refresh DNA caches from the now-durable entry.
    background_tasks.add_task(_feed_catalog, entry.title, entry.author, entry.cover_url, entry.isbn)
    background_tasks.add_task(recalculate_dna, current_user.id)
    background_tasks.add_task(refresh_book_aggregate, entry.book_id)
    # New book + new emotions = new possible resonance. Batched here, never
    # computed on the read path.
    background_tasks.add_task(recompute_resonance, current_user.id)
    return _entry_to_response(entry)


@router.post("/import", response_model=ImportResponse)
async def import_library(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import a Goodreads / StoryGraph CSV export into the library (B2.7).

    Deduped against the existing shelf. Imported books carry no emotions.
    """
    await entries_limiter.check(request)

    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")
    try:
        content = raw.decode("utf-8-sig")  # tolerate a BOM
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be UTF-8 CSV")

    books, errors = parse_import_csv(content)
    imported, skipped, engaged_book_ids = await import_entries(db, current_user.id, books)

    if imported:
        current_user.dna_dirty = True
        await db.flush()
        background_tasks.add_task(recalculate_dna, current_user.id)
        for book_id in engaged_book_ids:
            background_tasks.add_task(refresh_book_aggregate, book_id)
        background_tasks.add_task(recompute_resonance, current_user.id)

    return ImportResponse(
        parsed=len(books),
        imported=imported,
        skipped=skipped,
        errors=errors[:50],
    )


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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a book entry."""
    await entries_limiter.check(request)
    # Retitling retargets book_id, so both the book it left and the book it
    # joined need recomputing (B8.3).
    previous = await get_entry_by_id(db, entry_id, current_user.id)
    previous_book_id = previous.book_id if previous else None

    entry = await update_entry(db, entry_id, current_user.id, data)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    current_user.dna_dirty = True
    await db.flush()
    background_tasks.add_task(recalculate_dna, current_user.id)
    background_tasks.add_task(refresh_book_aggregate, entry.book_id)
    if previous_book_id and previous_book_id != entry.book_id:
        background_tasks.add_task(refresh_book_aggregate, previous_book_id)
    background_tasks.add_task(recompute_resonance, current_user.id)
    return _entry_to_response(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_existing_entry(
    request: Request,
    entry_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a book entry."""
    await entries_limiter.check(request)
    # Capture the book before the row goes away — afterwards there is nothing to
    # read it from, and its aggregate still needs recomputing without this reader.
    doomed = await get_entry_by_id(db, entry_id, current_user.id)
    book_id = doomed.book_id if doomed else None

    deleted = await delete_entry(db, entry_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    current_user.dna_dirty = True
    await db.flush()
    background_tasks.add_task(recalculate_dna, current_user.id)
    background_tasks.add_task(refresh_book_aggregate, book_id)


@router.get("/{entry_id}/arc-card", response_model=ArcCardResponse)
async def get_entry_arc_card(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Arc card data: title/author, dna_type, ordered arc beats, intensity, thought."""
    card = await get_arc_card(db, entry_id, current_user.id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return card


# REMOVED (Phase 5 B5.7): GET /{entry_id}/arc-card/og. Server-side OG rendering
# is gone entirely now — the share-token card that outlived this one was retired
# too, and the frontend rasterises the DNA card locally with html2canvas. The
# arc-card *data* endpoint stays.


@router.post("/{entry_id}/finish", response_model=EntryResponse)
async def finish_existing_entry(
    request: Request,
    entry_id: uuid.UUID,
    data: EntryFinish,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark an entry finished with a three-beat emotional arc."""
    await entries_limiter.check(request)

    slugs = {data.start_emotion_slug, data.middle_emotion_slug, data.end_emotion_slug}
    invalid = [s for s in slugs if s not in VALID_SLUGS]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid emotion slug(s): {invalid}",
        )

    entry = await finish_entry(
        db,
        entry_id,
        current_user.id,
        data.start_emotion_slug,
        data.middle_emotion_slug,
        data.end_emotion_slug,
        data.thought,
        data.intensity,
    )
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    current_user.dna_dirty = True
    await db.flush()
    background_tasks.add_task(recalculate_dna, current_user.id)
    # Finishing is the moment an entry becomes engaged data for the aggregate.
    background_tasks.add_task(refresh_book_aggregate, entry.book_id)
    # ...and the moment it can resonate with another reader.
    background_tasks.add_task(recompute_resonance, current_user.id)
    return _entry_to_response(entry)


@router.post(
    "/{entry_id}/checkins",
    response_model=CheckinResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry_checkin(
    request: Request,
    entry_id: uuid.UUID,
    data: CheckinCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record a mid-read emotional check-in against an entry."""
    await entries_limiter.check(request)

    if data.emotion_slug not in VALID_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid emotion_slug '{data.emotion_slug}'",
        )

    entry = await get_owned_entry(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    checkin = await create_checkin(db, entry.id, data.emotion_slug, data.note)
    return CheckinResponse(
        id=checkin.id,
        entry_id=checkin.entry_id,
        emotion_slug=checkin.emotion_id,
        note=checkin.note,
        created_at=checkin.created_at,
    )


@router.get("/{entry_id}/checkins", response_model=list[CheckinResponse])
async def list_entry_checkins(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List an entry's mid-read check-ins (oldest first). Owner only."""
    entry = await get_owned_entry(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    checkins = await list_checkins(db, entry.id)
    return [
        CheckinResponse(
            id=c.id,
            entry_id=c.entry_id,
            emotion_slug=c.emotion_id,
            note=c.note,
            created_at=c.created_at,
        )
        for c in checkins
    ]


@router.patch("/{entry_id}/shelf-position", response_model=EntryResponse)
async def patch_entry_shelf_position(
    request: Request,
    entry_id: uuid.UUID,
    data: ShelfPositionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set the user-arranged shelf position for this entry."""
    await entries_limiter.check(request)

    entry = await get_owned_entry(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    entry.shelf_position = data.shelf_position
    await db.flush()
    entry = await get_entry_by_id(db, entry_id, current_user.id)
    return _entry_to_response(entry)


@router.patch("/{entry_id}/status", response_model=EntryResponse)
async def patch_entry_status(
    request: Request,
    entry_id: uuid.UUID,
    data: StatusUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change an entry's reading status (want_to_read / reading / finished)."""
    await entries_limiter.check(request)

    entry = await get_owned_entry(db, entry_id, current_user.id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    await update_status(db, entry, data.status)
    # A status change moves the entry in or out of the engaged set, which changes
    # both the reader's own profile and what the book's aggregate is computed
    # over (B8.3).
    current_user.dna_dirty = True
    await db.flush()
    background_tasks.add_task(recalculate_dna, current_user.id)
    background_tasks.add_task(refresh_book_aggregate, entry.book_id)
    background_tasks.add_task(recompute_resonance, current_user.id)
    # Reload via the eager path so emotions are loaded for the serializer.
    entry = await get_entry_by_id(db, entry_id, current_user.id)
    return _entry_to_response(entry)