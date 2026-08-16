import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.notification import TIER_DIRECT
from app.models.user import User
from app.services.collection_chat_service import (
    MESSAGE_PAGE_DEFAULT,
    MESSAGE_PAGE_MAX,
    ChatError,
    ChatRefused,
    book_is_in_collection,
    list_conversations,
    list_messages,
)
from app.services.collection_chat_service import delete_message as delete_chat_message
from app.services.collection_chat_service import notify_targets as chat_notify_targets
from app.services.collection_chat_service import post_message as post_chat_message
from app.services.moderation import CRISIS_RESOURCES, VERDICT_CRISIS, submit_report
from app.services.notification_service import notify
from app.schemas.profile import (
    CollectionBookAdd,
    CollectionConversation,
    CollectionCreate,
    CollectionInviteCreate,
    CollectionInvitePeek,
    CollectionInviteResponse,
    CollectionItemAdd,
    CollectionJoinResponse,
    CollectionMemberResponse,
    CollectionMessageCreate,
    CollectionMessageList,
    CollectionMessageResponse,
    CollectionReorder,
    CollectionResponse,
    CollectionUpdate,
    ProfileUpdate,
)
from app.services.collection_service import (
    CollectionError,
    CollectionForbidden,
    add_book,
    add_item,
    create_collection,
    create_invite,
    delete_collection,
    ensure_owner_membership,
    get_owned_collection,
    get_visible_collection,
    list_members,
    peek_invite,
    redeem_invite,
    remove_book,
    remove_item,
    remove_member,
    reorder_items,
    revoke_invite,
    update_collection,
)
from app.services.handle_service import resolve_handle
from app.services.profile_service import compose_profile

router = APIRouter(tags=["profile"])

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_bio(text: str | None) -> str | None:
    if text is None:
        return None
    return _CONTROL_RE.sub("", text).strip() or None


# ── Profile ──

@router.get("/me/profile")
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The self-view: full profile, editable client-side."""
    return await compose_profile(db, current_user.id, current_user)


@router.patch("/me/profile")
async def update_my_profile(
    data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = data.model_dump(exclude_unset=True)
    if "bio" in update:
        update["bio"] = _sanitize_bio(update["bio"])
    for field, value in update.items():
        setattr(current_user, field, value)
    await db.flush()
    return await compose_profile(db, current_user.id, current_user)


@router.get("/profile/{handle}")
async def get_profile(
    handle: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Another reader's profile — visibility + block enforced server-side.
    Blocked or unknown → 404; private-to-stranger → minimal card."""
    owner, canonical = await resolve_handle(db, handle)
    if owner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")
    profile = await compose_profile(db, current_user.id, owner)
    if profile is None:  # blocked → appears not to exist
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such profile")
    profile["canonical_handle"] = canonical  # supports "previously known as" redirect
    return profile


# ── Collections ──

@router.post("/collections", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def post_collection(
    data: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        c = await create_collection(db, current_user.id, data.title, data.description, data.visibility)
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return CollectionResponse(id=c.id, title=c.title, description=c.description, visibility=c.visibility, position=c.position)


async def _owned_or_404(db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID):
    c = await get_owned_collection(db, collection_id, user_id)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return c


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def patch_collection(
    collection_id: uuid.UUID,
    data: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    try:
        await update_collection(db, c, **data.model_dump(exclude_unset=True))
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return CollectionResponse(id=c.id, title=c.title, description=c.description, visibility=c.visibility, position=c.position)


@router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection_endpoint(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await delete_collection(db, c)


@router.post("/collections/{collection_id}/items", status_code=status.HTTP_204_NO_CONTENT)
async def add_collection_item(
    collection_id: uuid.UUID,
    data: CollectionItemAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    try:
        await add_item(db, c, data.entry_id, current_user.id)
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete("/collections/{collection_id}/items/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection_item(
    collection_id: uuid.UUID,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await remove_item(db, c, entry_id)


@router.patch("/collections/{collection_id}/reorder", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_collection(
    collection_id: uuid.UUID,
    data: CollectionReorder,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    c = await _owned_or_404(db, collection_id, current_user.id)
    await reorder_items(db, c, data.entry_ids)


# ── Shared collections (#5) ──
#
# Owner-only routes keep using `_owned_or_404`. Anything a member can reach goes
# through `_visible_or_404`, so membership is the single gate.

async def _visible_or_404(db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID):
    c, member = await get_visible_collection(db, collection_id, user_id)
    if c is None:
        # 404, not 403: a non-member must not be able to probe which collection
        # ids exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collection not found")
    return c, member


@router.post(
    "/collections/{collection_id}/invites",
    response_model=CollectionInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection_invite(
    collection_id: uuid.UUID,
    data: CollectionInviteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mint an invite link. Owner only.

    The raw token is in this response and nowhere else — only its hash is stored,
    so it can never be read back. Losing it means minting another.
    """
    c = await _owned_or_404(db, collection_id, current_user.id)
    await ensure_owner_membership(db, c)
    try:
        invite, raw = await create_invite(
            db, c, current_user.id,
            expires_at=data.expires_at, max_uses=data.max_uses,
        )
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return CollectionInviteResponse(
        id=invite.id,
        token=raw,
        expires_at=invite.expires_at,
        max_uses=invite.max_uses,
    )


@router.delete(
    "/collections/{collection_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_collection_invite(
    collection_id: uuid.UUID,
    invite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kill a link. Already-joined members stay — revoking is about the door, not
    the people who came through it."""
    c = await _owned_or_404(db, collection_id, current_user.id)
    await revoke_invite(db, c, invite_id)


@router.get("/collections/invites/{token}", response_model=CollectionInvitePeek)
async def peek_collection_invite(
    token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """What this link points at, without joining — so the join screen can name
    the collection instead of asking someone to accept a blind invitation."""
    c = await peek_invite(db, token)
    if c is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This invite has expired or been revoked",
        )
    members = await list_members(db, c.id)
    return CollectionInvitePeek(
        collection_id=c.id,
        title=c.title,
        description=c.description,
        member_count=len(members),
        book_count=len(c.items),
        already_member=any(m.user_id == current_user.id for m in members),
    )


@router.post("/collections/invites/{token}/join", response_model=CollectionJoinResponse)
async def join_collection(
    token: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Join by link. Clicking twice is not an error — it reports joined:false."""
    c, joined = await redeem_invite(db, token, current_user.id)
    if c is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This invite has expired or been revoked",
        )
    return CollectionJoinResponse(collection_id=c.id, title=c.title, joined=joined)


@router.get(
    "/collections/{collection_id}/members",
    response_model=list[CollectionMemberResponse],
)
async def get_collection_members(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Who is in here. Members only — the list is not public."""
    await _visible_or_404(db, collection_id, current_user.id)
    return [
        CollectionMemberResponse(
            user_id=m.user_id,
            handle=getattr(m.user, "handle", None),
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in await list_members(db, collection_id)
    ]


@router.delete(
    "/collections/{collection_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_collection_member(
    collection_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a member, or leave yourself. The owner can do the former; anyone
    can do the latter to themselves. The owner cannot leave their own collection.
    """
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    if user_id != current_user.id and current_user.id != c.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can remove other members",
        )
    try:
        await remove_member(db, c, user_id)
    except CollectionForbidden as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/collections/{collection_id}/books", status_code=status.HTTP_204_NO_CONTENT)
async def add_collection_book(
    collection_id: uuid.UUID,
    data: CollectionBookAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a book by canonical id. Any member may add."""
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    try:
        await add_book(db, c, data.book_id, current_user.id)
    except CollectionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.delete(
    "/collections/{collection_id}/books/{book_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection_book(
    collection_id: uuid.UUID,
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove a book. Members may remove only what they added; the owner, any."""
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    try:
        await remove_book(db, c, book_id, current_user.id)
    except CollectionForbidden as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


# ── Collection chat (#6) ──
#
# Routed under the collection and anchored to a book. There is deliberately no
# "general" room: a collection is a set of books, and a general channel would
# turn it into a group chat that happens to have books in it.

chat_limiter = RateLimiter(max_requests=120, window_seconds=3600, prefix="collection_msg")


@router.get(
    "/collections/{collection_id}/conversations",
    response_model=list[CollectionConversation],
)
async def get_collection_conversations(
    collection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every book in the collection, with when it was last spoken about.

    Books with nothing said yet are included — this shows where a conversation
    *could* start, not only where one already has.
    """
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    return [CollectionConversation(**row) for row in await list_conversations(db, c, current_user.id)]


@router.get(
    "/collections/{collection_id}/books/{book_id}/messages",
    response_model=CollectionMessageList,
)
async def get_collection_messages(
    collection_id: uuid.UUID,
    book_id: uuid.UUID,
    before: datetime | None = Query(default=None, description="Page backward from this timestamp"),
    before_id: uuid.UUID | None = Query(default=None, description="Tie-breaker for `before`"),
    limit: int = Query(default=MESSAGE_PAGE_DEFAULT, ge=1, le=MESSAGE_PAGE_MAX),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One book's conversation, oldest-first. Page backward with `before` +
    `before_id`.

    History survives the book leaving the collection, but is only *reachable*
    while it is in — so this 404s for a book the collection no longer holds,
    rather than serving a room nobody can post to.
    """
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    if not await book_is_in_collection(db, c.id, book_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That book isn't in this collection",
        )

    messages = await list_messages(
        db, c.id, book_id, current_user.id, limit=limit, before=before, before_id=before_id,
    )

    # One lookup for every handle on the page, rather than per message.
    sender_ids = {m.sender_id for m in messages}
    handles = dict((await db.execute(
        select(User.id, User.handle).where(User.id.in_(sender_ids))
    )).all()) if sender_ids else {}

    return CollectionMessageList(
        messages=[
            CollectionMessageResponse(
                id=m.id,
                book_id=m.book_id,
                handle=handles.get(m.sender_id),
                is_mine=m.sender_id == current_user.id,
                body=m.body,
                created_at=m.created_at,
            )
            for m in messages
        ],
        # Only offered on a full page — a short page is the start of the
        # conversation, and there is nothing further back to ask for.
        next_before=messages[0].created_at if len(messages) == limit else None,
        next_before_id=messages[0].id if len(messages) == limit else None,
    )


@router.post(
    "/collections/{collection_id}/books/{book_id}/messages",
    response_model=CollectionMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_collection_message(
    collection_id: uuid.UUID,
    book_id: uuid.UUID,
    data: CollectionMessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Say something about a book in this collection. Any member may."""
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    await chat_limiter.check_key(str(current_user.id))

    try:
        message, verdict = await post_chat_message(db, c, book_id, current_user.id, data.body)
    except ChatRefused as e:
        # 422, not 400: the request was well-formed, the content is what was
        # refused. The sender is told plainly rather than left to wonder whether
        # it sent.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ChatError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Membership is read live, so someone who left stops being notified about a
    # room they can no longer open. `notify` drops self- and blocked-pairs too.
    for uid in await chat_notify_targets(db, c.id, current_user.id):
        await notify(
            db, uid, TIER_DIRECT, "collection_message",
            {"collection_id": str(c.id), "book_id": str(book_id)},
            # Coalesce a burst into one nudge per book — the conversation is the
            # place to read them, not the notification list.
            batch_key=f"collection_message:{c.id}:{book_id}",
            actor_id=current_user.id,
        )

    return CollectionMessageResponse(
        id=message.id,
        book_id=message.book_id,
        handle=current_user.handle,
        is_mine=True,
        body=message.body,
        created_at=message.created_at,
        crisis=dict(CRISIS_RESOURCES) if verdict == VERDICT_CRISIS else None,
    )


@router.delete(
    "/collections/{collection_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection_message(
    collection_id: uuid.UUID,
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a message. Authors may delete their own; the owner, any."""
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    try:
        await delete_chat_message(db, c, message_id, current_user.id)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post(
    "/collections/{collection_id}/books/{book_id}/report",
    status_code=status.HTTP_202_ACCEPTED,
)
async def report_collection_conversation(
    collection_id: uuid.UUID,
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report a conversation to moderation.

    The whole conversation is the target, not a single message — the same call
    thread reporting makes, and for the same reason: what is wrong is usually a
    pattern rather than one line. Reporting does NOT auto-hide the room: a
    private group must not be silenceable on one member's say-so. Blocking is the
    remedy the reporter holds themselves.
    """
    c, _ = await _visible_or_404(db, collection_id, current_user.id)
    if not await book_is_in_collection(db, c.id, book_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await submit_report(db, current_user.id, "collection_conversation", book_id, "conversation")
    return {"status": "received"}
