"""The share card's own endpoints: the owner's settings live on /user, the
public page and preview on /s/ (og.py). This is only the share counter."""

from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.middleware.auth import get_current_user
from app.services.card_counts import bump

router = APIRouter(prefix="/card", tags=["card"])

share_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="card-share")


class ShareEvent(BaseModel):
    format: Literal["story", "season", "square", "link"]


@router.post("/shares", status_code=status.HTTP_204_NO_CONTENT)
async def count_share(
    data: ShareEvent,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One share, counted into today's total for its format. Signed-in readers
    only (a share is of your own card), but the count keeps no trace of who."""
    await share_limiter.check(request)
    await bump(db, "share", data.format)
