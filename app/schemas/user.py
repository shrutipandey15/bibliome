from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.journal import JournalKeyBundleIn

Visibility = Literal["private", "community", "public"]


class UserSettingsUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    profile_visibility: Visibility | None = None
    # "What do you read for?" — 1–2 canonical emotion slugs (B7.1). Validated in
    # the router against VALID_SLUGS; `[]` clears it.
    reads_for: list[str] | None = Field(default=None, max_length=2)

class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)
    # The journal's data key is wrapped under a key derived from the password, and
    # the server cannot re-wrap it — it has neither the old key nor the new one.
    # So the client re-wraps locally and sends the new bundle *with* the password
    # change, and both land in one transaction. Omit it and the password still
    # changes, but the stored wrap goes stale and only the recovery code will open
    # the journal (journalCryptoContract.md §5).
    journal_key_bundle: JournalKeyBundleIn | None = None


class UserSettingsResponse(BaseModel):
    display_name: str | None
    profile_visibility: Visibility
    is_public: bool  # derived (profile_visibility == "public"); kept for back-compat
    personality_type: str | None
    reads_for: list[str] | None = None
    username: str
    email: str

    model_config = {"from_attributes": True}
