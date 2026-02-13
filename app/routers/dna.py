from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.user import User
from app.schemas.dna import (
    DNAGenerateResponse,
    DNAProfileResponse,
    DNASnapshotResponse,
    HeatmapResponse,
    PersonalityInfo,
    StatsResponse,
)
from app.services.dna_engine import (
    build_heatmap_data,
    calculate_personality,
    generate_stats,
)

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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Calculate DNA and save a snapshot.
    Minimum 3 books required.
    """
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
    )


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the emotion x book heatmap matrix data."""
    entries = await _get_user_entries(db, current_user.id)
    return build_heatmap_data(entries)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get reading statistics."""
    entries = await _get_user_entries(db, current_user.id)
    return generate_stats(entries)


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