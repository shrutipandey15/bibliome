from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.notification import TIER_SECURITY
from app.models.user import User
from app.schemas.echo import HandleChangeRequest
from app.schemas.user import (
    PasswordChangeRequest,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from app.services.auth_service import hash_password, revoke_all_refresh_tokens, verify_password
from app.services.handle_service import HandleError, change_handle
from app.services.journal_service import (
    KeyBundleMissing,
    mark_password_wrap_stale,
    replace_key_bundle,
)
from app.services.notification_service import notify
from app.services.visibility import create_share_token, revoke_share_tokens
from app.utils.cookies import clear_refresh_cookie
from app.utils.emotions import canonicalize

router = APIRouter(prefix="/user", tags=["user"])


def _settings_response(user: User) -> UserSettingsResponse:
    return UserSettingsResponse(
        display_name=user.display_name,
        profile_visibility=user.profile_visibility,
        is_public=user.is_public,
        personality_type=user.personality_type,
        reads_for=user.reads_for,
        username=user.username,
        email=user.email,
    )


@router.get("/settings", response_model=UserSettingsResponse)
async def get_settings(current_user: User = Depends(get_current_user)):
    """Get current user settings."""
    return _settings_response(current_user)


@router.patch("/settings", response_model=UserSettingsResponse)
async def update_settings(
    data: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update user settings (display name, profile visibility, stated preference)."""
    update_data = data.model_dump(exclude_unset=True)

    # Stated preference (B7.1) needs validation + it changes the contradiction
    # insight, so it dirties the DNA cache.
    if "reads_for" in update_data:
        raw = update_data.pop("reads_for")
        if raw is None or raw == []:
            current_user.reads_for = None
        else:
            canon = [canonicalize(s) for s in raw]
            if any(c is None for c in canon):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="reads_for must be 1–2 canonical emotion slugs.",
                )
            current_user.reads_for = canon
        current_user.dna_dirty = True

    for field, value in update_data.items():
        if hasattr(current_user, field):
            setattr(current_user, field, value)

    await db.flush()
    return _settings_response(current_user)


@router.patch("/handle")
async def change_user_handle(
    data: HandleChangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change the pseudonymous public handle (rate-limited; old handle enters a
    grace window before it can be reused)."""
    try:
        await change_handle(db, current_user, data.handle)
    except HandleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"handle": current_user.handle}


@router.post("/change-password")
async def change_password(
    data: PasswordChangeRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change password. Requires current password for verification.

    If the account has an encrypted journal, the password change also has to move
    its wrapped data key — which only the client can do. Send the re-wrapped
    bundle as ``journal_key_bundle`` and both are written in this one transaction,
    so there is never a moment where the stored wrap doesn't match the password.

    Omit it and the change still goes through; we then mark the password wrap
    stale and say so in the response rather than letting the user find out the
    next time they open their journal.
    """
    if not await verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = await hash_password(data.new_password)

    # Journal key, same transaction as the password itself.
    journal_state: dict | None = None
    if data.journal_key_bundle is not None:
        try:
            await replace_key_bundle(db, current_user.id, data.journal_key_bundle)
            journal_state = {"rewrapped": True}
        except KeyBundleMissing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No journal key exists for this account, so there is nothing "
                    "to re-wrap. Set one up with POST /journal/key."
                ),
            )
    elif await mark_password_wrap_stale(db, current_user.id):
        journal_state = {
            "rewrapped": False,
            "locked": True,
            "recoverable_with_recovery_code": True,
            "message": (
                "Your journal is still encrypted under your old password. Unlock it "
                "with your recovery code, then re-wrap it (PUT /journal/key)."
            ),
        }

    # Changing the password revokes all sessions and clears this browser's cookie (P1-1).
    await revoke_all_refresh_tokens(db, current_user.id)
    await db.flush()
    clear_refresh_cookie(response)

    # Tier-0 security notice — always delivered, bypasses quiet hours/prefs.
    await notify(db, current_user.id, TIER_SECURITY, "password_changed",
                 payload={"message": "Your password was changed."})

    body: dict = {"message": "Password updated"}
    if journal_state is not None:
        body["journal"] = journal_state
    return body


@router.post("/share-token")
async def generate_share_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mint a new revocable share link (capability link) for the current user."""
    token = await create_share_token(db, current_user.id)
    await db.commit()
    return {"share_token": token}


@router.delete("/share-token", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke all of the current user's active share links."""
    await revoke_share_tokens(db, current_user.id)
    await db.flush()
