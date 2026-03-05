from pydantic import BaseModel, Field, model_validator


class UserSettingsUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class UserSettingsResponse(BaseModel):
    display_name: str | None
    is_public: bool
    personality_type: str | None
    username: str
    email: str

    model_config = {"from_attributes": True}


class RoomItem(BaseModel):
    type: str = Field(pattern="^(book|deco)$")
    id: str = Field(max_length=100)


class RoomLayoutUpdate(BaseModel):
    """Payload for saving room arrangement."""
    shelves: list[list[RoomItem]] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_shelves(self):
        MAX_ITEMS_PER_SHELF = 12
        total_items = 0
        seen_decos = set()

        for i, shelf in enumerate(self.shelves):
            if len(shelf) > MAX_ITEMS_PER_SHELF:
                raise ValueError(f"Shelf {i} exceeds {MAX_ITEMS_PER_SHELF} items")
            total_items += len(shelf)
            for item in shelf:
                if item.type == "deco":
                    if item.id in seen_decos:
                        raise ValueError(f"Decoration '{item.id}' placed multiple times")
                    seen_decos.add(item.id)

        if total_items > 50:
            raise ValueError("Total items across all shelves exceeds maximum (50)")

        return self


class RoomDecorationInfo(BaseModel):
    id: str
    name: str
    description: str
    unlock_condition: str
    unlocked: bool


class RoomResponse(BaseModel):
    version: int
    layout: dict | None
    unlocks: list[str]
    decorations: list[RoomDecorationInfo]

    model_config = {"from_attributes": True}