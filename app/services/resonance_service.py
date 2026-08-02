"""Resonance matching: find the reader who felt what you felt about a book.

The rule is deliberately narrow. Two readers match on a book when they both have
an engaged ``book_entries`` row for the same canonical ``book_id`` *and* their
``entry_emotions`` sets overlap. A shared emotion felt at a similar intensity
(within ``CLOSE_INTENSITY`` points) makes the match ``strong``; a bare overlap
makes it ``light``. Nothing else — no reading volume, no follower graph, no
engagement history — feeds the score.

This is **not** computed on the read path. ``refresh_matches_for_user`` runs as a
background task after an entry is written and as a nightly sweep
(``scripts/refresh_resonance.py``); the API only reads pre-computed rows.

Two rules the read paths enforce and must keep enforcing:
  - Identity (id, handle, name, email) is never emitted before ``connected``.
  - Nothing is counted. There is no "N readers" query here and there must not be.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models.book import Book
from app.models.book_entry import BookEntry, EntryEmotion
from app.models.resonance import (
    STRENGTH_LIGHT,
    STRENGTH_STRONG,
    ResonanceMatch,
    ResonanceMessage,
    ResonanceThread,
)
from app.models.user import User
from app.services.aggregate_service import ENGAGED_STATUSES
from app.services.moderation import VERDICT_CRISIS, VERDICT_HOLD, classify_text
from app.services.social_service import hidden_author_ids, is_blocked_between
from app.utils.emotions import get_emotion

logger = logging.getLogger("bibliome.resonance")

# How many suggestions a reader is shown at once. Three is the whole point: this
# is a quiet invitation, not an inbox. Raising it makes the feature a feed.
SURFACE_LIMIT = 3

# How many rows the batch job is allowed to bank per reader per run. The surplus
# above SURFACE_LIMIT is what refills the set as suggestions get acted on.
STORE_LIMIT = 12

# Intensity distance (1–10 scale) within which a shared emotion counts as "felt
# the same way", not merely "felt".
CLOSE_INTENSITY = 2

# Hard ceiling on candidate rows pulled per reader — a reader with 500 books of
# common titles would otherwise drag the whole tag table into memory.
MAX_CANDIDATE_ROWS = 5000

# A reach costs the recipient attention, so it is rationed per account per day.
REACH_DAILY_LIMIT = 5

MAX_NOTE_CHARS = 500
MAX_MESSAGE_CHARS = 2000


class ResonanceError(ValueError):
    """Bad resonance input or a forbidden state transition (router maps to 400)."""


def ordered_pair(x: uuid.UUID, y: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Canonical (user_a, user_b) ordering — the dedupe key's foundation."""
    return (x, y) if str(x) < str(y) else (y, x)


@dataclass(frozen=True)
class SharedEmotion:
    """One overlapping emotion, with both readers' intensities."""

    emotion_id: str
    mine: int
    theirs: int

    @property
    def close(self) -> bool:
        return abs(self.mine - self.theirs) <= CLOSE_INTENSITY


@dataclass
class Candidate:
    """A prospective match, from the perspective of the reader we queried for."""

    other_user_id: uuid.UUID
    book_id: uuid.UUID
    shared: list[SharedEmotion]
    score: float
    strength: str


def _score(shared: list[SharedEmotion]) -> tuple[float, str]:
    """Rank candidates. A close-intensity overlap is worth much more than a bare
    one; a small average-intensity term breaks ties toward the emotions both
    readers felt hardest, so "we were both wrecked" outranks "we were both mildly
    amused" when the overlap is otherwise identical."""
    if not shared:
        return 0.0, STRENGTH_LIGHT
    base = sum(1.0 if s.close else 0.4 for s in shared)
    avg_intensity = sum(s.mine + s.theirs for s in shared) / (2 * len(shared))
    strength = STRENGTH_STRONG if any(s.close for s in shared) else STRENGTH_LIGHT
    return round(base + 0.01 * avg_intensity, 4), strength


async def _excluded_user_ids(db: AsyncSession, user_id: uuid.UUID) -> set[uuid.UUID]:
    """Readers this user must never be matched with again: anyone blocked or
    muted in either direction, plus anyone they have already connected with or
    declined *on any book*. A "no" is a no about the person, not the title."""
    excluded = await hidden_author_ids(db, user_id)

    r = await db.execute(
        select(ResonanceMatch.user_a, ResonanceMatch.user_b).where(
            or_(ResonanceMatch.user_a == user_id, ResonanceMatch.user_b == user_id),
            ResonanceMatch.status.in_(("connected", "declined")),
        )
    )
    for a, b in r.all():
        excluded.add(b if a == user_id else a)
    return excluded


async def _existing_pair_books(db: AsyncSession, user_id: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """(other_user_id, book_id) combinations that already have a row — the batch
    job must not resurrect a suggestion the reader has already been shown."""
    r = await db.execute(
        select(ResonanceMatch.user_a, ResonanceMatch.user_b, ResonanceMatch.book_id).where(
            or_(ResonanceMatch.user_a == user_id, ResonanceMatch.user_b == user_id)
        )
    )
    return {(b if a == user_id else a, book_id) for a, b, book_id in r.all()}


async def find_candidate_matches(
    db: AsyncSession, user_id: uuid.UUID, limit: int = STORE_LIMIT
) -> list[Candidate]:
    """Candidate matches for one reader, best first.

    Self-join over ``book_entries`` on ``book_id`` and ``entry_emotions`` on
    ``emotion_id``. Only engaged entries (finished / abandoned / paused / reread)
    take part — a want-to-read shelf entry is an intention, not a feeling.
    """
    mine_entry = aliased(BookEntry)
    their_entry = aliased(BookEntry)
    mine_emo = aliased(EntryEmotion)
    their_emo = aliased(EntryEmotion)

    stmt = (
        select(
            their_entry.user_id,
            mine_entry.book_id,
            mine_emo.emotion_id,
            mine_emo.strength,
            their_emo.strength,
        )
        .join(mine_emo, mine_emo.entry_id == mine_entry.id)
        .join(
            their_entry,
            and_(
                their_entry.book_id == mine_entry.book_id,
                their_entry.user_id != mine_entry.user_id,
                their_entry.status.in_(ENGAGED_STATUSES),
            ),
        )
        .join(
            their_emo,
            and_(
                their_emo.entry_id == their_entry.id,
                their_emo.emotion_id == mine_emo.emotion_id,
            ),
        )
        .where(
            mine_entry.user_id == user_id,
            mine_entry.book_id.isnot(None),
            mine_entry.status.in_(ENGAGED_STATUSES),
        )
        # Closest-felt pairings first, so the row cap truncates the weakest
        # evidence rather than an arbitrary slice.
        .order_by(func.abs(mine_emo.strength - their_emo.strength))
        .limit(MAX_CANDIDATE_ROWS)
    )

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    excluded = await _excluded_user_ids(db, user_id)
    already = await _existing_pair_books(db, user_id)

    # (other, book) → emotion_id → SharedEmotion, keeping the closest pairing when
    # a reader has several entries for the same book (rereads).
    grouped: dict[tuple[uuid.UUID, uuid.UUID], dict[str, SharedEmotion]] = {}
    for other_id, book_id, emotion_id, my_strength, their_strength in rows:
        if other_id in excluded or (other_id, book_id) in already:
            continue
        shared = SharedEmotion(emotion_id, my_strength or 5, their_strength or 5)
        bucket = grouped.setdefault((other_id, book_id), {})
        held = bucket.get(emotion_id)
        if held is None or abs(shared.mine - shared.theirs) < abs(held.mine - held.theirs):
            bucket[emotion_id] = shared

    candidates: list[Candidate] = []
    for (other_id, book_id), emotions in grouped.items():
        shared = sorted(emotions.values(), key=lambda s: (not s.close, s.emotion_id))
        score, strength = _score(shared)
        candidates.append(Candidate(other_id, book_id, shared, score, strength))

    # str(book_id) as the tiebreak keeps the ordering stable across runs.
    candidates.sort(key=lambda c: (-c.score, str(c.book_id)))
    return candidates[:limit]


async def refresh_matches_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Persist this reader's candidates as `suggested` rows. Returns rows created.

    Idempotent: the unique (user_a, user_b, book_id) constraint absorbs both a
    re-run and the race between two workers refreshing both sides of a pair.
    """
    candidates = await find_candidate_matches(db, user_id)
    if not candidates:
        return 0

    created = 0
    for cand in candidates:
        a, b = ordered_pair(user_id, cand.other_user_id)
        a_is_me = a == user_id
        payload = [
            {
                "emotion_id": s.emotion_id,
                "strength_a": s.mine if a_is_me else s.theirs,
                "strength_b": s.theirs if a_is_me else s.mine,
                "close": s.close,
            }
            for s in cand.shared
        ]
        stmt = (
            pg_insert(ResonanceMatch)
            .values(
                id=uuid.uuid4(),
                user_a=a,
                user_b=b,
                book_id=cand.book_id,
                shared_emotions=payload,
                strength=cand.strength,
                score=cand.score,
                status="suggested",
            )
            .on_conflict_do_nothing(constraint="uq_resonance_pair_book")
            .returning(ResonanceMatch.id)
        )
        result = await db.execute(stmt)
        if result.scalar_one_or_none() is not None:
            created += 1

    await db.flush()
    return created


async def refresh_all_matches(db: AsyncSession) -> tuple[int, int]:
    """Nightly sweep over every reader with at least one engaged, resolved entry.
    Returns (readers_processed, matches_created)."""
    user_ids = (
        await db.execute(
            select(BookEntry.user_id)
            .where(BookEntry.book_id.isnot(None), BookEntry.status.in_(ENGAGED_STATUSES))
            .group_by(BookEntry.user_id)
        )
    ).scalars().all()

    total = 0
    for uid in user_ids:
        try:
            total += await refresh_matches_for_user(db, uid)
        except Exception:
            logger.exception("Resonance refresh failed for user %s", uid)
    return len(user_ids), total


# ── Reading the surfaced set ──


async def list_matches(db: AsyncSession, user_id: uuid.UUID) -> list[ResonanceMatch]:
    """What this reader should see right now: every live pending/connected match,
    plus at most ``SURFACE_LIMIT`` suggestions.

    Suggestions already shown are preferred over higher-scoring newcomers, so the
    set a reader looked at yesterday is still there today instead of reshuffling
    under them. Declined matches are gone for good.
    """
    mine = or_(ResonanceMatch.user_a == user_id, ResonanceMatch.user_b == user_id)

    live = (
        await db.execute(
            select(ResonanceMatch)
            .options(selectinload(ResonanceMatch.thread))
            .where(mine, ResonanceMatch.status.in_(("pending", "connected")))
            .order_by(ResonanceMatch.updated_at.desc())
        )
    ).scalars().all()

    suggested = (
        await db.execute(
            select(ResonanceMatch)
            .options(selectinload(ResonanceMatch.thread))
            .where(mine, ResonanceMatch.status == "suggested")
            .order_by(
                # Already-surfaced first (False sorts before True), then by score.
                ResonanceMatch.surfaced_at.is_(None),
                ResonanceMatch.score.desc(),
                ResonanceMatch.id,
            )
            .limit(SURFACE_LIMIT)
        )
    ).scalars().all()

    now = datetime.now(timezone.utc)
    for match in suggested:
        if match.surfaced_at is None:
            match.surfaced_at = now
    if suggested:
        await db.flush()

    return list(live) + list(suggested)


async def get_match_for_user(
    db: AsyncSession, match_id: uuid.UUID, user_id: uuid.UUID
) -> ResonanceMatch | None:
    """Load a match only if this reader is one of its two parties. A non-party
    gets None — and therefore a 404, never a 403, which would confirm the row."""
    match = (
        await db.execute(
            select(ResonanceMatch)
            .options(selectinload(ResonanceMatch.thread))
            .where(ResonanceMatch.id == match_id)
        )
    ).scalar_one_or_none()
    if match is None or not match.involves(user_id):
        return None
    return match


async def match_view(db: AsyncSession, match: ResonanceMatch, viewer_id: uuid.UUID) -> dict:
    """The anonymised card. The other reader's identity appears here only once
    ``status == 'connected'`` — before that the response carries a book, some
    emotions, and nothing that could be resolved to a person.

    Their note is likewise sealed until connection, which is what makes a reach
    safe to send: it cannot be read by someone who has not answered it.
    """
    a_side = viewer_id == match.user_a
    shared = [
        {
            "emotion_id": s["emotion_id"],
            "label": (get_emotion(s["emotion_id"]) or {}).get("phrase", s["emotion_id"]),
            "your_strength": s["strength_a"] if a_side else s["strength_b"],
            "their_strength": s["strength_b"] if a_side else s["strength_a"],
            "close": s["close"],
        }
        for s in (match.shared_emotions or [])
    ]

    book = (await db.execute(select(Book).where(Book.id == match.book_id))).scalar_one_or_none()

    if match.status == "pending":
        direction = "you_reached" if match.initiator_id == viewer_id else "they_reached"
    else:
        direction = "none"

    connected = match.status == "connected"
    initiated = match.initiator_id == viewer_id
    your_note = match.initiator_note if initiated else match.responder_note
    their_note = (match.responder_note if initiated else match.initiator_note) if connected else None

    view = {
        "match_id": match.id,
        "book_id": match.book_id,
        "book_title": book.title if book else None,
        "book_author": book.author if book else None,
        "cover_url": getattr(book, "cover_url", None) if book else None,
        "shared_emotions": shared,
        "strength": match.strength,
        "status": match.status,
        "direction": direction,
        "your_note": your_note,
        "their_note": their_note,
        "thread_id": match.thread.id if (connected and match.thread) else None,
        "handle": None,
        "created_at": match.created_at,
    }

    if connected:
        other = (
            await db.execute(select(User.handle).where(User.id == match.other_id(viewer_id)))
        ).scalar_one_or_none()
        view["handle"] = other

    return view


# ── The state machine ──


async def reach(
    db: AsyncSession, match: ResonanceMatch, user_id: uuid.UUID, note: str
) -> tuple[ResonanceMatch, ResonanceThread | None]:
    """Leave the opening note. Moves `suggested` → `pending` and seals the note.

    If the other side has already reached, both parties have now said yes without
    either ever having read the other's note — that is a mutual accept, and it
    connects.
    """
    note = (note or "").strip()
    if not note:
        raise ResonanceError("A note is required to reach out")
    if len(note) > MAX_NOTE_CHARS:
        raise ResonanceError(f"Note must be {MAX_NOTE_CHARS} characters or fewer")

    other_id = match.other_id(user_id)
    if await is_blocked_between(db, user_id, other_id):
        raise ResonanceError("This match is no longer available")

    if match.status == "connected":
        raise ResonanceError("You are already connected")
    if match.status == "declined":
        raise ResonanceError("This match is closed")

    if match.status == "pending":
        if match.initiator_id == user_id:
            raise ResonanceError("You have already reached out")
        # Mutual reach — treat the second one as the accept it is.
        match.responder_note = note
        return await _connect(db, match)

    match.status = "pending"
    match.initiator_id = user_id
    match.initiator_note = note
    match.reached_at = datetime.now(timezone.utc)
    await db.flush()
    return match, None


async def respond(
    db: AsyncSession,
    match: ResonanceMatch,
    user_id: uuid.UUID,
    accept: bool,
    note: str | None = None,
) -> tuple[ResonanceMatch, ResonanceThread | None]:
    """Accept or decline. Accepting the other side's reach connects the pair;
    declining closes the match permanently and is never reported back as a
    rejection — the other side simply stops seeing it.

    A reader may also decline a bare `suggested` match (dismiss it), or decline
    their own pending reach (withdraw it).
    """
    if match.status == "connected":
        raise ResonanceError("You are already connected")
    if match.status == "declined":
        raise ResonanceError("This match is closed")

    if not accept:
        match.status = "declined"
        match.declined_by = user_id
        match.responded_at = datetime.now(timezone.utc)
        await db.flush()
        return match, None

    if match.status != "pending":
        raise ResonanceError("There is nothing to accept yet")
    if match.initiator_id == user_id:
        raise ResonanceError("You have already reached out — wait for a reply")

    if await is_blocked_between(db, user_id, match.other_id(user_id)):
        raise ResonanceError("This match is no longer available")

    if note:
        note = note.strip()[:MAX_NOTE_CHARS]
        match.responder_note = note
    return await _connect(db, match)


async def _connect(
    db: AsyncSession, match: ResonanceMatch
) -> tuple[ResonanceMatch, ResonanceThread]:
    """Both sides said yes: reveal identity, open the thread, seed it with the
    notes so the conversation starts where the two of them left off."""
    already_connected = match.status == "connected"
    match.status = "connected"
    match.responded_at = datetime.now(timezone.utc)

    # uq_thread_match makes one thread per match a schema fact; if a concurrent
    # accept got here first, adopt theirs rather than colliding on the insert.
    existing = (
        await db.execute(select(ResonanceThread).where(ResonanceThread.match_id == match.id))
    ).scalar_one_or_none()
    if existing is not None:
        match.thread = existing
        await db.flush()
        return match, existing
    if already_connected:  # defensive: connected without a thread should not happen
        logger.warning("Match %s was connected with no thread; recreating", match.id)

    thread = ResonanceThread(match_id=match.id)
    db.add(thread)
    await db.flush()

    if match.initiator_id and match.initiator_note:
        db.add(
            ResonanceMessage(
                thread_id=thread.id, sender_id=match.initiator_id, body=match.initiator_note
            )
        )
    if match.responder_note:
        responder = match.other_id(match.initiator_id) if match.initiator_id else match.user_a
        db.add(
            ResonanceMessage(thread_id=thread.id, sender_id=responder, body=match.responder_note)
        )
    await db.flush()
    match.thread = thread
    return match, thread


# ── Threads ──


async def get_thread_for_user(
    db: AsyncSession, thread_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[ResonanceThread, ResonanceMatch] | None:
    """A thread and its match, only for its two parties. Non-parties get None."""
    thread = (
        await db.execute(
            select(ResonanceThread)
            .options(selectinload(ResonanceThread.match))
            .where(ResonanceThread.id == thread_id)
        )
    ).scalar_one_or_none()
    if thread is None or not thread.match.involves(user_id):
        return None
    return thread, thread.match


async def list_threads(db: AsyncSession, user_id: uuid.UUID) -> list[ResonanceThread]:
    """This reader's own open threads. Private by construction — there is no
    endpoint that lists anyone else's."""
    r = await db.execute(
        select(ResonanceThread)
        .join(ResonanceMatch, ResonanceMatch.id == ResonanceThread.match_id)
        .options(selectinload(ResonanceThread.match))
        .where(
            or_(ResonanceMatch.user_a == user_id, ResonanceMatch.user_b == user_id),
            ResonanceThread.status == "open",
        )
        .order_by(ResonanceThread.created_at.desc())
    )
    return list(r.scalars().all())


async def post_message(
    db: AsyncSession, thread: ResonanceThread, sender_id: uuid.UUID, body: str
) -> tuple[ResonanceMessage, str, str | None]:
    """Send a message. Free text: no topic anchor, no emotion tag, no prompt.

    Returns ``(message, verdict, reason)``. The classifier that guards Echo runs
    here too — it previously did not, which meant the one surface where a
    stranger can say anything to one specific person was the only surface with
    no pre-publish check at all.

    The two verdicts are treated differently than on a public surface:

    - **crisis** (the *sender* sounds at risk): the message sends, and the sender
      gets the resources back. Care, not punishment — same stance as Echo.
    - **threat**: refused outright. Echo can hold a threat invisibly because it
      has a feed to hide it from; a thread has one reader, who is the target.
      Holding it silently would tell the sender it sent. Refusing is honest.
    - **pii**: allowed through. Echo holds these because it is public. Two people
      who both said yes swapping contact details is the thread working.
    """
    body = (body or "").strip()
    if not body:
        raise ResonanceError("Message body is required")
    if len(body) > MAX_MESSAGE_CHARS:
        raise ResonanceError(f"Message must be {MAX_MESSAGE_CHARS} characters or fewer")
    if thread.status != "open":
        raise ResonanceError("This conversation is closed")
    if await is_blocked_between(db, sender_id, thread.match.other_id(sender_id)):
        raise ResonanceError("This conversation is closed")

    verdict, reason = classify_text(body)
    if verdict == VERDICT_HOLD and reason == "threat":
        raise ResonanceError("This message can't be sent.")

    message = ResonanceMessage(thread_id=thread.id, sender_id=sender_id, body=body)
    db.add(message)
    await db.flush()
    return message, verdict, reason


async def list_messages(
    db: AsyncSession, thread: ResonanceThread, limit: int = 50, before: datetime | None = None
) -> list[ResonanceMessage]:
    """A page of transcript, oldest-first within the page. `before` pages backward
    through history."""
    stmt = select(ResonanceMessage).where(ResonanceMessage.thread_id == thread.id)
    if before is not None:
        stmt = stmt.where(ResonanceMessage.created_at < before)
    stmt = stmt.order_by(ResonanceMessage.created_at.desc(), ResonanceMessage.id).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def close_thread(
    db: AsyncSession, thread: ResonanceThread, user_id: uuid.UUID, decline_match: bool = True
) -> None:
    """Shut a conversation down. Silent to the other party beyond the thread
    going quiet — they are not told they were blocked."""
    thread.status = "closed"
    thread.closed_by = user_id
    if decline_match:
        thread.match.status = "declined"
        thread.match.declined_by = user_id
    await db.flush()
