"""Public surface — reduced to the one legitimate case (Phase 5 B5.1/B5.7).

The old username/entry-based public endpoints (stream, echoes, card, room, per-echo
images) are gone — they bypassed the visibility spine. What remains is the
share-token DNA card: a revocable, opt-in capability link the user creates for
themselves (visibility spine, B2.1). Echo is the one real public surface.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.rate_limit import RateLimiter
from app.models.book_entry import BookEntry
from app.models.user import User
from app.services.dna_engine import calculate_personality
from app.services.og_image import generate_dna_card_image
from app.services.visibility import resolve_share_token

router = APIRouter(prefix="/public", tags=["public"])

# Unauthenticated + heavy (PIL render, DNA compute) → rate-limited (audit P1-6).
public_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="public")


async def _generate_card_image_for_user(user: User, db: AsyncSession) -> Response:
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user.id)
        .order_by(BookEntry.created_at.asc())
    )
    entries = result.scalars().all()

    entry_dicts = [
        {
            "id": str(e.id),
            "title": e.title,
            "author": e.author,
            "intensity": e.intensity,
            "emotions": [em.emotion_id for em in e.emotions],
            "created_at": e.created_at,
        }
        for e in entries
    ]

    dna = calculate_personality(entry_dicts)
    personality = dna.get("personality")
    if not personality:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No DNA profile generated yet")

    image_bytes = await asyncio.to_thread(
        generate_dna_card_image,
        personality_name=personality["name"],
        personality_description=personality["description"],
        personality_color=personality["color"],
        personality_glyph=personality["glyph"],
        username=user.handle,  # pseudonymous handle, never the raw username
        book_count=len(entries),
        top_emotions=dna.get("top_emotions", []),
    )
    return Response(content=image_bytes, media_type="image/png")


@router.get("/shared/{token}/og")
async def get_shared_token_og_image(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """DNA card image for a revocable share token (the ShareModal download)."""
    await public_limiter.check(request)
    user = await resolve_share_token(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link invalid or expired")
    return await _generate_card_image_for_user(user, db)


@router.get("/shared/{token}")
async def get_shared_card(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """DNA profile via a revocable share token — the one way to share a profile."""
    await public_limiter.check(request)
    user = await resolve_share_token(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link invalid or expired")

    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user.id)
        .order_by(BookEntry.created_at.asc())
    )
    entries = result.scalars().all()

    entry_dicts = [
        {
            "id": str(e.id),
            "title": e.title,
            "author": e.author,
            "intensity": e.intensity,
            "emotions": [em.emotion_id for em in e.emotions],
            "created_at": e.created_at,
        }
        for e in entries
    ]

    dna = calculate_personality(entry_dicts)
    return {
        "handle": user.handle,
        "personality": dna.get("personality"),
        "stats": dna.get("stats", {}),
        "top_emotions": dna.get("top_emotions", []),
        "share_token": token,
    }
