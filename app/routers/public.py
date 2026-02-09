from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.book_entry import BookEntry
from app.models.user import User
from app.schemas.public import PublicCardResponse, PublicEcho, PublicEchoesResponse
from app.services.dna_engine import PERSONALITY_TYPES, calculate_personality

router = APIRouter(prefix="/public", tags=["public"])


async def _get_public_user(db: AsyncSession, username: str) -> User:
    """Get a user by username. Must be public."""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.is_public:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This profile is private")

    return user


@router.get("/echoes/{username}", response_model=PublicEchoesResponse)
async def get_public_echoes(username: str, db: AsyncSession = Depends(get_db)):
    """
    Get a user's public echoes. No auth required.
    Only returns entries that have a public_echo set.
    """
    user = await _get_public_user(db, username)

    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(
            BookEntry.user_id == user.id,
            BookEntry.public_echo.isnot(None),
            BookEntry.public_echo != "",
        )
        .order_by(BookEntry.created_at.desc())
    )
    entries = result.scalars().all()

    echoes = [
        PublicEcho(
            entry_id=e.id,
            title=e.title,
            author=e.author,
            public_echo=e.public_echo,
            emotions=[em.emotion_id for em in e.emotions],
            intensity=e.intensity,
            created_at=e.created_at,
        )
        for e in entries
    ]

    return PublicEchoesResponse(
        username=user.username,
        display_name=user.display_name,
        echoes=echoes,
        total=len(echoes),
    )


@router.get("/card/{username}", response_model=PublicCardResponse)
async def get_public_card(username: str, db: AsyncSession = Depends(get_db)):
    """
    Get a user's shareable DNA card data. No auth required.
    Used by the frontend to render the card, and for OG image generation.
    """
    user = await _get_public_user(db, username)

    # Get entries to calculate live personality
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

    # Find full personality type info
    ptype_info = None
    if personality:
        ptype_info = personality

    return PublicCardResponse(
        username=user.username,
        display_name=user.display_name,
        personality_type=ptype_info["name"] if ptype_info else None,
        personality_description=ptype_info["description"] if ptype_info else None,
        personality_color=ptype_info["color"] if ptype_info else None,
        personality_glyph=ptype_info["glyph"] if ptype_info else None,
        book_count=len(entries),
        top_emotions=dna.get("top_emotions", []),
        member_since=user.created_at,
    )
