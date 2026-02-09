import uuid
from datetime import datetime

from pydantic import BaseModel


class PublicEcho(BaseModel):
    entry_id: uuid.UUID
    title: str
    author: str | None
    public_echo: str
    emotions: list[str]
    intensity: int
    created_at: datetime


class PublicEchoesResponse(BaseModel):
    username: str
    display_name: str | None
    echoes: list[PublicEcho]
    total: int


class PublicCardResponse(BaseModel):
    username: str
    display_name: str | None
    personality_type: str | None
    personality_description: str | None
    personality_color: str | None
    personality_glyph: str | None
    book_count: int
    top_emotions: list[dict]
    member_since: datetime
