"""Resonance endpoints: see your matches, reach out, answer.

Every response on this router is built by ``resonance_service.match_view``, which
is the single place identity can leak from. Do not assemble a match payload by
hand here.

A match the caller is not party to returns 404, not 403 — a 403 would confirm the
row exists, which is itself information about two other people.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.notification import TIER_DIRECT
from app.models.user import User
from app.schemas.resonance import (
    MatchListResponse,
    MatchResponse,
    ReachRequest,
    RespondRequest,
)
from app.services.notification_service import notify
from app.services.resonance_service import (
    REACH_DAILY_LIMIT,
    ResonanceError,
    get_match_for_user,
    list_matches,
    match_view,
    reach,
    respond,
)

router = APIRouter(prefix="/resonance", tags=["resonance"])

# Per *account*, per day — an IP limit would be trivially sidestepped and would
# punish shared networks. Reaching out is the harvesting vector here: five a day
# is plenty for a person and useless for a scraper.
reach_limiter = RateLimiter(
    max_requests=REACH_DAILY_LIMIT, window_seconds=86_400, prefix="resonance_reach"
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Match not found")


@router.get("/matches", response_model=MatchListResponse)
async def get_matches(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your current matches: at most three suggestions, plus anything live.

    Anonymised — a suggestion is a book, some shared emotions, and a strength.
    No handle, no name, no id, and no indication of how many other people are in
    the pool behind it.
    """
    matches = await list_matches(db, current_user.id)
    views = [MatchResponse(**await match_view(db, m, current_user.id)) for m in matches]
    return MatchListResponse(
        matches=views,
        reaches_left_today=await reach_limiter.remaining(str(current_user.id)),
    )


@router.post("/{match_id}/reach", response_model=MatchResponse)
async def reach_out(
    match_id: uuid.UUID,
    data: ReachRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Leave the opening note. The note is sealed: the other reader is told that
    someone who felt the same way about this book reached out, and can read what
    you wrote only after they answer."""
    match = await get_match_for_user(db, match_id, current_user.id)
    if match is None:
        raise _not_found()

    await reach_limiter.check_key(str(current_user.id))

    try:
        match, thread = await reach(db, match, current_user.id, data.note)
    except ResonanceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    other_id = match.other_id(current_user.id)
    if thread is not None:
        # Mutual reach connected them — tell both sides, not just one.
        for uid in (match.user_a, match.user_b):
            await notify(
                db, uid, TIER_DIRECT, "resonance_connected",
                {"match_id": str(match.id), "thread_id": str(thread.id)},
                actor_id=current_user.id,  # notify() drops the self-notification
            )
    else:
        # Deliberately contentless: the payload names no book and no person, so a
        # notification preview cannot become the identity leak the API prevents.
        await notify(
            db, other_id, TIER_DIRECT, "resonance_reach", {"match_id": str(match.id)},
        )

    return MatchResponse(**await match_view(db, match, current_user.id))


@router.post("/{match_id}/respond", response_model=MatchResponse)
async def respond_to_match(
    match_id: uuid.UUID,
    data: RespondRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept or decline. Accepting reveals both handles and opens the thread.

    Declining is silent and final: the other side is not notified and the pair is
    never suggested to each other again, on this book or any other.
    """
    match = await get_match_for_user(db, match_id, current_user.id)
    if match is None:
        raise _not_found()

    try:
        match, thread = await respond(db, match, current_user.id, data.accept, data.note)
    except ResonanceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if thread is not None:
        for uid in (match.user_a, match.user_b):
            await notify(
                db, uid, TIER_DIRECT, "resonance_connected",
                {"match_id": str(match.id), "thread_id": str(thread.id)},
                actor_id=current_user.id,  # notify() drops the self-notification
            )

    return MatchResponse(**await match_view(db, match, current_user.id))
