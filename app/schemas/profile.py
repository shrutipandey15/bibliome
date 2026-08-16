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
