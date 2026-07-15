import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CheckinCreate(BaseModel):
    emotion_slug: str
    note: str | None = Field(default=None, max_length=80)


class CheckinResponse(BaseModel):
    id: uuid.UUID
    entry_id: uuid.UUID
    emotion_slug: str
    note: str | None
    created_at: datetime


class StatusUpdate(BaseModel):
    status: Literal["want_to_read", "reading", "finished", "abandoned", "paused", "reread"]
