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
from app.services.notification_service import notify
from app.services.visibility import create_share_token, revoke_share_tokens
from app.utils.cookies import clear_refresh_cookie

router = APIRouter(prefix="/user", tags=["user"])


def _settings_response(user: User) -> UserSettingsResponse:
    return UserSettingsResponse(
        display_name=user.display_name,
        profile_visibility=user.profile_visibility,
        is_public=user.is_public,
        personality_type=user.personality_type,
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
    """Update user settings (display name, profile visibility)."""
    update_data = data.model_dump(exclude_unset=True)
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
    """Change password. Requires current password for verification."""
    if not await verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = await hash_password(data.new_password)
    # Changing the password revokes all sessions and clears this browser's cookie (P1-1).
    await revoke_all_refresh_tokens(db, current_user.id)
    await db.flush()
    clear_refresh_cookie(response)

    # Tier-0 security notice — always delivered, bypasses quiet hours/prefs.
    await notify(db, current_user.id, TIER_SECURITY, "password_changed",
                 payload={"message": "Your password was changed."})

    return {"message": "Password updated"}


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
