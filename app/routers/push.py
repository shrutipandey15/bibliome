"""Web Push subscribe/unsubscribe (add-on to #6)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.services.push_service import delete_subscription, push_to_user, save_subscription

router = APIRouter(prefix="/push", tags=["push"])
logger = logging.getLogger("bibliome.push")

# Keyed per user, not per IP: this rings the caller's own devices, so the only
# thing to cap is someone leaning on the button and hammering the push service.
test_limiter = RateLimiter(max_requests=5, window_seconds=300, prefix="push_test")


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


@router.post("/test")
async def send_test(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Ring this reader's own devices, right now.

    The one thing no test can check: whether the VAPID pair is actually accepted
    by Google's and Mozilla's push services, and whether the worker on this
    phone shows what arrives. Everything else about notifications is verified
    in CI against a stub — this is the end of the wire.

    Only ever targets the caller. There is no user parameter to abuse.
    """
    settings = get_settings()
    if not settings.push_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Push isn't configured on this server",
        )

    await test_limiter.check_key(str(current_user.id))

    sent = await push_to_user(db, current_user.id, "push_test", {})
    # `sent` counts devices the push service accepted it for — which is the
    # honest answer. Zero means nothing is subscribed (or every subscription is
    # dead), and saying so beats a cheerful "sent!" that rings nothing.
    return {"sent": sent}
