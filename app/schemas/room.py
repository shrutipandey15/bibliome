import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.mirror import EmotionMini


class ShelfBook(BaseModel):
    entry_id: uuid.UUID
    title: str
    author: str | None
    dominant_emotion: EmotionMini | None
    status: Literal["want_to_read", "reading", "finished"]
    shelf_position: int | None


class RoomDecoration(BaseModel):
    slug: str
    display_name: str
    unlocked_at: datetime | None = None


class RoomResponse(BaseModel):
    dna_type_slug: str | None
    dna_type_name: str | None
    books: list[ShelfBook]
    decorations: list[RoomDecoration]


class ShelfPositionUpdate(BaseModel):
    shelf_position: int
