"""The weekly Prompt — a single shared question that populates the feed (B6.5).

Curated, not user-generated. One endpoint: what's the campfire question right now.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.echo import PromptResponse
from app.services.echo_service import current_prompt

router = APIRouter(prefix="/prompts", tags=["prompt"])


@router.get("/today", response_model=PromptResponse | None)
async def get_today_prompt(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The live Prompt, or `null` when none is scheduled for now."""
    prompt = await current_prompt(db)
    if prompt is None:
        return None
    return PromptResponse(
        id=prompt.id, question=prompt.question,
        starts_at=prompt.starts_at, ends_at=prompt.ends_at,
    )
