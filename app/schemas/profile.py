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


# ── Collection chat (#6) ──

class CollectionMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class CollectionMessageResponse(BaseModel):
    id: uuid.UUID
    book_id: uuid.UUID
    handle: str | None
    is_mine: bool
    body: str
    created_at: datetime
    # Present only when the sender's own words tripped the self-harm classifier.
    # Returned TO THE SENDER with the message, never to the room.
    crisis: dict | None = None


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
