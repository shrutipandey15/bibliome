from typing import Literal

from pydantic import BaseModel, Field

Visibility = Literal["private", "community", "public"]


class UserSettingsUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    profile_visibility: Visibility | None = None

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class UserSettingsResponse(BaseModel):
    display_name: str | None
    profile_visibility: Visibility
    is_public: bool  # derived (profile_visibility == "public"); kept for back-compat
    personality_type: str | None
    username: str
    email: str

    model_config = {"from_attributes": True}
