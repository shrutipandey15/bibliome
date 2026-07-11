"""PARKED (B1.18): the GET /api/room endpoint and schemas/room.py::RoomResponse.

This is the second of two overlapping "room" APIs (the live one is
GET /api/user/room in routers/user.py). It is intentionally NOT mounted in
app/main.py — kept only so no data/logic is lost while the Reading Room feature
is deferred. Do not wire it back up without collapsing the duplicate contracts.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.user import User
from app.schemas.room import RoomDecoration, RoomResponse, ShelfBook
from app.services.mirror_service import _dominant_emotion
from app.services.room_decorations import build_decoration_catalog

router = APIRouter(prefix="/room", tags=["room"])


@router.get("", response_model=RoomResponse)
async def get_room(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The reading room: DNA type, ordered shelf of books, and decoration catalog."""
    # Latest DNA snapshot (for room atmosphere theme)
    dna_result = await db.execute(
        select(DNASnapshot)
        .where(DNASnapshot.user_id == current_user.id)
        .order_by(DNASnapshot.generated_at.desc())
        .limit(1)
    )
    snapshot = dna_result.scalar_one_or_none()
    dna_type_slug = snapshot.dna_type_slug if snapshot else None
    dna_type_name = snapshot.personality_type if snapshot else None

    # Shelf: shelf_position ASC NULLS LAST, then finished_at DESC
    shelf_result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == current_user.id)
        .order_by(
            BookEntry.shelf_position.asc().nulls_last(),
            BookEntry.finished_at.desc().nulls_last(),
        )
    )
    entries = shelf_result.scalars().all()

    books = [
        ShelfBook(
            entry_id=e.id,
            title=e.title,
            author=e.author,
            dominant_emotion=_dominant_emotion(e),
            status=e.status,
            shelf_position=e.shelf_position,
        )
        for e in entries
    ]

    unlocked_ids = current_user.room_unlocks or []
    catalog = build_decoration_catalog(unlocked_ids)
    decorations = [
        RoomDecoration(slug=d["id"], display_name=d["name"])
        for d in catalog
        if d["unlocked"]
    ]

    return RoomResponse(
        dna_type_slug=dna_type_slug,
        dna_type_name=dna_type_name,
        books=books,
        decorations=decorations,
    )
