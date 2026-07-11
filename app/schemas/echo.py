import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Visibility = Literal["community", "public"]
ReactionKind = Literal["felt_this", "changed_my_mind", "adding_to_list"]
ReportCategory = Literal["harassment", "hate", "csam", "spam", "self_harm", "pii", "other"]


class EchoCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)
    book_title: str | None = Field(default=None, max_length=300)
    book_author: str | None = Field(default=None, max_length=200)
    primary_emotion: str | None = Field(default=None, max_length=30)
    secondary_emotion: str | None = Field(default=None, max_length=30)
    visibility: Visibility = "community"


class EchoResponse(BaseModel):
    """An echo card. Deliberately carries NO counts of any kind."""
    id: uuid.UUID
    handle: str
    book_title: str | None
    book_author: str | None
    primary_emotion: str | None
    secondary_emotion: str | None
    body: str
    visibility: str
    created_at: datetime
    edited_at: datetime | None


class CrisisInterstitial(BaseModel):
    message: str
    resources: list[dict]


class EchoCreateResponse(BaseModel):
    echo: EchoResponse
    held_for_review: bool = False
    # Present only when the self-harm classifier fired: the supportive path.
    crisis: CrisisInterstitial | None = None


class FeedResponse(BaseModel):
    echoes: list[EchoResponse]
    next_cursor: str | None = None
    caught_up: bool = True


class ReplyCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)


class ReplyResponse(BaseModel):
    id: uuid.UUID
    echo_id: uuid.UUID
    handle: str
    body: str
    created_at: datetime


class EchoThreadResponse(BaseModel):
    echo: EchoResponse
    replies: list[ReplyResponse]


class ReactionUpdate(BaseModel):
    kind: ReactionKind
    on: bool = True


class ReportCreate(BaseModel):
    category: ReportCategory


class HandleChangeRequest(BaseModel):
    handle: str = Field(min_length=3, max_length=50)


class BlockRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=50)
