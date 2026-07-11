"""Echo core: create / feed / replies / reactions (B3.2–B3.5).

Feeds are chronological, keyset-paginated, block-filtered, and carry NO counts of
any kind (blueprint Feature 1). An Echo must be anchored to a book and/or a
canonical emotion — there is no freeform posting.
"""

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.echo import Echo, EchoReaction, EchoReply
from app.services.book_search import normalize
from app.services.moderation import VERDICT_CRISIS, VERDICT_HOLD, classify_text
from app.services.social_service import hidden_author_ids, is_blocked_between
from app.utils.emotions import canonicalize


class EchoError(ValueError):
    """Bad echo input (router maps to 400)."""


def _book_key(title: str | None, author: str | None) -> str | None:
    if not title:
        return None
    return f"{normalize(title)}|{normalize(author or '')}"


def _encode_cursor(echo: Echo) -> str:
    return f"{echo.created_at.isoformat()}|{echo.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        ts_raw, id_raw = cursor.rsplit("|", 1)
        return datetime.fromisoformat(ts_raw), uuid.UUID(id_raw)
    except (ValueError, AttributeError):
        raise EchoError("Invalid feed cursor")


async def create_echo(
    db: AsyncSession,
    author_id: uuid.UUID,
    body: str,
    book_title: str | None,
    book_author: str | None,
    primary_emotion: str | None,
    secondary_emotion: str | None,
    visibility: str,
) -> tuple[Echo, str, str | None]:
    """Create an echo. Returns (echo, verdict, reason).

    Anchoring (book or emotion) is required. The pre-publish classifier can hold
    the echo for review (threats/PII) or route the author to the crisis path
    (self-harm) — in both cases the echo is created but held, never public.
    """
    body = (body or "").strip()
    if not body:
        raise EchoError("Echo body is required")
    if visibility not in ("community", "public"):
        raise EchoError("visibility must be 'community' or 'public'")

    prim = canonicalize(primary_emotion) if primary_emotion else None
    sec = canonicalize(secondary_emotion) if secondary_emotion else None
    if primary_emotion and prim is None:
        raise EchoError(f"Invalid emotion: {primary_emotion}")
    if secondary_emotion and sec is None:
        raise EchoError(f"Invalid emotion: {secondary_emotion}")

    title = (book_title or "").strip() or None
    if not title and not prim:
        raise EchoError("An echo must be anchored to a book and/or an emotion")

    verdict, reason = classify_text(body)
    status = "held" if verdict in (VERDICT_HOLD, VERDICT_CRISIS) else "active"

    echo = Echo(
        author_id=author_id,
        book_key=_book_key(title, book_author),
        book_title=title,
        book_author=(book_author or "").strip() or None,
        primary_emotion=prim,
        secondary_emotion=sec,
        body=body,
        visibility=visibility,
        status=status,
    )
    db.add(echo)
    await db.flush()
    return echo, verdict, reason


def _visibility_filter(viewer_id: uuid.UUID | None):
    """Which visibilities a viewer may see (author's own handled separately)."""
    if viewer_id is None:
        return Echo.visibility == "public"          # anon: public only, noindex handled at edge
    return Echo.visibility.in_(("public", "community"))  # signed-in members: both


async def list_feed(
    db: AsyncSession,
    viewer_id: uuid.UUID | None,
    limit: int = 20,
    cursor: str | None = None,
    book_key: str | None = None,
    emotion: str | None = None,
) -> tuple[list[Echo], str | None]:
    """Chronological, keyset-paginated, block-filtered feed. No counts anywhere.

    Optional anchors: `book_key` (the "A Book" feed) or `emotion` (the "A Feeling"
    feed). Returns (echoes, next_cursor); next_cursor is None at the end ("caught up").
    """
    hidden = await hidden_author_ids(db, viewer_id)

    query = (
        select(Echo)
        .options(selectinload(Echo.author))
        .where(Echo.status == "active", _visibility_filter(viewer_id))
        .order_by(Echo.created_at.desc(), Echo.id.desc())
    )
    if hidden:
        query = query.where(Echo.author_id.notin_(hidden))
    if book_key:
        query = query.where(Echo.book_key == book_key)
    if emotion:
        canon = canonicalize(emotion) or emotion
        query = query.where(
            or_(Echo.primary_emotion == canon, Echo.secondary_emotion == canon)
        )
    if cursor:
        cts, cid = _decode_cursor(cursor)
        query = query.where(tuple_(Echo.created_at, Echo.id) < (cts, cid))

    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_next = len(rows) > limit
    echoes = rows[:limit]
    next_cursor = _encode_cursor(echoes[-1]) if has_next and echoes else None
    return echoes, next_cursor


async def get_visible_echo(db: AsyncSession, echo_id: uuid.UUID, viewer_id: uuid.UUID | None) -> Echo | None:
    result = await db.execute(
        select(Echo).options(selectinload(Echo.author)).where(Echo.id == echo_id)
    )
    echo = result.scalar_one_or_none()
    if echo is None:
        return None
    if echo.author_id == viewer_id:
        return echo  # author always sees their own
    if echo.status != "active":
        return None
    if echo.visibility == "community" and viewer_id is None:
        return None
    if viewer_id is not None and await is_blocked_between(db, viewer_id, echo.author_id):
        return None
    return echo


async def create_reply(db: AsyncSession, echo: Echo, author_id: uuid.UUID, body: str) -> tuple[EchoReply, str, str | None]:
    body = (body or "").strip()
    if not body:
        raise EchoError("Reply body is required")
    if await is_blocked_between(db, author_id, echo.author_id):
        raise EchoError("Cannot reply")

    verdict, reason = classify_text(body)
    status = "held" if verdict in (VERDICT_HOLD, VERDICT_CRISIS) else "active"
    reply = EchoReply(echo_id=echo.id, author_id=author_id, body=body, status=status)
    db.add(reply)
    await db.flush()
    return reply, verdict, reason


async def list_replies(db: AsyncSession, echo_id: uuid.UUID, viewer_id: uuid.UUID | None) -> list[EchoReply]:
    hidden = await hidden_author_ids(db, viewer_id)
    query = (
        select(EchoReply)
        .options(selectinload(EchoReply.author))
        .where(EchoReply.echo_id == echo_id, EchoReply.status == "active")
        .order_by(EchoReply.created_at.asc())
    )
    if hidden:
        query = query.where(EchoReply.author_id.notin_(hidden))
    return list((await db.execute(query)).scalars().all())


async def set_reaction(db: AsyncSession, echo_id: uuid.UUID, user_id: uuid.UUID, kind: str, on: bool) -> None:
    from app.models.echo import REACTION_KINDS
    if kind not in REACTION_KINDS:
        raise EchoError(f"Invalid reaction: {kind}")
    if on:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(EchoReaction).values(echo_id=echo_id, user_id=user_id, kind=kind).on_conflict_do_nothing(
            constraint="uq_echo_reaction"
        )
        await db.execute(stmt)
    else:
        result = await db.execute(
            select(EchoReaction).where(
                EchoReaction.echo_id == echo_id,
                EchoReaction.user_id == user_id,
                EchoReaction.kind == kind,
            )
        )
        r = result.scalar_one_or_none()
        if r:
            await db.delete(r)
    await db.flush()


async def reaction_counts(db: AsyncSession, echo_id: uuid.UUID) -> dict[str, int]:
    """Private aggregate — only ever exposed to the echo's author."""
    from sqlalchemy import func
    result = await db.execute(
        select(EchoReaction.kind, func.count(EchoReaction.id))
        .where(EchoReaction.echo_id == echo_id)
        .group_by(EchoReaction.kind)
    )
    return {kind: n for kind, n in result.all()}
