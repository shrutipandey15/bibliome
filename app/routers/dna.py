import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session, get_db
from app.middleware.auth import get_current_user, get_current_user_id
from app.middleware.rate_limit import RateLimiter, generate_limiter

dna_read_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="dna_read")
from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.user import User
from app.schemas.dna import (
    BlindSpotsResponse,
    DNAGenerateResponse,
    DNAProfileResponse,
    DNASnapshotResponse,
    EmotionalCalendarResponse,
    HeatmapResponse,
    PersonalityInfo,
    RecapResponse,
    StatsResponse,
    TwinResponse,
)
from app.services.blind_spots_service import get_blind_spots
from app.services.calendar_service import get_emotional_calendar
from app.services.dna_engine import (
    build_heatmap_data,
    calculate_personality,
    dna_type_slug_for,
    find_twins,
    generate_recap,
    generate_stats,
)
from app.services.room_decorations import compute_unlocks
from app.utils.cache import dna_cache, invalidate_dna
from app.utils.emotions import TWO_AM_SLUGS

router = APIRouter(prefix="/dna", tags=["dna"])


async def _get_user_entries(db: AsyncSession, user_id) -> list[dict]:
    """Fetch all entries for a user and convert to dicts for the engine."""
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.asc())
    )
    entries = result.scalars().all()

    return [
        {
            "id": str(e.id),
            "title": e.title,
            "author": e.author,
            "intensity": e.intensity,
            "emotions": [em.emotion_id for em in e.emotions],
            "created_at": e.created_at,
            "finished_at": e.finished_at,
        }
        for e in entries
    ]


@router.get("/profile", response_model=DNAProfileResponse)
async def get_dna_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the current DNA profile. Uses cached result if entries haven't changed.
    Recalculates and caches when dna_dirty is True.
    """
    # Serve from cache if clean
    if not current_user.dna_dirty and current_user.cached_dna_profile:
        return current_user.cached_dna_profile

    # Recalculate
    entries = await _get_user_entries(db, current_user.id)
    result = calculate_personality(entries)
    result["book_count"] = len(entries)

    # Cache it
    current_user.cached_dna_profile = result
    current_user.dna_dirty = False
    await db.flush()

    return result


@router.post("/generate", response_model=DNAGenerateResponse)
async def generate_dna(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Calculate DNA and save a snapshot.
    Minimum 3 books required.
    """
    await generate_limiter.check(request)
    entries = await _get_user_entries(db, current_user.id)

    if len(entries) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least 3 books to generate DNA. You have {len(entries)}.",
        )

    result = calculate_personality(entries)

    if not result["personality"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine personality. Add more emotion tags to your entries.",
        )

    personality = result["personality"]
    now = datetime.now(timezone.utc)

    # Save snapshot
    snapshot = DNASnapshot(
        user_id=current_user.id,
        personality_type=personality["name"],
        dna_type_slug=dna_type_slug_for(personality["id"]),
        emotion_data={
            "frequency": result["emotion_frequency"],
            "intensity": result["emotion_intensity"],
            "top_emotions": result["top_emotions"],
            "avoided": result["avoided_emotions"],
            "co_occurrence": result["co_occurrence"],
            "scores": result["scores"],
        },
        book_count=len(entries),
        year=now.year,
    )
    db.add(snapshot)

    # Update user's cached personality type and DNA cache
    current_user.personality_type = personality["name"]
    current_user.cached_dna_profile = result
    current_user.cached_dna_profile["book_count"] = len(entries)
    current_user.dna_dirty = False

    await db.flush()

    await invalidate_dna(current_user.id)

    # Piggyback: check for new room decoration unlocks (glyph_figurine unlocks on first DNA gen)
    room_unlocks_new = []
    if current_user.room_unlocks is not None:
        old_set = set(current_user.room_unlocks)
        from sqlalchemy import func, select
        from app.models.book_entry import BookEntry, EntryEmotion
        r_i10 = await db.execute(
            select(func.count(BookEntry.id)).where(
                BookEntry.user_id == current_user.id, BookEntry.intensity == 10
            )
        )
        has_i10 = (r_i10.scalar() or 0) > 0
        r_2am = await db.execute(
            select(func.count(EntryEmotion.id))
            .join(BookEntry, EntryEmotion.entry_id == BookEntry.id)
            .where(BookEntry.user_id == current_user.id, EntryEmotion.emotion_id.in_(TWO_AM_SLUGS))
        )
        has_2am = (r_2am.scalar() or 0) > 0
        updated = compute_unlocks(current_user, len(entries), has_i10, has_2am)
        merged = old_set | set(updated)
        room_unlocks_new = sorted(merged - old_set)
        if room_unlocks_new:
            current_user.room_unlocks = sorted(merged)

    return DNAGenerateResponse(
        snapshot=DNASnapshotResponse(
            id=snapshot.id,
            personality_type=snapshot.personality_type,
            emotion_data=snapshot.emotion_data,
            book_count=snapshot.book_count,
            year=snapshot.year,
            generated_at=snapshot.generated_at or now,
        ),
        personality=PersonalityInfo(**personality),
        room_unlocks_new=room_unlocks_new,
    )


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Get the emotion x book heatmap matrix data."""
    await dna_read_limiter.check(request)
    cache_key = f"heatmap:{user_id}"

    # Cache hit: zero DB connections needed
    cached = await dna_cache.get(cache_key)
    if cached:
        return cached

    # Cache miss: open DB only when necessary
    async with async_session() as db:
        entries = await _get_user_entries(db, user_id)
    result = build_heatmap_data(entries)
    await dna_cache.set(cache_key, result)
    return result


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
):
    """Get reading statistics."""
    await dna_read_limiter.check(request)
    cache_key = f"stats:{user_id}"

    # Cache hit: zero DB connections needed
    cached = await dna_cache.get(cache_key)
    if cached:
        return cached

    # Cache miss: open DB only when necessary
    async with async_session() as db:
        entries = await _get_user_entries(db, user_id)
    result = generate_stats(entries)
    await dna_cache.set(cache_key, result)
    return result


@router.get("/history", response_model=list[DNASnapshotResponse])
async def get_dna_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get past DNA snapshots (for yearly Wrapped-style comparisons)."""
    result = await db.execute(
        select(DNASnapshot)
        .where(DNASnapshot.user_id == current_user.id)
        .order_by(DNASnapshot.generated_at.desc())
    )
    snapshots = result.scalars().all()
    return snapshots


@router.get("/recap", response_model=RecapResponse)
async def get_monthly_recap(
    month: str = Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM format"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a monthly recap — aggregated stats for a specific month.
    Includes books logged, emotion breakdown, new emotions discovered,
    and whether your personality type shifted.

    Usage: GET /api/dna/recap?month=2026-02
    """
    from datetime import datetime, timezone
    from calendar import monthrange

    # Parse month
    try:
        year, mo = int(month[:4]), int(month[5:7])
        month_start = datetime(year, mo, 1, tzinfo=timezone.utc)
        last_day = monthrange(year, mo)[1]
        month_end = datetime(year, mo, last_day, 23, 59, 59, tzinfo=timezone.utc)
    except (ValueError, IndexError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid month format. Use YYYY-MM (e.g. 2026-02).",
        )

    # Fetch all user entries
    all_entries = await _get_user_entries(db, current_user.id)

    # Split into month entries and prior entries
    month_entries = []
    prior_entries = []
    for e in all_entries:
        created = e.get("created_at")
        if not created:
            continue
        if month_start <= created <= month_end:
            month_entries.append(e)
        elif created < month_start:
            prior_entries.append(e)

    recap = generate_recap(
        month_entries=month_entries,
        prior_entries=prior_entries,
        current_personality=current_user.personality_type,
    )
    recap["month"] = month

    return recap


@router.get("/twin", response_model=TwinResponse)
async def get_reading_twin(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Find your reading twin — public users with the most similar emotion profiles.
    Uses cosine similarity on emotion frequency vectors.
    Requires at least 3 books.
    """
    # Get current user's entries
    user_entries = await _get_user_entries(db, current_user.id)

    if len(user_entries) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least 3 books to find twins. You have {len(user_entries)}.",
        )

    # Build current user's emotion frequency
    from collections import Counter
    user_freq = Counter()
    for entry in user_entries:
        for emo in entry["emotions"]:
            user_freq[emo] += 1

    user_top = [emo for emo, _ in user_freq.most_common(5)]

    # Fetch all public users (excluding self) who have entries
    public_users_result = await db.execute(
        select(User)
        .where(User.profile_visibility == "public", User.id != current_user.id)
    )
    public_users = public_users_result.scalars().all()

    # Build candidate profiles
    candidates = []
    for pu in public_users:
        pu_entries = await _get_user_entries(db, pu.id)
        if len(pu_entries) < 3:
            continue

        pu_freq = Counter()
        for entry in pu_entries:
            for emo in entry["emotions"]:
                pu_freq[emo] += 1

        candidates.append({
            "username": pu.username,
            "display_name": pu.display_name,
            "personality_type": pu.personality_type,
            "emotion_frequency": dict(pu_freq),
        })

    twins = find_twins(dict(user_freq), candidates, max_results=5)

    return TwinResponse(
        twins=twins,
        your_top_emotions=user_top,
        total_public_users_searched=len(candidates),
    )


@router.get("/emotional-calendar", response_model=EmotionalCalendarResponse)
async def emotional_calendar(
    months: int = Query(default=6, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-month emotion distribution over the last N months. Weights sum to 1.0 per month."""
    return await get_emotional_calendar(db, current_user.id, months)


@router.get("/blind-spots", response_model=BlindSpotsResponse)
async def blind_spots(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Up to 3 emotions the user under-tags or has never tagged. Requires >=5 entries."""
    return await get_blind_spots(db, current_user.id)