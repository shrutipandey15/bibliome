"""Echo core: create / feed / replies / reactions (B3.2–B3.5).

Feeds are chronological, keyset-paginated, block-filtered, and carry NO counts of
any kind (blueprint Feature 1). An Echo must be anchored to a book and/or a
canonical emotion — there is no freeform posting.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.echo import Echo, EchoReaction, EchoReply
from app.models.prompt import Prompt
from app.models.user import User
from app.services.book_identity import resolve_book
from app.services.book_search import normalize
from app.services.moderation import VERDICT_CRISIS, VERDICT_HOLD, classify_text
from app.services.social_service import hidden_author_ids, is_blocked_between
from app.utils.emotions import canonicalize

# How many replies the feed renders inline under each echo (blueprint: shown, not counted).
REPLY_PREVIEW_LIMIT = 2


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
    prompt_id: uuid.UUID | None = None,
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

    if prompt_id is not None:
        exists = (await db.execute(select(Prompt.id).where(Prompt.id == prompt_id))).scalar_one_or_none()
        if exists is None:
            raise EchoError("Unknown prompt")

    verdict, reason = classify_text(body)
    status = "held" if verdict in (VERDICT_HOLD, VERDICT_CRISIS) else "active"

    echo = Echo(
        author_id=author_id,
        book_key=_book_key(title, book_author),
        book_title=title,
        book_author=(book_author or "").strip() or None,
        primary_emotion=prim,
        secondary_emotion=sec,
        prompt_id=prompt_id,
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
    prompt_id: uuid.UUID | None = None,
    mine: bool = False,
) -> tuple[list[Echo], str | None]:
    """Chronological, keyset-paginated, block-filtered feed. No counts anywhere.

    Optional anchors: `book_key` (the "A Book" feed), `emotion` (the "A Feeling"
    feed), `prompt_id` (answers to one weekly Prompt — the campfire), or `mine`
    (the author's own echoes). They compose — "my echoes tagged grief" is one
    query, not a client-side filter over a page of everyone's. Returns
    (echoes, next_cursor); next_cursor is None at the end ("caught up").
    """
    hidden = await hidden_author_ids(db, viewer_id)

    query = (
        select(Echo)
        .options(selectinload(Echo.author))
        .where(Echo.status == "active", _visibility_filter(viewer_id))
        .order_by(Echo.created_at.desc(), Echo.id.desc())
    )
    if mine:
        # Composes with every other anchor rather than replacing them, so
        # "my echoes tagged grief" is one query instead of a client-side filter
        # over a page of everyone's. Safe against the visibility filter above:
        # echoes are only ever community|public, never private, so an author's
        # own echoes are never excluded by it.
        if viewer_id is None:
            return [], None  # anonymous viewer has no "mine"
        query = query.where(Echo.author_id == viewer_id)
    if hidden:
        query = query.where(Echo.author_id.notin_(hidden))
    if book_key:
        query = query.where(Echo.book_key == book_key)
    if emotion:
        canon = canonicalize(emotion) or emotion
        query = query.where(
            or_(Echo.primary_emotion == canon, Echo.secondary_emotion == canon)
        )
    if prompt_id:
        query = query.where(Echo.prompt_id == prompt_id)
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
    result = await db.execute(
        select(EchoReaction.kind, func.count(EchoReaction.id))
        .where(EchoReaction.echo_id == echo_id)
        .group_by(EchoReaction.kind)
    )
    return {kind: n for kind, n in result.all()}


async def viewer_reactions(db: AsyncSession, echo_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    """Which reaction kinds this viewer currently has set on one echo."""
    result = await db.execute(
        select(EchoReaction.kind).where(
            EchoReaction.echo_id == echo_id, EchoReaction.user_id == user_id
        )
    )
    return list(result.scalars().all())


class FeedAnnotations:
    """Per-echo render state for a whole feed page, loaded in a constant number of
    queries regardless of page size (B6.1). Naive per-item loading is the classic
    N+1 on the one fan-out surface in the product — so everything here is batched
    by echo id and stitched in Python."""

    def __init__(self) -> None:
        self.replies: dict[uuid.UUID, list[EchoReply]] = {}
        self.has_more: dict[uuid.UUID, bool] = {}
        self.my_reactions: dict[uuid.UUID, list[str]] = {}
        self.counts: dict[uuid.UUID, dict[str, int]] = {}  # author's own echoes only
        self.reply_counts: dict[uuid.UUID, int] = {}       # author's own echoes only

    def replies_for(self, echo_id: uuid.UUID) -> list[EchoReply]:
        return self.replies.get(echo_id, [])[:REPLY_PREVIEW_LIMIT]


async def annotate_feed(
    db: AsyncSession, echoes: list[Echo], viewer_id: uuid.UUID | None
) -> FeedAnnotations:
    """Batch-load reply previews + the viewer's reaction state for a page of echoes.

    Query budget (constant in page size):
      1. reply previews (top REPLY_PREVIEW_LIMIT+1 per echo, block-filtered)
      2. the viewer's own reaction rows across the page
      3. private reaction_counts — ONLY for echoes the viewer authored (skipped if none)
    """
    ann = FeedAnnotations()
    ids = [e.id for e in echoes]
    if not ids:
        return ann

    # 1) Reply previews. A window function caps rows at PREVIEW_LIMIT+1 per echo so a
    # popular echo can't drag the whole page; the +1 tells us `has_more` without a count.
    # Block/mute filtering must survive the optimization (B6.4).
    hidden = await hidden_author_ids(db, viewer_id)
    rn = func.row_number().over(
        partition_by=EchoReply.echo_id,
        order_by=(EchoReply.created_at.asc(), EchoReply.id.asc()),
    ).label("rn")
    ranked = (
        select(EchoReply.id.label("rid"), rn)
        .where(EchoReply.echo_id.in_(ids), EchoReply.status == "active")
    )
    if hidden:
        ranked = ranked.where(EchoReply.author_id.notin_(hidden))
    ranked = ranked.subquery()

    reply_q = (
        select(EchoReply)
        .options(selectinload(EchoReply.author))
        .join(ranked, ranked.c.rid == EchoReply.id)
        .where(ranked.c.rn <= REPLY_PREVIEW_LIMIT + 1)
        .order_by(EchoReply.echo_id, EchoReply.created_at.asc(), EchoReply.id.asc())
    )
    for reply in (await db.execute(reply_q)).scalars().all():
        bucket = ann.replies.setdefault(reply.echo_id, [])
        if len(bucket) < REPLY_PREVIEW_LIMIT:
            bucket.append(reply)
        else:
            ann.has_more[reply.echo_id] = True  # the PREVIEW_LIMIT+1'th row

    # 2) The viewer's own reactions across the page (drives pressed-state).
    if viewer_id is not None:
        rows = await db.execute(
            select(EchoReaction.echo_id, EchoReaction.kind).where(
                EchoReaction.echo_id.in_(ids), EchoReaction.user_id == viewer_id
            )
        )
        for eid, kind in rows.all():
            ann.my_reactions.setdefault(eid, []).append(kind)

    # 3) Private counts — only for echoes the viewer authored (the witness payoff).
    own = [e.id for e in echoes if viewer_id is not None and e.author_id == viewer_id]
    if own:
        rows = await db.execute(
            select(EchoReaction.echo_id, EchoReaction.kind, func.count(EchoReaction.id))
            .where(EchoReaction.echo_id.in_(own))
            .group_by(EchoReaction.echo_id, EchoReaction.kind)
        )
        for eid, kind, n in rows.all():
            ann.counts.setdefault(eid, {})[kind] = n

        # 4) Reply totals, same author-only rule. Counted here rather than derived
        # from the preview, which caps at REPLY_PREVIEW_LIMIT and would silently
        # under-report any echo with a real conversation on it. Block-filtered to
        # match the preview, so the number never exceeds what the author can read.
        reply_q = (
            select(EchoReply.echo_id, func.count(EchoReply.id))
            .where(EchoReply.echo_id.in_(own), EchoReply.status == "active")
            .group_by(EchoReply.echo_id)
        )
        if hidden:
            reply_q = reply_q.where(EchoReply.author_id.notin_(hidden))
        reply_rows = await db.execute(reply_q)
        for eid, n in reply_rows.all():
            ann.reply_counts[eid] = n
        # Zero-reply echoes get an explicit 0 rather than a missing key: for the
        # author the field means "how many", and absent must not read as none.
        for eid in own:
            ann.reply_counts.setdefault(eid, 0)

    return ann


async def add_book_to_shelf(db: AsyncSession, user_id: uuid.UUID, echo: Echo) -> bool:
    """'To my shelf' made real (B6.3): add the echo's book to the reader's own shelf
    as `want_to_read`. Idempotent — reacting twice never creates a duplicate. Returns
    True only when a new entry was created (so the UI can confirm). Emotion-only echoes
    (no book anchor) add nothing.
    """
    if not echo.book_title:
        return False
    t_norm = normalize(echo.book_title)
    a_norm = normalize(echo.book_author or "")

    existing = (await db.execute(
        select(BookEntry).where(BookEntry.user_id == user_id)
    )).scalars().all()
    for e in existing:
        if normalize(e.title) == t_norm and normalize(e.author or "") == a_norm:
            return False  # already on their shelf — don't touch its status

    # Same find-or-create every other write path uses, so a book added from an
    # echo lands on the same canonical row as one added from search (B8.1).
    book = await resolve_book(db, echo.book_title, echo.book_author)
    db.add(BookEntry(
        user_id=user_id,
        book_id=book.id if book else None,
        title=echo.book_title,
        author=echo.book_author,
        status="want_to_read",
    ))
    await db.flush()
    return True


async def current_prompt(db: AsyncSession) -> Prompt | None:
    """The weekly Prompt whose window contains now (B6.5). None if none is live.
    Most-recently-started wins if windows ever overlap."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Prompt)
        .where(Prompt.starts_at <= now, Prompt.ends_at > now)
        .order_by(Prompt.starts_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
