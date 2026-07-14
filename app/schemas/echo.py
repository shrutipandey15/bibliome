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
    prompt_id: uuid.UUID | None = None  # optional: answer the weekly Prompt (B6.5)


class PromptResponse(BaseModel):
    """The current weekly campfire question. `None` when nothing is live."""
    id: uuid.UUID
    question: str
    starts_at: datetime
    ends_at: datetime


class ReplyResponse(BaseModel):
    id: uuid.UUID
    echo_id: uuid.UUID
    handle: str
    body: str
    created_at: datetime


class EchoResponse(BaseModel):
    """An echo card.

    Carries NO *public* counts. The only count here is `reaction_counts`, which is
    populated ONLY when the viewer is the echo's author (the private witness signal)
    and is `None` for everyone else. Replies are shown (previewed), never counted.
    """
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
    prompt_id: uuid.UUID | None = None  # the weekly Prompt this echo answers, if any

    # Viewer-relative render state (B6.1).
    my_reactions: list[str] = []          # which kinds the *viewer* has set
    replies_preview: list[ReplyResponse] = []  # first 2 replies, oldest first, inline
    has_more_replies: bool = False        # drives the neutral "read the rest" link (no number)
    reaction_counts: dict[str, int] | None = None  # author-only private aggregate; None otherwise


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


class EchoThreadResponse(BaseModel):
    echo: EchoResponse
    replies: list[ReplyResponse]


class ReactionUpdate(BaseModel):
    kind: ReactionKind
    on: bool = True


class ReactionResponse(BaseModel):
    """State echoed back after /react so the UI never has to guess (B6.2)."""
    my_reactions: list[str]                       # the viewer's kinds after the change
    reaction_counts: dict[str, int] | None = None  # author-only private aggregate
    added_to_shelf: bool = False                  # true when 'adding_to_list' created a shelf entry


class ReportCreate(BaseModel):
    category: ReportCategory


class HandleChangeRequest(BaseModel):
    handle: str = Field(min_length=3, max_length=50)


class BlockRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=50)
