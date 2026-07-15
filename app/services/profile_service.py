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
def compute_milestones(entries: list[BookEntry]) -> list[dict]:
    if not entries:
        return []

    tagged: set[str] = set()
    used_finish = False
    dates = []
    for e in entries:
        for em in e.emotions:
            canon = canonicalize(em.emotion_id)
            if canon:
                tagged.add(canon)
        if e.arc_start_emotion_id or e.finish_thought:
            used_finish = True
        if e.created_at:
            dates.append(e.created_at)

    milestones: list[dict] = []
    milestones.append({"kind": "first_book", "label": "Logged your first book"})
    if used_finish:
        milestones.append({"kind": "first_finish", "label": "Completed a full emotional arc"})
    if len(tagged) >= 10:
        milestones.append({"kind": "deep_range", "label": "Felt across 10+ emotional registers"})
    if tagged >= VALID_SLUGS:
        milestones.append({"kind": "full_spectrum", "label": f"Read across all {len(VALID_SLUGS)} emotional registers"})
    if dates and (max(dates) - min(dates)).days >= 365:
        milestones.append({"kind": "year_of_reflection", "label": "A year of consistent reflection"})
    return milestones


def _viewer_class(viewer_id: uuid.UUID | None, owner_id: uuid.UUID) -> str:
    if viewer_id is None:
        return VIEWER_ANON
    if viewer_id == owner_id:
        return VIEWER_OWNER
    return VIEWER_MEMBER


async def _load_entries(db: AsyncSession, user_id: uuid.UUID) -> list[BookEntry]:
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.desc())
    )
    return list(result.scalars().all())


def _entry_card(e: BookEntry) -> dict:
    """A book card for a profile — never exposes private notes/echo, only the
    shelf-safe fields (blueprint: a private review stays hidden even in a public
    collection)."""
    dominant = None
    if e.emotions:
        top = max(e.emotions, key=lambda x: x.strength)
        dominant = canonicalize(top.emotion_id)
    return {
        "entry_id": str(e.id),
        "title": e.title,
        "author": e.author,
        "cover_url": e.cover_url,
        "dominant_emotion": dominant,
        "status": e.status,
    }


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


def _identity_strip(user: User) -> dict:
    """Only pseudonymous, shelf-safe identity — never email or real data."""
    return {
        "handle": user.handle,
        "display_name": user.display_name,
        "bio": user.bio,
        "profile_visibility": user.profile_visibility,
        "personality_type": user.personality_type,
    }


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
    now_reading = [_entry_card(e) for e in entries if e.status == "reading"]

    profile = {
        "restricted": False,
        **_identity_strip(owner),
        "is_self": viewer_class == VIEWER_OWNER,
        "signature": owner.cached_dna_profile,  # reuse cache; never recompute here (P2-3)
        "now_reading": now_reading,
        "collections": await _visible_collections(db, owner.id, viewer_class, entries_by_id),
        "milestones": compute_milestones(entries),
        "book_count": len(entries),
        "recent": [_entry_card(e) for e in entries[:12]],
    }
    return profile
