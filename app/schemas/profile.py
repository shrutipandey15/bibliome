import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Visibility = Literal["private", "community", "public"]


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    bio: str | None = Field(default=None, max_length=300)
    profile_visibility: Visibility | None = None


class CollectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    visibility: Visibility = "private"


class CollectionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    visibility: Visibility | None = None


class CollectionItemAdd(BaseModel):
    entry_id: uuid.UUID


class CollectionReorder(BaseModel):
    entry_ids: list[uuid.UUID]


class CollectionResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    visibility: str
    position: int


# ── Shared collections (#5) ──

class CollectionBookAdd(BaseModel):
    """Add a book to a shared collection by its CANONICAL id.

    Deliberately not `entry_id`. An entry is one reader's private copy; a shared
    collection holds books, and a member adding one must not be attaching a row
    other members cannot read.
    """
    book_id: uuid.UUID


class CollectionInviteCreate(BaseModel):
    # Both optional: the common case is an open link pasted into a group chat.
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1, le=1000)


class CollectionInviteResponse(BaseModel):
    id: uuid.UUID
    # The raw token, shown ONCE — only its hash is stored. Never re-readable.
    token: str
    expires_at: datetime | None
    max_uses: int | None


class CollectionMemberResponse(BaseModel):
    user_id: uuid.UUID
    handle: str | None
    role: str
    joined_at: datetime


class CollectionInvitePeek(BaseModel):
    """What a link points at, before anyone commits to joining it."""
    collection_id: uuid.UUID
    title: str
    description: str | None
    member_count: int
    book_count: int
    already_member: bool


class CollectionJoinResponse(BaseModel):
    collection_id: uuid.UUID
    title: str
    # False when the caller was already a member — clicking a link twice is not
    # an error, and the UI should say "you're already in" rather than "joined".
    joined: bool


class JoinedCollection(BaseModel):
    """A collection someone else owns and this reader has joined."""
    id: uuid.UUID
    title: str
    description: str | None
    owner_handle: str | None
    joined_at: datetime
    book_count: int
    member_count: int


# ── Collection chat (#6) ──

# Same vocabulary as resonance threads (app/schemas/resonance.py) — one shared
# set of non-emoji reaction marks across both chat surfaces, not one per room.
ChatReactionKind = Literal[
    "resonated", "noted", "reconsidered", "warm", "underlined", "quotable", "chills",
]


class CollectionMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    # Optional: a message may point at one of the collection's books. A label on
    # a message in the one room — never a separate room.
    book_id: uuid.UUID | None = None
    # Optional: quoting an earlier message in this same room.
    reply_to_id: uuid.UUID | None = None


class CollectionReportRequest(BaseModel):
    # Same categories as a thread report — a moderator triaging the queue
    # shouldn't have to learn a second vocabulary for the same judgment call.
    category: Literal["harassment", "hate", "csam", "spam", "self_harm", "pii", "other"] = "other"


class ChatReactionUpdate(BaseModel):
    kind: ChatReactionKind
    on: bool = True


class ChatReactionResponse(BaseModel):
    """State echoed back after /react so the UI never has to guess. Unlike
    Echo's author-only private tally, this is the room's real public count —
    every participant gets the same `reaction_counts`."""
    my_reactions: list[str]
    reaction_counts: dict[str, int]


class ReplyPreview(BaseModel):
    id: uuid.UUID
    handle: str | None
    body: str


class PinRequest(BaseModel):
    # None clears the pin. Setting a new one replaces whatever was pinned —
    # there is only ever one.
    message_id: uuid.UUID | None = None


class PinnedMessageResponse(BaseModel):
    pinned: ReplyPreview | None = None


class CollectionMessageResponse(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID | None = None
    # Denormalised so a message can render its label without the client holding
    # the whole shelf.
    book_title: str | None = None
    handle: str | None
    is_mine: bool
    body: str
    created_at: datetime
    # Present only when the sender's own words tripped the self-harm classifier.
    # Returned TO THE SENDER with the message, never to the room.
    crisis: dict | None = None
    # None when this isn't a reply, or when it was but the quoted message has
    # since been deleted (reply_to_id goes NULL — see the migration docstring).
    reply_to: ReplyPreview | None = None
    reaction_counts: dict[str, int] = Field(default_factory=dict)
    my_reactions: list[str] = Field(default_factory=list)
    # A same-origin, membership-checked URL — never the raw disk path (see
    # app/routers/profile.py get_collection_message_attachment).
    attachment_url: str | None = None


class CollectionMessageList(BaseModel):
    messages: list[CollectionMessageResponse]
    # Cursor for the previous page. Carries BOTH halves of the sort key, because
    # two messages can share a timestamp and a timestamp-only cursor would skip
    # or repeat them at the boundary.
    next_before: datetime | None = None
    next_before_id: uuid.UUID | None = None


class CollectionConversation(BaseModel):
    """One book in the collection, and whether anyone has spoken about it."""
    book_id: uuid.UUID
    title: str
    author: str | None
    cover_url: str | None
    last_message_at: datetime | None
    message_count: int
