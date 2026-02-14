import secrets
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.user import UserSettingsResponse, UserSettingsUpdate

router = APIRouter(prefix="/user", tags=["user"])


@router.get("/settings", response_model=UserSettingsResponse)
async def get_settings(current_user: User = Depends(get_current_user)):
    """Get current user settings."""
    return UserSettingsResponse(
        display_name=current_user.display_name,
        is_public=current_user.is_public,
        personality_type=current_user.personality_type,
        username=current_user.username,
        email=current_user.email,
    )


@router.patch("/settings", response_model=UserSettingsResponse)
async def update_settings(
    data: UserSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update user settings (display name, public/private toggle)."""
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(current_user, field, value)

    await db.flush()

    return UserSettingsResponse(
        display_name=current_user.display_name,
        is_public=current_user.is_public,
        personality_type=current_user.personality_type,
        username=current_user.username,
        email=current_user.email,
    )

@router.post("/share-token")
async def generate_share_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate (or reset) a secure share token for the current user."""
    token = secrets.token_urlsafe(16)
    
    current_user.share_token = token
    await db.commit()
    
    return {"share_token": token}