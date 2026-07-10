import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class EmotionMini(BaseModel):
    slug: str
    symbol: str
    color: str


class LandscapeItem(BaseModel):
    entry_id: uuid.UUID
    book_title: str
    book_author: str | None
    dominant_emotion: EmotionMini | None
    finished_at: date | None
    status: Literal["want_to_read", "reading", "finished"]


LandscapeResponse = list[LandscapeItem]


class RightNowBook(BaseModel):
    entry_id: uuid.UUID
    title: str
    author: str | None
    cover_url: str | None


class RightNowCheckin(BaseModel):
    emotion: EmotionMini
    note: str | None
    created_at: datetime


class RightNowResponse(BaseModel):
    book: RightNowBook
    last_checkin: RightNowCheckin | None


class InsightResponse(BaseModel):
    sentence: str | None
    week_key: str


class WeeklyMemoryResponse(BaseModel):
    memory: str | None
    week_key: str
