import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.book_entry import BookEntry
from app.models.echo import Echo, EchoReply
from app.models.user import User
from app.schemas.echo import (
    CrisisInterstitial,
    EchoCreate,
    EchoCreateResponse,
    EchoResponse,
    EchoThreadResponse,
    FeedResponse,
    ReactionResponse,
    ReactionUpdate,
    ReplyCreate,
    ReplyResponse,
    ReportCreate,
)
from app.services.echo_service import (
    EchoError,
    FeedAnnotations,
    _book_key,
    add_book_to_shelf,
    annotate_feed,
    create_echo,
    create_reply,
    get_visible_echo,
    list_feed,
    list_replies,
    reaction_counts,
    set_reaction,
    viewer_reactions,
)
from app.services.moderation import CRISIS_RESOURCES, VERDICT_CRISIS, submit_report
from app.models.notification import TIER_DIRECT
from app.services.notification_service import notify

router = APIRouter(prefix="/echoes", tags=["echo"])

echo_write_limiter = RateLimiter(max_requests=20, window_seconds=300, prefix="echo_write")
# Reaction spam is cheap; throttle it (B6.2). Higher ceiling than writes — tapping
# marginalia is a normal reading gesture, not authoring.
react_limiter = RateLimiter(max_requests=120, window_seconds=300, prefix="echo_react")

# New-account cool-down (B3.9): young accounts with few logged books post at a
# reduced rate — kills throwaway spam without blocking genuine new readers.
NEW_ACCOUNT_HOURS = 24
NEW_ACCOUNT_MIN_BOOKS = 3
NEW_ACCOUNT_ECHO_CAP = 3


def _reply_resp(reply: EchoReply) -> ReplyResponse:
    return ReplyResponse(
        id=reply.id, echo_id=reply.echo_id, handle=reply.author.handle,
        body=reply.body, created_at=reply.created_at,
    )


def _echo_resp(
    echo: Echo,
    handle: str,
    ann: FeedAnnotations | None = None,
    viewer_id: uuid.UUID | None = None,
) -> EchoResponse:
    """Build an echo card. When `ann` is supplied (feed path), the viewer-relative
    render state is stitched in from the batch-loaded page annotations."""
    my_reactions: list[str] = []
    replies_preview: list[ReplyResponse] = []
    has_more = False
    counts = None
    if ann is not None:
        my_reactions = ann.my_reactions.get(echo.id, [])
        replies_preview = [_reply_resp(r) for r in ann.replies_for(echo.id)]
        has_more = ann.has_more.get(echo.id, False)
        # Private witness signal: only the author ever receives counts.
        if viewer_id is not None and echo.author_id == viewer_id:
            counts = ann.counts.get(echo.id, {})
    return EchoResponse(
        id=echo.id,
        handle=handle,
        book_title=echo.book_title,
        book_author=echo.book_author,
        primary_emotion=echo.primary_emotion,
        secondary_emotion=echo.secondary_emotion,
        body=echo.body,
        visibility=echo.visibility,
        created_at=echo.created_at,
        edited_at=echo.edited_at,
        prompt_id=echo.prompt_id,
        my_reactions=my_reactions,
        replies_preview=replies_preview,
        has_more_replies=has_more,
        reaction_counts=counts,
    )


async def _enforce_new_account_cooldown(db: AsyncSession, user: User) -> None:
    now = datetime.now(timezone.utc)
    age = now - user.created_at.replace(tzinfo=timezone.utc)
    if age >= timedelta(hours=NEW_ACCOUNT_HOURS):
        return
    books = (await db.execute(
        select(func.count(BookEntry.id)).where(BookEntry.user_id == user.id)
    )).scalar() or 0
    if books >= NEW_ACCOUNT_MIN_BOOKS:
        return
    hour_ago = now - timedelta(hours=1)
    recent = (await db.execute(
        select(func.count(Echo.id)).where(Echo.author_id == user.id, Echo.created_at >= hour_ago)
    )).scalar() or 0
    if recent >= NEW_ACCOUNT_ECHO_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="New accounts can post a limited number of echoes at first. Log a few books to unlock more.",
        )


@router.post("", response_model=EchoCreateResponse, status_code=status.HTTP_201_CREATED)
async def post_echo(
    data: EchoCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Publish an echo (book/emotion-anchored). May be held for review, or route
    the author to the supportive crisis path."""
    await echo_write_limiter.check(request)
    await _enforce_new_account_cooldown(db, current_user)

    try:
        echo, verdict, reason = await create_echo(
            db, current_user.id,
            body=data.body,
            book_title=data.book_title,
            book_author=data.book_author,
            primary_emotion=data.primary_emotion,
            secondary_emotion=data.secondary_emotion,
            visibility=data.visibility,
            prompt_id=data.prompt_id,
        )
    except EchoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    crisis = CrisisInterstitial(**CRISIS_RESOURCES) if verdict == VERDICT_CRISIS else None
    return EchoCreateResponse(
        echo=_echo_resp(echo, current_user.handle),
        held_for_review=echo.status == "held",
        crisis=crisis,
    )


@router.get("/feed", response_model=FeedResponse)
async def get_feed(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    book_title: str | None = Query(default=None, max_length=300),
    book_author: str | None = Query(default=None, max_length=200),
    emotion: str | None = Query(default=None, max_length=30),
    prompt_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Chronological, block-filtered feed that ends. No counts. Optional anchors:
    a book (title[+author]) for the 'A Book' feed, an emotion for 'A Feeling', or a
    prompt_id for the answers to one weekly Prompt (the campfire)."""
    book_key = _book_key(book_title, book_author) if book_title else None
    try:
        echoes, next_cursor = await list_feed(
            db, current_user.id, limit=limit, cursor=cursor,
            book_key=book_key, emotion=emotion, prompt_id=prompt_id,
        )
    except EchoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    ann = await annotate_feed(db, echoes, current_user.id)
    return FeedResponse(
        echoes=[_echo_resp(e, e.author.handle, ann, current_user.id) for e in echoes],
        next_cursor=next_cursor,
        caught_up=next_cursor is None,
    )


@router.get("/{echo_id}", response_model=EchoThreadResponse)
async def get_echo_thread(
    echo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """An echo and its replies (replies before reactions — conversation first)."""
    echo = await get_visible_echo(db, echo_id, current_user.id)
    if echo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    replies = await list_replies(db, echo_id, current_user.id)
    return EchoThreadResponse(
        echo=_echo_resp(echo, echo.author.handle),
        replies=[_reply_resp(r) for r in replies],
    )


@router.delete("/{echo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_echo(
    echo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Author removes their own echo."""
    echo = (await db.execute(select(Echo).where(Echo.id == echo_id))).scalar_one_or_none()
    if echo is None or echo.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    echo.status = "removed"
    await db.flush()


@router.post("/{echo_id}/replies", response_model=ReplyResponse, status_code=status.HTTP_201_CREATED)
async def post_reply(
    echo_id: uuid.UUID,
    data: ReplyCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await echo_write_limiter.check(request)
    echo = await get_visible_echo(db, echo_id, current_user.id)
    if echo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    try:
        reply, verdict, reason = await create_reply(db, echo, current_user.id, data.body)
    except EchoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Tier-1 batched notification to the echo's author ("N readers responded…").
    # notify() suppresses self-replies and blocked actors.
    if reply.status == "active":
        await notify(
            db,
            echo.author_id,
            TIER_DIRECT,
            "echo_reply",
            payload={
                "echo_id": str(echo_id),
                "book_title": echo.book_title,
                "actors": [current_user.handle],
                "count": 1,
            },
            batch_key=f"echo_reply:{echo_id}",
            actor_id=current_user.id,
        )

    return ReplyResponse(
        id=reply.id, echo_id=echo_id, handle=current_user.handle, body=reply.body, created_at=reply.created_at,
    )


@router.post("/{echo_id}/react", response_model=ReactionResponse)
async def react_to_echo(
    echo_id: uuid.UUID,
    data: ReactionUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set/unset a private reaction and echo back the new state (B6.2).

    Returns the viewer's `my_reactions`; the author additionally gets the private
    `reaction_counts`. `adding_to_list = on` also adds the book to the viewer's
    shelf (B6.3) and reports `added_to_shelf`. Public counts are never returned.
    """
    await react_limiter.check(request)
    echo = await get_visible_echo(db, echo_id, current_user.id)
    if echo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    try:
        await set_reaction(db, echo_id, current_user.id, data.kind, data.on)
    except EchoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # "To my shelf" performs a real act of reading life. Deliberately asymmetric:
    # turning it ON may create a want_to_read entry; turning it OFF never deletes it.
    added_to_shelf = False
    if data.kind == "adding_to_list" and data.on:
        added_to_shelf = await add_book_to_shelf(db, current_user.id, echo)

    mine = await viewer_reactions(db, echo_id, current_user.id)
    counts = await reaction_counts(db, echo_id) if echo.author_id == current_user.id else None
    return ReactionResponse(my_reactions=mine, reaction_counts=counts, added_to_shelf=added_to_shelf)


@router.get("/{echo_id}/reactions")
async def get_reactions(
    echo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Private aggregate — only the echo's author may read it."""
    echo = (await db.execute(select(Echo).where(Echo.id == echo_id))).scalar_one_or_none()
    if echo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    if echo.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reactions are private to the author")
    return await reaction_counts(db, echo_id)


@router.post("/{echo_id}/report", status_code=status.HTTP_202_ACCEPTED)
async def report_echo(
    echo_id: uuid.UUID,
    data: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    echo = (await db.execute(select(Echo).where(Echo.id == echo_id))).scalar_one_or_none()
    if echo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Echo not found")
    await submit_report(db, current_user.id, "echo", echo_id, data.category)
    return {"status": "received"}


@router.post("/{echo_id}/replies/{reply_id}/report", status_code=status.HTTP_202_ACCEPTED)
async def report_reply(
    echo_id: uuid.UUID,
    reply_id: uuid.UUID,
    data: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reply = (await db.execute(
        select(EchoReply).where(EchoReply.id == reply_id, EchoReply.echo_id == echo_id)
    )).scalar_one_or_none()
    if reply is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found")
    await submit_report(db, current_user.id, "reply", reply_id, data.category)
    return {"status": "received"}
