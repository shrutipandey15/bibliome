"""Web Push subscribe/unsubscribe (add-on to #6)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services.push_service import delete_subscription, save_subscription

router = APIRouter(prefix="/push", tags=["push"])
logger = logging.getLogger("bibliome.push")


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class PushSubscribe(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


@router.get("/key")
async def public_key():
    """The VAPID public key the browser needs to subscribe.

    Public by design — it is the half of the pair that is meant to be handed out,
    and a subscription signed with it is still worthless without our private key.
    Returns `enabled: false` rather than 404ing when push is not configured, so
    the client can hide the toggle instead of showing one that errors.
    """
    settings = get_settings()
    if not settings.push_enabled:
        return {"enabled": False, "key": None}
    return {"enabled": True, "key": settings.VAPID_PUBLIC_KEY}


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(
    data: PushSubscribe,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register this device. Idempotent — the endpoint is the identity, so
    re-subscribing in the same browser updates rather than duplicates."""
    settings = get_settings()
    if not settings.push_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push isn't configured on this server",
        )
    await save_subscription(
        db, current_user.id, data.endpoint, data.keys.p256dh, data.keys.auth,
    )


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    data: PushUnsubscribe,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Drop this device.

    Deletes by endpoint without checking ownership on purpose: the caller is
    holding the endpoint, which means they are the device. Requiring a match
    would strand a subscription that got re-pointed at another account on a
    shared machine — the row would then be un-deletable by the person actually
    holding it.
    """
    await delete_subscription(db, data.endpoint)
