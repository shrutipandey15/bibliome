import uuid
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
