from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.mirror import (
    InsightResponse,
    LandscapeResponse,
    RightNowResponse,
    WeeklyMemoryResponse,
)
from app.services.insight_service import get_or_cache_insight, get_or_cache_weekly_memory
from app.services.mirror_service import get_landscape, get_right_now

router = APIRouter(prefix="/mirror", tags=["mirror"])


@router.get("/landscape", response_model=LandscapeResponse)
async def landscape(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The emotional landscape strip: up to 50 of the user's most recently
    finished (or currently reading) books, each tagged with its dominant emotion."""
    return await get_landscape(db, current_user.id)


@router.get("/right-now", response_model=RightNowResponse | None)
async def right_now(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Currently-reading book + last check-in. Returns null if nothing is being read."""
    return await get_right_now(db, current_user.id)


@router.get("/insight", response_model=InsightResponse)
async def insight(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A single Mirror insight sentence, cached per ISO week."""
    sentence, week = await get_or_cache_insight(db, current_user)
    return InsightResponse(sentence=sentence, week_key=week)


@router.get("/weekly-memory", response_model=WeeklyMemoryResponse)
async def weekly_memory(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One observation pulled from older reading history. Cached per ISO week."""
    memory, week = await get_or_cache_weekly_memory(db, current_user)
    return WeeklyMemoryResponse(memory=memory, week_key=week)
