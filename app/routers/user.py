import secrets
import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.book_entry import BookEntry, EntryEmotion
from app.models.user import User
from app.schemas.user import (
    PasswordChangeRequest,
    RoomLayoutUpdate,
    RoomResponse,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from app.services.auth_service import hash_password, verify_password
from app.services.room_decorations import (
    VALID_DECO_IDS,
    build_decoration_catalog,
    compute_unlocks,
)
from app.utils.cache import room_cache
from app.utils.emotions import TWO_AM_SLUGS

router = APIRouter(prefix="/user", tags=["user"])

room_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="room")

CURRENT_ROOM_VERSION = 1


def migrate_room_layout(layout: dict | None) -> dict | None:
    """Migrate old room layouts to current version. Pure function."""
    if layout is None:
        return None
    v = layout.get("version", 0)
    if v == CURRENT_ROOM_VERSION:
        return layout
    # Future: add per-version migration logic here.
    # Return a copy so callers can treat this as pure (no side effects on read).
    return {**layout, "version": CURRENT_ROOM_VERSION}


async def _count_entries(db: AsyncSession, user_id) -> int:
    result = await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.user_id == user_id)
    )
    return result.scalar() or 0


async def _check_special_unlocks(db: AsyncSession, user_id) -> tuple[bool, bool]:
    """Check for intensity-10 and 2am-tagged entries."""
    i10 = await db.execute(
        select(func.count(BookEntry.id)).where(
            BookEntry.user_id == user_id,
            BookEntry.intensity == 10,
        )
    )
    has_i10 = (i10.scalar() or 0) > 0

    am = await db.execute(
        select(func.count(EntryEmotion.id))
        .join(BookEntry, EntryEmotion.entry_id == BookEntry.id)
        .where(BookEntry.user_id == user_id, EntryEmotion.emotion_id.in_(TWO_AM_SLUGS))
    )
    has_2am = (am.scalar() or 0) > 0

    return has_i10, has_2am


async def _clean_stale_books(db: AsyncSession, user_id, layout: dict | None) -> dict | None:
    """Remove deleted book references from room layout."""
    if not layout or not layout.get("shelves"):
        return layout

    book_ids = set()
    for shelf in layout["shelves"]:
        for item in shelf:
            if item.get("type") == "book":
                book_ids.add(item["id"])

    if not book_ids:
        return layout

    valid_uuids = []
    for bid in book_ids:
        try:
            valid_uuids.append(uuid_mod.UUID(bid))
        except ValueError:
            continue

    result = await db.execute(
        select(BookEntry.id).where(
            BookEntry.user_id == user_id,
            BookEntry.id.in_(valid_uuids),
        )
    )
    existing_ids = {str(r) for r in result.scalars().all()}

    cleaned = False
    new_shelves = []
    for shelf in layout["shelves"]:
        new_shelf = []
        for item in shelf:
            if item.get("type") == "book" and item["id"] not in existing_ids:
                cleaned = True
                continue
            new_shelf.append(item)
        new_shelves.append(new_shelf)

    if cleaned:
        layout = {**layout, "shelves": new_shelves}

    return layout


@router.get("/settings", response_model=UserSettingsResponse)
async def get_settings(current_user: User = Depends(get_current_user)):
    """Get current user settings."""
    return UserSettingsResponse(
        display_name=current_user.display_name,
        is_public=current_user.is_public,
        personality_type=current_user.personality_type,
        username=current_user.username,
        email=current_user.email,
    )


@router.patch("/settings", response_model=UserSettingsResponse)
async def update_settings(
    data: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update user settings (display name)."""
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if hasattr(current_user, field):
            setattr(current_user, field, value)

    await db.flush()

    return UserSettingsResponse(
        display_name=current_user.display_name,
        is_public=current_user.is_public,
        personality_type=current_user.personality_type,
        username=current_user.username,
        email=current_user.email,
    )


@router.post("/change-password")
async def change_password(
    data: PasswordChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change password. Requires current password for verification."""
    if not await verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = await hash_password(data.new_password)
    await db.flush()

    return {"message": "Password updated"}


@router.post("/share-token")
async def generate_share_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate (or reset) a secure share token for the current user."""
    token = secrets.token_urlsafe(16)
    current_user.share_token = token

    # Piggyback: check for new room unlocks (mini_dna_frame unlocks on first share)
    room_unlocks_new = []
    if current_user.room_unlocks is not None:
        old_set = set(current_user.room_unlocks)
        entry_count = await _count_entries(db, current_user.id)
        has_i10, has_2am = await _check_special_unlocks(db, current_user.id)
        updated = compute_unlocks(current_user, entry_count, has_i10, has_2am)
        room_unlocks_new = [u for u in updated if u not in old_set]
        if room_unlocks_new:
            current_user.room_unlocks = updated

    await db.commit()
    return {"share_token": token, "room_unlocks_new": room_unlocks_new}


@router.get("/room", response_model=RoomResponse)
async def get_room(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the user's reading room layout and decoration unlocks.

    Read-only: unlock init, layout migration, and stale-book pruning are all
    computed in-memory for the response but never written here (B1.5). The
    persisted state is refreshed by the write endpoints that recompute unlocks
    (POST /entries, POST /dna/generate, POST /user/share-token) and by PATCH
    /user/room, which stamps the current layout version.
    """
    await room_limiter.check(request)

    # Compute unlocks in-memory if never initialized (do not persist on read).
    unlocks = current_user.room_unlocks
    if unlocks is None:
        entry_count = await _count_entries(db, current_user.id)
        has_i10, has_2am = await _check_special_unlocks(db, current_user.id)
        unlocks = compute_unlocks(current_user, entry_count, has_i10, has_2am)

    # Migrate + prune stale books for the response only (pure, no writes).
    layout = migrate_room_layout(current_user.room_layout)
    layout = await _clean_stale_books(db, current_user.id, layout)

    catalog = build_decoration_catalog(unlocks)

    return RoomResponse(
        version=CURRENT_ROOM_VERSION,
        layout=layout,
        unlocks=unlocks or [],
        decorations=catalog,
    )


@router.patch("/room")
async def update_room(
    request: Request,
    data: RoomLayoutUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the user's room arrangement."""
    await room_limiter.check(request)

    # Validate all placed decorations are unlocked
    unlocked = set(current_user.room_unlocks or [])
    for shelf in data.shelves:
        for item in shelf:
            if item.type == "deco":
                if item.id not in VALID_DECO_IDS:
                    raise HTTPException(400, f"Unknown decoration '{item.id}'")
                if item.id not in unlocked:
                    raise HTTPException(403, f"Decoration '{item.id}' is locked")

    current_user.room_layout = {
        "version": CURRENT_ROOM_VERSION,
        **data.model_dump(),
    }
    await db.flush()

    # Invalidate public cache
    await room_cache.invalidate(f"public:{current_user.username}")

    return {"status": "saved"}
