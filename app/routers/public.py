import uuid
import httpx

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.book_entry import BookEntry
from app.models.user import User
from app.schemas.public import PublicCardResponse, PublicEcho, PublicEchoesResponse
from app.services.dna_engine import calculate_personality
from app.services.og_image import generate_dna_card_image, generate_echo_card_image, generate_story_image # <--- IMPORTED

router = APIRouter(prefix="/public", tags=["public"])


async def _get_strict_public_user(db: AsyncSession, username: str) -> User:
    """
    Get a user by username. STRICTLY enforces public profile.
    Used ONLY for the full profile card (which is effectively disabled for everyone now).
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.is_public:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This profile is private")

    return user


@router.get("/stream", response_model=PublicEchoesResponse)
async def get_public_stream(db: AsyncSession = Depends(get_db)):
    """
    The Global Echo Feed.
    Returns the 50 most recent echoes from ALL users.
    """
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions), selectinload(BookEntry.user))
        .where(
            BookEntry.public_echo.isnot(None),
            BookEntry.public_echo != "",
        )
        .order_by(desc(BookEntry.created_at))
        .limit(50)
    )
    entries = result.scalars().all()

    echoes = []
    for e in entries:
        if not e.user: continue
            
        echoes.append(
            PublicEcho(
                entry_id=e.id,
                title=e.title,
                author=e.author,
                public_echo=e.public_echo,
                emotions=[em.emotion_id for em in e.emotions],
                intensity=e.intensity,
                created_at=e.created_at,
                username=e.user.username,
                display_name=e.user.display_name
            )
        )

    return PublicEchoesResponse(
        username="community",
        display_name="Global Stream",
        echoes=echoes,
        total=len(echoes),
    )


@router.get("/echoes/{username}", response_model=PublicEchoesResponse)
async def get_user_echoes(username: str, db: AsyncSession = Depends(get_db)):
    """
    Get a SPECIFIC user's echoes. 
    Always public, regardless of profile settings.
    """
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

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
    Get a user's full DNA stats.
    STRICT: Will return 403 Forbidden for almost everyone now.
    """
    user = await _get_strict_public_user(db, username)

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

    ptype_info = personality if personality else None

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

async def _generate_card_image_for_user(user: User, db: AsyncSession):
    """Helper to generate the DNA card image."""
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No DNA profile generated yet",
        )

    image_bytes = generate_dna_card_image(
        personality_name=personality["name"],
        personality_description=personality["description"],
        personality_color=personality["color"],
        personality_glyph=personality["glyph"],
        username=user.username,
        book_count=len(entries),
        top_emotions=dna.get("top_emotions", []),
    )

    return Response(content=image_bytes, media_type="image/png")


@router.get("/card/{username}/og")
async def get_card_og_image(username: str, db: AsyncSession = Depends(get_db)):
    """
    Strict OG Image for public profiles.
    Likely unused now, but kept for backward compatibility.
    """
    user = await _get_strict_public_user(db, username)
    return await _generate_card_image_for_user(user, db)


@router.get("/shared/{token}/og")
async def get_shared_token_og_image(token: str, db: AsyncSession = Depends(get_db)):
    """
    Generate OG Image for a SHARE TOKEN.
    Allows private users to download/share their 'Year in Review'.
    """
    result = await db.execute(select(User).where(User.share_token == token))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid token")

    return await _generate_card_image_for_user(user, db)


@router.get("/echo/{entry_id}/og")
async def get_echo_og_image(entry_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Generate OG Image for a single ECHO (Horizontal).
    """
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()

    if not entry or not entry.public_echo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")

    user_result = await db.execute(select(User).where(User.id == entry.user_id))
    user = user_result.scalar_one_or_none()

    image_bytes = generate_echo_card_image(
        title=entry.title,
        author=entry.author or "Unknown",
        public_echo=entry.public_echo,
        emotions=[em.emotion_id for em in entry.emotions],
        intensity=entry.intensity,
        username=user.username if user else "Anonymous",
    )

    return Response(content=image_bytes, media_type="image/png")

@router.get("/echo/{entry_id}/story")
async def get_echo_story_image(entry_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Generate a Vertical (9:16) Story Image for Instagram/TikTok.
    """
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()

    if not entry or not entry.public_echo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")

    user_result = await db.execute(select(User).where(User.id == entry.user_id))
    user = user_result.scalar_one_or_none()

    cover_bytes = None
    if entry.cover_url:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(entry.cover_url, timeout=5.0)
                if resp.status_code == 200:
                    cover_bytes = resp.content
        except Exception as e:
            print(f"Failed to fetch cover: {e}") 

    image_bytes = generate_story_image(
        title=entry.title,
        author=entry.author or "Unknown",
        public_echo=entry.public_echo,
        emotions=[em.emotion_id for em in entry.emotions],
        intensity=entry.intensity,
        username=user.username if user else "Anonymous",
        cover_bytes=cover_bytes
    )

    return Response(content=image_bytes, media_type="image/png")

@router.get("/shared/{token}")
async def get_shared_card(token: str, db: AsyncSession = Depends(get_db)):
    """
    Get DNA profile via secure token. 
    The primary way for users to share their full profile now.
    """
    result = await db.execute(select(User).where(User.share_token == token))
    user = result.scalar_one_or_none()

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
        "username": user.username,
        "personality": dna.get("personality"),
        "stats": dna.get("stats", {}),
        "top_emotions": dna.get("top_emotions", []),
        "share_token": user.share_token 
    }