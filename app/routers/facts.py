"""The daily book fact, for the card at the top of the shelf."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.notification import DailyFact
from app.models.user import User

router = APIRouter(prefix="/facts", tags=["facts"])


@router.get("/today")
async def today(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The reader's most recent fact, if it arrived in the last day."""
    row = (await db.execute(select(DailyFact).where(
        DailyFact.user_id == current_user.id,
        DailyFact.sent_at > datetime.now(timezone.utc) - timedelta(hours=24),
    ).order_by(DailyFact.sent_at.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No fact today")
    return row.payload
