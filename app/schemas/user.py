from pydantic import BaseModel, Field


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