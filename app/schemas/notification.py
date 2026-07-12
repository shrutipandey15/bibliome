import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationItem(BaseModel):
    id: uuid.UUID
    tier: int
    kind: str
    payload: dict
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: list[NotificationItem]
    unread_count: int


class MarkReadRequest(BaseModel):
    ids: list[uuid.UUID] | None = None  # None = mark all read


class NotificationPrefsResponse(BaseModel):
    reply_enabled: bool
    digest_enabled: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    timezone: str

    model_config = {"from_attributes": True}


class NotificationPrefsUpdate(BaseModel):
    reply_enabled: bool | None = None
    digest_enabled: bool | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = Field(default=None, max_length=64)
