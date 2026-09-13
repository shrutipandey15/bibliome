"""Threads: the conversation that exists once both readers said yes.

Free text, no topic restriction — the anchor did its job at the match stage.
Access is party-only; anyone else gets 404 rather than 403, so the endpoint never
confirms a thread they aren't in.

Safety lives here rather than in the message pipeline: block closes the thread
and takes effect everywhere (the same cross-surface Block as Echo); report files
the whole thread to the moderation queue, and blocks by default. Neither tells
the other party anything — the conversation simply stops.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.book import Book
from app.models.notification import TIER_DIRECT
from app.models.resonance import ResonanceMessage, ResonanceMessageReaction
from app.models.user import User
from app.schemas.resonance import (
    ChatReactionResponse,
    ChatReactionUpdate,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    ReplyPreview,
    ThreadReportRequest,
    ThreadResponse,
)
from app.schemas.echo import CrisisInterstitial
from app.services.moderation import CRISIS_RESOURCES, VERDICT_CRISIS, submit_report
from app.services.notification_service import notify
from app.services.resonance_service import (
    ResonanceError,
    annotate_messages,
    close_thread,
    get_thread_for_user,
    list_messages,
    list_threads,
    post_message,
)
from app.services.resonance_service import set_reaction as set_thread_reaction
from app.services.social_service import block_user
from app.utils.attachments import save_chat_image

router = APIRouter(prefix="/threads", tags=["resonance"])

message_limiter = RateLimiter(max_requests=120, window_seconds=3600, prefix="thread_msg")

MESSAGE_PAGE_DEFAULT = 50
MESSAGE_PAGE_MAX = 100


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")


async def _load(db: AsyncSession, thread_id: uuid.UUID, user: User):
    found = await get_thread_for_user(db, thread_id, user.id)
    if found is None:
        raise _not_found()
    return found


@router.get("", response_model=list[ThreadResponse])
async def get_threads(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your open conversations. Private: there is no endpoint that lists anyone
    else's threads, and no count of them anywhere."""
    threads = await list_threads(db, current_user.id)
    out: list[ThreadResponse] = []
    for thread in threads:
        match = thread.match
        other_id = match.other_id(current_user.id)
        handle = (
            await db.execute(select(User.handle).where(User.id == other_id))
        ).scalar_one_or_none()
        title = (
            await db.execute(select(Book.title).where(Book.id == match.book_id))
        ).scalar_one_or_none()
        out.append(
            ThreadResponse(
                thread_id=thread.id,
                match_id=match.id,
                book_id=match.book_id,
                book_title=title,
                handle=handle or "",
                status=thread.status,
                created_at=thread.created_at,
            )
        )
    return out


@router.get("/{thread_id}/messages", response_model=MessageListResponse)
async def get_messages(
    thread_id: uuid.UUID,
    before: datetime | None = Query(default=None, description="Page backward from this timestamp"),
    after: datetime | None = Query(default=None, description="Only what arrived after this — the live poll"),
    limit: int = Query(default=MESSAGE_PAGE_DEFAULT, ge=1, le=MESSAGE_PAGE_MAX),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A page of the transcript, oldest-first. Page backward with `before`;
    `after` is the live poll — only what has arrived since."""
    thread, match = await _load(db, thread_id, current_user)

    messages = await list_messages(db, thread, limit=limit, before=before, after=after)

    handles = {
        current_user.id: current_user.handle,
        match.other_id(current_user.id): (
            await db.execute(select(User.handle).where(User.id == match.other_id(current_user.id)))
        ).scalar_one_or_none() or "",
    }

    counts, mine, previews = await annotate_messages(db, messages, current_user.id)

    return MessageListResponse(
        messages=[
            MessageResponse(
                id=m.id,
                thread_id=m.thread_id,
                handle=handles.get(m.sender_id, ""),
                is_mine=m.sender_id == current_user.id,
                body=m.body,
                created_at=m.created_at,
                reply_to=ReplyPreview(**previews[m.id]) if m.id in previews else None,
                reaction_counts=counts.get(m.id, {}),
                my_reactions=mine.get(m.id, []),
                # No leading /api: the frontend's apiFetch adds that base itself
                # (see src/services/api.js API_BASE) and this URL is fetched
                # through it — as a Bearer-authenticated blob, not a plain <img
                # src>, since this API has no auth cookie for ordinary requests.
                attachment_url=f"/threads/{thread_id}/messages/{m.id}/attachment" if m.attachment_path else None,
            )
            for m in messages
        ],
        # A backward page is only offered when it came back full; a poll never
        # offers one, because it is reading the newest end.
        next_before=messages[0].created_at if (after is None and len(messages) == limit) else None,
    )


@router.post("/{thread_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    thread_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a message. Plain free text — no emotion tag, no book anchor, no prompt."""
    thread, match = await _load(db, thread_id, current_user)
    await message_limiter.check_key(str(current_user.id))

    try:
        message, verdict, _reason = await post_message(
            db, thread, current_user.id, data.body, reply_to_id=data.reply_to_id,
        )
    except ResonanceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    reply_to = None
    if message.reply_to_id:
        quoted = (await db.execute(
            select(ResonanceMessage.body, User.handle)
            .join(User, User.id == ResonanceMessage.sender_id)
            .where(ResonanceMessage.id == message.reply_to_id)
        )).first()
        if quoted:
            reply_to = ReplyPreview(id=message.reply_to_id, handle=quoted.handle, body=quoted.body)

    await notify(
        db,
        match.other_id(current_user.id),
        TIER_DIRECT,
        "resonance_message",
        {"thread_id": str(thread.id)},
        # Coalesce a burst of messages into one nudge — the conversation is the
        # place to read them, not the notification list.
        batch_key=f"resonance_message:{thread.id}",
        actor_id=current_user.id,
    )

    return MessageResponse(
        id=message.id,
        thread_id=message.thread_id,
        handle=current_user.handle,
        is_mine=True,
        body=message.body,
        created_at=message.created_at,
        crisis=CrisisInterstitial(**CRISIS_RESOURCES) if verdict == VERDICT_CRISIS else None,
        reply_to=reply_to,
    )


@router.post(
    "/{thread_id}/messages/image", response_model=MessageResponse, status_code=status.HTTP_201_CREATED,
)
async def send_image_message(
    thread_id: uuid.UUID,
    body: str = Form(...),
    reply_to_id: uuid.UUID | None = Form(default=None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a letter with a photo attached — a page, a margin note, a cover.
    A separate multipart endpoint rather than widening `send_message` to accept
    either JSON or a file: that would change an existing contract every caller
    already depends on. Same rate limit as a plain letter — an attachment isn't
    a way around the per-hour cap."""
    thread, match = await _load(db, thread_id, current_user)
    await message_limiter.check_key(str(current_user.id))

    path, content_type = await save_chat_image(file)
    try:
        message, verdict, _reason = await post_message(
            db, thread, current_user.id, body, reply_to_id=reply_to_id,
            attachment_path=path, attachment_type=content_type,
        )
    except ResonanceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    reply_to = None
    if message.reply_to_id:
        quoted = (await db.execute(
            select(ResonanceMessage.body, User.handle)
            .join(User, User.id == ResonanceMessage.sender_id)
            .where(ResonanceMessage.id == message.reply_to_id)
        )).first()
        if quoted:
            reply_to = ReplyPreview(id=message.reply_to_id, handle=quoted.handle, body=quoted.body)

    await notify(
        db, match.other_id(current_user.id), TIER_DIRECT, "resonance_message",
        {"thread_id": str(thread.id)},
        batch_key=f"resonance_message:{thread.id}",
        actor_id=current_user.id,
    )

    return MessageResponse(
        id=message.id,
        thread_id=message.thread_id,
        handle=current_user.handle,
        is_mine=True,
        body=message.body,
        created_at=message.created_at,
        crisis=CrisisInterstitial(**CRISIS_RESOURCES) if verdict == VERDICT_CRISIS else None,
        reply_to=reply_to,
        attachment_url=f"/threads/{thread_id}/messages/{message.id}/attachment",
    )


@router.get("/{thread_id}/messages/{message_id}/attachment")
async def get_message_attachment(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The photo attached to one letter. Party-only, same 404-not-403 rule as
    the rest of this router — the file itself never sits behind a public or
    guessable static URL."""
    thread, _match = await _load(db, thread_id, current_user)
    path, content_type = (await db.execute(
        select(ResonanceMessage.attachment_path, ResonanceMessage.attachment_type).where(
            ResonanceMessage.id == message_id, ResonanceMessage.thread_id == thread.id,
        )
    )).first() or (None, None)
    if not path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No attachment here")
    return FileResponse(path, media_type=content_type or "application/octet-stream")


@router.post("/{thread_id}/messages/{message_id}/react", response_model=ChatReactionResponse)
async def react_to_thread_message(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    data: ChatReactionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set/unset a reaction. Public within the thread — both readers already
    know who's who once a thread exists, unlike Echo's stranger-facing feed."""
    thread, _match = await _load(db, thread_id, current_user)
    sender_id = (await db.execute(
        select(ResonanceMessage.sender_id).where(
            ResonanceMessage.id == message_id, ResonanceMessage.thread_id == thread.id,
        )
    )).scalar_one_or_none()
    if sender_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    try:
        await set_thread_reaction(db, message_id, current_user.id, data.kind, data.on)
    except ResonanceError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Only on adding a reaction, and only to the letter's own author — a removal
    # is not news, and notify() already drops the self-reaction case.
    if data.on:
        await notify(
            db, sender_id, TIER_DIRECT, "chat_reaction",
            {"thread_id": str(thread.id), "message_id": str(message_id), "kind": data.kind, "actors": [current_user.handle], "count": 1},
            batch_key=f"chat_reaction:{message_id}",
            actor_id=current_user.id,
        )

    rows = (await db.execute(
        select(ResonanceMessageReaction.kind, ResonanceMessageReaction.user_id)
        .where(ResonanceMessageReaction.message_id == message_id)
    )).all()
    counts: dict[str, int] = {}
    my_kinds: list[str] = []
    for kind, uid in rows:
        counts[kind] = counts.get(kind, 0) + 1
        if uid == current_user.id:
            my_kinds.append(kind)

    return ChatReactionResponse(my_reactions=my_kinds, reaction_counts=counts)


@router.post("/{thread_id}/block", status_code=status.HTTP_204_NO_CONTENT)
async def block_thread(
    thread_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block the other reader: closes this conversation, declines the match, and
    hides both parties from each other everywhere else too. Silent — they are
    told nothing, the thread simply stops."""
    thread, match = await _load(db, thread_id, current_user)
    await block_user(db, current_user.id, match.other_id(current_user.id))
    await close_thread(db, thread, current_user.id)


@router.post("/{thread_id}/report", status_code=status.HTTP_202_ACCEPTED)
async def report_thread(
    thread_id: uuid.UUID,
    data: ThreadReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report a conversation to moderation, blocking by default. Reporting does
    not require a message id: the whole thread is the target."""
    thread, match = await _load(db, thread_id, current_user)
    await submit_report(db, current_user.id, "thread", thread.id, data.category)

    if data.block:
        await block_user(db, current_user.id, match.other_id(current_user.id))
        await close_thread(db, thread, current_user.id)

    return {"status": "received"}
