from pydantic import BaseModel, Field


class UserSettingsUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    is_public: bool | None = None


class UserSettingsResponse(BaseModel):
    display_name: str | None
    is_public: bool
    personality_type: str | None
    username: str
    email: str

    model_config = {"from_attributes": True}