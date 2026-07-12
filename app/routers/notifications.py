from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.notification import (
    MarkReadRequest,
    NotificationItem,
    NotificationListResponse,
    NotificationPrefsResponse,
    NotificationPrefsUpdate,
)
from app.services.notification_service import (
    get_or_create_prefs,
    list_notifications,
    mark_read,
    unread_count,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def get_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The in-app notification center — the source of truth. Deferred (quiet-hours)
    items are withheld until their deliver_after."""
    items = await list_notifications(db, current_user.id)
    count = await unread_count(db, current_user.id)
    return NotificationListResponse(
        notifications=[
            NotificationItem(
                id=n.id, tier=n.tier, kind=n.kind, payload=n.payload,
                read=n.read_at is not None, created_at=n.created_at,
            )
            for n in items
        ],
        unread_count=count,
    )


@router.post("/read", status_code=status.HTTP_204_NO_CONTENT)
async def read_notifications(
    data: MarkReadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark notifications read (bulk). Omit `ids` to mark all read."""
    await mark_read(db, current_user.id, data.ids)


@router.get("/preferences", response_model=NotificationPrefsResponse)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_or_create_prefs(db, current_user.id)


@router.patch("/preferences", response_model=NotificationPrefsResponse)
async def update_preferences(
    data: NotificationPrefsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prefs = await get_or_create_prefs(db, current_user.id)
    update = data.model_dump(exclude_unset=True)
    if "timezone" in update and update["timezone"] is not None:
        try:
            ZoneInfo(update["timezone"])
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown timezone")
    for field, value in update.items():
        setattr(prefs, field, value)
    await db.flush()
    return prefs
