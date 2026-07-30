"""Resonance wire types.

`MatchResponse` is the anonymised card. It has no `user_id` field and never will
— `handle` is the only identity it can carry, and the service leaves that None
until both sides have accepted. Adding an id here would silently undo the whole
privacy model, so the absence is load-bearing.

No response type here carries a count of anything.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MatchStatus = Literal["suggested", "pending", "connected", "declined"]
MatchStrength = Literal["strong", "light"]
# "you_reached" / "they_reached" tell the UI which side of a pending match the
# viewer is on without naming the other person.
MatchDirection = Literal["none", "you_reached", "they_reached"]
ReportCategory = Literal["harassment", "hate", "csam", "spam", "self_harm", "pii", "other"]


class SharedEmotionOut(BaseModel):
    emotion_id: str
    label: str                # the first-person phrase, e.g. "it wrecked me"
    your_strength: int
    their_strength: int
    close: bool               # intensities within CLOSE_INTENSITY of each other


class MatchResponse(BaseModel):
    match_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str | None
    book_author: str | None
    cover_url: str | None
    shared_emotions: list[SharedEmotionOut]
    strength: MatchStrength
    status: MatchStatus
    direction: MatchDirection
    your_note: str | None = None
    # The other reader's note — None until the match is connected.
    their_note: str | None = None
    thread_id: uuid.UUID | None = None
    # The other reader's handle — None until the match is connected. There is no
    # field here for their name, email, or id at any status.
    handle: str | None = None
    created_at: datetime


class MatchListResponse(BaseModel):
    matches: list[MatchResponse]
    # How many reaches this reader has left today. Their own budget, not a
    # measure of anyone else — the one number this feature exposes.
    reaches_left_today: int


class ReachRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class RespondRequest(BaseModel):
    accept: bool
    note: str | None = Field(default=None, max_length=500)


class MessageResponse(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    handle: str              # safe: a thread only exists between connected readers
    is_mine: bool
    body: str
    created_at: datetime


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    # Keyset cursor for paging further back; None when the transcript starts here.
    next_before: datetime | None = None


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ThreadResponse(BaseModel):
    thread_id: uuid.UUID
    match_id: uuid.UUID
    book_id: uuid.UUID
    book_title: str | None
    handle: str
    status: str
    created_at: datetime


class ThreadReportRequest(BaseModel):
    category: ReportCategory
    # Report and walk away in one gesture; blocking is the common case.
    block: bool = True
