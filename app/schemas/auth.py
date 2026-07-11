import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthUser(BaseModel):
    id: uuid.UUID
    handle: str  # currently the username; becomes the pseudonymous handle in Phase 3
    email: str


class AccessTokenResponse(BaseModel):
    """Shape returned by /refresh — refresh token is in the cookie, not here."""
    access_token: str
    expires_in: int


class AuthResponse(AccessTokenResponse):
    """Shape returned by login/register (auto-login)."""
    user: AuthUser

class ForgotPasswordRequest(BaseModel):
    email: EmailStr
class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    display_name: str | None
    personality_type: str | None
    is_public: bool
    is_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}