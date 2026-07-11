import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.utils.emotions import VALID_EMOTION_IDS
from app.utils.url_safety import validate_cover_url

EntryStatus = Literal["want_to_read", "reading", "finished"]


def _validate_cover(v: str | None) -> str | None:
    """Reject cover URLs that aren't https + an allowlisted host (SSRF guard, B1.8)."""
    return validate_cover_url(v)


class EmotionIn(BaseModel):
    emotion_id: str
    strength: int = Field(default=5, ge=1, le=10)

    def model_post_init(self, __context):
        if self.emotion_id not in VALID_EMOTION_IDS:
            raise ValueError(f"Invalid emotion: {self.emotion_id}. Valid: {VALID_EMOTION_IDS}")


class EmotionOut(BaseModel):
    emotion_id: str
    strength: int

    model_config = {"from_attributes": True}


class EntryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    cover_url: str | None = Field(default=None, max_length=500)
    isbn: str | None = Field(default=None, max_length=13)
    intensity: int = Field(default=5, ge=1, le=10)
    quote: str | None = None
    public_echo: str | None = None
    notes: str | None = None
    emotions: list[EmotionIn] = Field(default_factory=list)
    started_at: date | None = None
    finished_at: date | None = None
    status: EntryStatus | None = None

    _check_cover = field_validator("cover_url")(_validate_cover)


class EntryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    author: str | None = None
    cover_url: str | None = None
    isbn: str | None = None
    intensity: int | None = Field(default=None, ge=1, le=10)
    quote: str | None = None
    public_echo: str | None = None
    notes: str | None = None
    emotions: list[EmotionIn] | None = None
    started_at: date | None = None
    finished_at: date | None = None
    status: EntryStatus | None = None

    _check_cover = field_validator("cover_url")(_validate_cover)


class EntryResponse(BaseModel):
    id: uuid.UUID
    title: str
    author: str | None
    cover_url: str | None
    isbn: str | None
    intensity: int
    quote: str | None
    public_echo: str | None
    notes: str | None
    emotions: list[EmotionOut]
    started_at: date | None
    finished_at: date | None
    created_at: datetime
    updated_at: datetime
    status: EntryStatus = "finished"
    arc_start_emotion_id: str | None = None
    arc_middle_emotion_id: str | None = None
    arc_end_emotion_id: str | None = None
    finish_thought: str | None = None
    room_unlocks_new: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EntryFinish(BaseModel):
    start_emotion_slug: str
    middle_emotion_slug: str
    end_emotion_slug: str
    thought: str | None = Field(default=None, max_length=120)
    intensity: int = Field(ge=1, le=10)


class ImportResponse(BaseModel):
    parsed: int          # rows that parsed into a book
    imported: int        # new entries created
    skipped: int         # duplicates skipped
    errors: list[str]    # per-row parse errors (capped)


class EntryListResponse(BaseModel):
    entries: list[EntryResponse]
    total: int
    next_cursor: str | None = None
    has_more: bool = False
    # Legacy — kept for backward compat
    page: int | None = None
    per_page: int | None = None