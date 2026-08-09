"""Profile aggregation (Feature 2).

Composes a profile from entries / DNA / collections, enforcing visibility + blocks
server-side on every field. Milestones are substance-based only — range and
consistency, never volume ("100 books") or streaks (blueprint's explicit rule).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.collection import Collection
from app.models.user import User
from app.services.social_service import hidden_author_ids, is_blocked_between
from app.services.visibility import VIEWER_ANON, VIEWER_MEMBER, VIEWER_OWNER, can_view_profile
from app.utils.emotions import VALID_SLUGS, canonicalize


# Substance-based milestones only. Each predicate reads real data; nothing invented.
#
# Each earned milestone carries the date it was actually earned — found by walking
# the shelf oldest-first and stamping the entry that tipped the predicate over, not
# by dating the whole list "today". Unearned ones are returned too, with
# `achieved: False` and no date, so the study can show what is still ahead without
# the frontend having to guess which of the five are missing. [F2.8]
_MILESTONE_LABELS = {
    "first_book": "Logged your first book",
    "first_finish": "Completed a full emotional arc",
    "deep_range": "Felt across 10+ emotional registers",
    "full_spectrum": f"Read across all {len(VALID_SLUGS)} emotional registers",
    "year_of_reflection": "A year of consistent reflection",
}
_MILESTONE_ORDER = list(_MILESTONE_LABELS)


def compute_milestones(entries: list[BookEntry]) -> list[dict]:
    if not entries:
        return []

    # Oldest first: a milestone is dated by the entry that earned it.
    chronological = sorted(entries, key=lambda e: e.created_at or datetime.max.replace(tzinfo=timezone.utc))
    earned: dict[str, datetime | None] = {}

    def earn(kind: str, when: datetime | None) -> None:
        earned.setdefault(kind, when)

    tagged: set[str] = set()
    first_date = chronological[0].created_at if chronological else None
    for e in chronological:
        when = e.created_at
        earn("first_book", when)
        for em in e.emotions:
            canon = canonicalize(em.emotion_id)
            if canon:
                tagged.add(canon)
        if e.arc_start_emotion_id or e.finish_thought:
            earn("first_finish", when)
        if len(tagged) >= 10:
            earn("deep_range", when)
        if tagged >= VALID_SLUGS:
            earn("full_spectrum", when)
        if first_date and when and (when - first_date).days >= 365:
            earn("year_of_reflection", when)

    return [
        {
            "kind": kind,
            "label": _MILESTONE_LABELS[kind],
            "achieved": kind in earned,
            "achieved_at": earned[kind].isoformat() if earned.get(kind) else None,
        }
        for kind in _MILESTONE_ORDER
    ]


def _viewer_class(viewer_id: uuid.UUID | None, owner_id: uuid.UUID) -> str:
    if viewer_id is None:
        return VIEWER_ANON
    if viewer_id == owner_id:
        return VIEWER_OWNER
    return VIEWER_MEMBER


async def _load_entries(db: AsyncSession, user_id: uuid.UUID) -> list[BookEntry]:
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions), selectinload(BookEntry.checkins))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.desc())
    )
    return list(result.scalars().all())


def _latest_checkin(e: BookEntry) -> dict | None:
    """The most recent weather report on a book in progress. The note is the
    reader's own 80 characters, so it rides along ONLY on the owner's now-reading
    rail — never on a collection or a stranger's view."""
    if not e.checkins:
        return None
    last = max(e.checkins, key=lambda c: c.created_at)
    return {
        "emotion": canonicalize(last.emotion_id),
        "note": last.note,
        "at": last.created_at.isoformat() if last.created_at else None,
    }


def _entry_card(e: BookEntry, *, with_checkin: bool = False) -> dict:
    """A book card for a profile — never exposes private notes/echo, only the
    shelf-safe fields (blueprint: a private review stays hidden even in a public
    collection)."""
    dominant = None
    if e.emotions:
        top = max(e.emotions, key=lambda x: x.strength)
        dominant = canonicalize(top.emotion_id)
    card = {
        "entry_id": str(e.id),
        "title": e.title,
        "author": e.author,
        "cover_url": e.cover_url,
        "dominant_emotion": dominant,
        "status": e.status,
        # Null unless the reader said — the study draws no bar for a book that
        # hasn't been placed.
        "progress": e.progress,
    }
    if with_checkin:
        card["last_checkin"] = _latest_checkin(e)
    return card


async def _visible_collections(
    db: AsyncSession, owner_id: uuid.UUID, viewer_class: str, entries_by_id: dict
) -> list[dict]:
    result = await db.execute(
        select(Collection)
        .options(selectinload(Collection.items))
        .where(Collection.user_id == owner_id)
        .order_by(Collection.position.asc())
    )
    collections = result.scalars().all()
    out = []
    for c in collections:
        # A collection is shown only if the viewer may see it at its own visibility.
        if viewer_class != VIEWER_OWNER:
            if c.visibility == "private":
                continue
            if c.visibility == "community" and viewer_class == VIEWER_ANON:
                continue
        items = sorted(c.items, key=lambda i: i.position)
        cards = [_entry_card(entries_by_id[i.entry_id]) for i in items if i.entry_id in entries_by_id]
        out.append({
            "id": str(c.id),
            "title": c.title,
            "description": c.description,
            "visibility": c.visibility,
            "position": c.position,
            "books": cards,
        })
    return out


def _book_key(e: BookEntry) -> tuple[str, str]:
    return ((e.title or "").strip().lower(), (e.author or "").strip().lower())


# How many saved lines the study carries. The page shows two and offers the rest
# behind "more"; past this the response stops being a profile and starts being an
# export.
MARGINS_LIMIT = 24


def _margins(entries: list[BookEntry]) -> list[dict]:
    """The lines a reader kept — `entry.quote`, one per book.

    A book can appear on the shelf more than once (a reread is a separate record,
    deliberately), so the same title can carry two quotes. The FIRST one wins: the
    line that struck you the first time through. Ordered newest-kept first.

    OWNER ONLY. The quote is written in the same breath as the private notes, and
    `_entry_card` strips those on purpose — this must never be composed into a
    view someone else can read.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[tuple[datetime, dict]] = []
    # Oldest first, so the earliest record of a book is the one we keep.
    for e in sorted(entries, key=lambda x: x.created_at or datetime.max.replace(tzinfo=timezone.utc)):
        if not (e.quote or "").strip():
            continue
        key = _book_key(e)
        if key in seen:
            continue
        seen.add(key)
        at = e.finished_at or (e.created_at.date() if e.created_at else None)
        dominant = None
        if e.emotions:
            dominant = canonicalize(max(e.emotions, key=lambda x: x.strength).emotion_id)
        kept.append((e.created_at, {
            "entry_id": str(e.id),
            "title": e.title,
            "author": e.author,
            "quote": e.quote.strip(),
            "at": at.isoformat() if at else None,
            "dominant_emotion": dominant,
        }))
    kept.sort(key=lambda pair: pair[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return [card for _, card in kept[:MARGINS_LIMIT]]


def _figures(entries: list[BookEntry]) -> dict:
    """The four numbers across the top of the study. Every one is counted from
    the shelf in front of us — there is no figure here the reader could not
    recount by hand."""
    registers: set[str] = set()
    intensities: list[int] = []
    set_down = 0
    for e in entries:
        for em in e.emotions:
            canon = canonicalize(em.emotion_id)
            if canon:
                registers.add(canon)
        if e.intensity is not None:
            intensities.append(e.intensity)
        if e.status == "abandoned":
            set_down += 1
    return {
        "registers_felt": len(registers),
        "avg_intensity": round(sum(intensities) / len(intensities), 1) if intensities else None,
        "set_down": set_down,
    }


def _emotion_counts(entries: list[BookEntry]) -> dict[str, int]:
    """Books per emotional register, counted once per book.

    This is the fingerprint the signature card draws. It is a real tally over the
    reader's own shelf — every register in the vocabulary is answerable from it,
    including the ones that come back zero, which is the half of the picture that
    actually says something.
    """
    counts: dict[str, int] = {}
    for e in entries:
        for slug in {canonicalize(em.emotion_id) for em in e.emotions}:
            if slug:
                counts[slug] = counts.get(slug, 0) + 1
    return counts


# Below this many readers with a settled archetype, a share is noise dressed as a
# statistic — "one of eight" out of nine readers means nothing. The card omits the
# line entirely rather than printing a number that will swing wildly next week.
ARCHETYPE_SHARE_FLOOR = 50


async def archetype_share(db: AsyncSession, personality_type: str | None) -> int | None:
    """What percent of readers share this archetype, or None if it can't be said
    honestly yet. Whole percent — the extra decimal would imply a precision this
    does not have."""
    if not personality_type:
        return None
    total = await db.scalar(select(func.count()).select_from(User).where(User.personality_type.isnot(None)))
    if not total or total < ARCHETYPE_SHARE_FLOOR:
        return None
    mine = await db.scalar(
        select(func.count()).select_from(User).where(User.personality_type == personality_type)
    )
    if not mine:
        return None
    return max(1, round(100 * mine / total))


def _identity_strip(user: User) -> dict:
    """Only pseudonymous, shelf-safe identity — never email or real data."""
    return {
        "handle": user.handle,
        "display_name": user.display_name,
        "bio": user.bio,
        "profile_visibility": user.profile_visibility,
        "personality_type": user.personality_type,
        # When the account was opened. Pseudonymous — a join date, not an identity.
        "member_since": user.created_at.isoformat() if user.created_at else None,
    }


def _shelf_since(user: User, entries: list[BookEntry]) -> str | None:
    """When this shelf actually starts.

    NOT the join date. A reader who imported a decade of Goodreads history on
    their first afternoon has a shelf reaching back to 2014, and stamping it
    "keeping this shelf since 2026" contradicts the very books printed under it.
    So: the earliest date the shelf can evidence — a finish date, a start date,
    or failing both the day the entry was logged — and the account's own birthday
    only when there is nothing on the shelf at all.
    """
    candidates = []
    for e in entries:
        for value in (e.finished_at, e.started_at):
            if value:
                candidates.append(value)
        if e.created_at:
            candidates.append(e.created_at.date())
    if user.created_at:
        candidates.append(user.created_at.date())
    return min(candidates).isoformat() if candidates else None


async def compose_profile(db: AsyncSession, viewer_id: uuid.UUID | None, owner: User) -> dict | None:
    """Compose a profile for a viewer. Returns:
    - None if the viewer must not know it exists (blocked, or private-to-stranger
      when caller wants a hard 404).
    Callers should treat a returned dict with `restricted=True` as the minimal card.
    """
    viewer_class = _viewer_class(viewer_id, owner.id)

    # Blocked either way → appears not to exist.
    if viewer_id is not None and viewer_class != VIEWER_OWNER:
        if await is_blocked_between(db, viewer_id, owner.id):
            return None

    # Private profile viewed by a stranger → minimal card, no data leak.
    if not can_view_profile(owner, viewer_class):
        return {"restricted": True, **_identity_strip(owner)}

    entries = await _load_entries(db, owner.id)
    entries_by_id = {e.id: e for e in entries}
    is_self = viewer_class == VIEWER_OWNER
    # The check-in note is the reader's private shorthand, so only their own
    # now-reading rail carries it.
    now_reading = [_entry_card(e, with_checkin=is_self) for e in entries if e.status == "reading"]

    profile = {
        "restricted": False,
        **_identity_strip(owner),
        "is_self": is_self,
        "shelf_since": _shelf_since(owner, entries),
        "signature": owner.cached_dna_profile,  # reuse cache; never recompute here (P2-3)
        "now_reading": now_reading,
        "collections": await _visible_collections(db, owner.id, viewer_class, entries_by_id),
        "milestones": compute_milestones(entries),
        "book_count": len(entries),
        **_figures(entries),
        # The signature card's fingerprint, drawn from this reader's own shelf.
        "emotion_counts": _emotion_counts(entries),
        "archetype_share": await archetype_share(db, owner.personality_type),
        "recent": [_entry_card(e) for e in entries[:12]],
        # Kept lines are written alongside the private notes — owner only. [F2.8]
        "margins": _margins(entries) if is_self else [],
    }
    return profile
