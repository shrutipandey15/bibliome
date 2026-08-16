"""Collections: CRUD (Feature 2) + sharing (#5).

The original CRUD is owner-scoped. The sharing half below is membership-scoped —
see the permission model documented above `get_membership`.
"""

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book import Book
from app.models.book_entry import BookEntry
from app.models.user import User
from app.models.collection import (
    Collection, CollectionInvite, CollectionItem, CollectionMember,
)
from app.services.auth_service import hash_token


class CollectionError(ValueError):
    """Bad collection input (router maps to 400)."""


async def create_collection(
    db: AsyncSession, user_id: uuid.UUID, title: str, description: str | None, visibility: str
) -> Collection:
    if visibility not in ("private", "community", "public"):
        raise CollectionError("Invalid visibility")
    max_pos = (await db.execute(
        select(func.max(Collection.position)).where(Collection.user_id == user_id)
    )).scalar()
    c = Collection(
        user_id=user_id,
        title=title.strip(),
        description=(description or None),
        visibility=visibility,
        position=(max_pos or 0) + 1,
    )
    db.add(c)
    await db.flush()
    # The owner is a member from the first moment, so membership is the only
    # thing any read path has to check (#5).
    db.add(CollectionMember(collection_id=c.id, user_id=user_id, role="owner"))
    await db.flush()
    return c


async def get_owned_collection(db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID) -> Collection | None:
    result = await db.execute(
        select(Collection)
        .options(selectinload(Collection.items))
        .where(Collection.id == collection_id, Collection.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def update_collection(db: AsyncSession, collection: Collection, **fields) -> Collection:
    if "visibility" in fields and fields["visibility"] not in ("private", "community", "public"):
        raise CollectionError("Invalid visibility")
    for key, value in fields.items():
        if value is not None and hasattr(collection, key):
            setattr(collection, key, value)
    await db.flush()
    return collection


async def delete_collection(db: AsyncSession, collection: Collection) -> None:
    await db.delete(collection)
    await db.flush()


async def add_item(db: AsyncSession, collection: Collection, entry_id: uuid.UUID, user_id: uuid.UUID) -> None:
    # The entry must belong to the collection's owner.
    owned = (await db.execute(
        select(BookEntry.id).where(BookEntry.id == entry_id, BookEntry.user_id == user_id)
    )).scalar_one_or_none()
    if owned is None:
        raise CollectionError("Book not found in your library")
    max_pos = (await db.execute(
        select(func.max(CollectionItem.position)).where(CollectionItem.collection_id == collection.id)
    )).scalar()
    stmt = pg_insert(CollectionItem).values(
        collection_id=collection.id, entry_id=entry_id, position=(max_pos or 0) + 1
    ).on_conflict_do_nothing(constraint="uq_collection_item")
    await db.execute(stmt)
    await db.flush()


async def remove_item(db: AsyncSession, collection: Collection, entry_id: uuid.UUID) -> None:
    item = (await db.execute(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection.id, CollectionItem.entry_id == entry_id
        )
    )).scalar_one_or_none()
    if item is not None:
        await db.delete(item)
        await db.flush()


async def reorder_items(db: AsyncSession, collection: Collection, entry_ids: list[uuid.UUID]) -> None:
    items = (await db.execute(
        select(CollectionItem).where(CollectionItem.collection_id == collection.id)
    )).scalars().all()
    by_entry = {it.entry_id: it for it in items}
    for pos, eid in enumerate(entry_ids):
        it = by_entry.get(eid)
        if it is not None:
            it.position = pos
    await db.flush()


# ── Sharing (#5): membership, invites, and book-identity items ──
#
# The permission model in one place, because every route below reads it:
#   owner  — rename, delete, invite, revoke, remove ANY item, remove members
#   member — view, add items, remove their OWN items, leave
# Two roles only. A third tier is a permissions UI nobody asked for.

INVITE_TOKEN_BYTES = 24


class CollectionForbidden(PermissionError):
    """Caller is not allowed to do this here (router maps to 403)."""


async def get_membership(
    db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID
) -> CollectionMember | None:
    return (await db.execute(
        select(CollectionMember).where(
            CollectionMember.collection_id == collection_id,
            CollectionMember.user_id == user_id,
        )
    )).scalar_one_or_none()


async def get_visible_collection(
    db: AsyncSession, collection_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Collection, CollectionMember] | tuple[None, None]:
    """The collection plus the caller's membership, or (None, None).

    Membership is the single gate. `get_owned_collection` still exists for the
    owner-only routes, but everything a member can reach goes through here so
    that "can this person see it?" is one query with one answer.
    """
    member = await get_membership(db, collection_id, user_id)
    if member is None:
        return None, None
    collection = (await db.execute(
        select(Collection)
        .options(selectinload(Collection.items))
        .where(Collection.id == collection_id)
    )).scalar_one_or_none()
    if collection is None:
        return None, None
    return collection, member


async def ensure_owner_membership(db: AsyncSession, collection: Collection) -> None:
    """Backstop for collections created before the membership table existed.

    The migration backfills every existing collection, so this only ever fires
    for a row created by a code path that forgot — cheap insurance against an
    owner locked out of their own collection.
    """
    stmt = pg_insert(CollectionMember).values(
        collection_id=collection.id, user_id=collection.user_id, role="owner",
    ).on_conflict_do_nothing(constraint="uq_collection_member")
    await db.execute(stmt)


async def list_members(db: AsyncSession, collection_id: uuid.UUID) -> list[CollectionMember]:
    return list((await db.execute(
        select(CollectionMember)
        .options(selectinload(CollectionMember.user))
        .where(CollectionMember.collection_id == collection_id)
        .order_by(CollectionMember.joined_at.asc())
    )).scalars().all())


async def create_invite(
    db: AsyncSession,
    collection: Collection,
    created_by: uuid.UUID,
    *,
    expires_at: datetime | None = None,
    max_uses: int | None = None,
) -> tuple[CollectionInvite, str]:
    """Mint an invite link.

    Returns (invite, RAW token). The raw token is returned rather than stored —
    only its SHA-256 is persisted, so it can never be read back out of the DB.
    """
    if max_uses is not None and max_uses < 1:
        raise CollectionError("max_uses must be at least 1")
    raw = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    invite = CollectionInvite(
        collection_id=collection.id,
        token_hash=hash_token(raw),
        created_by=created_by,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    db.add(invite)
    await db.flush()
    return invite, raw


async def _live_invite(db: AsyncSession, raw_token: str) -> CollectionInvite | None:
    invite = (await db.execute(
        select(CollectionInvite).where(CollectionInvite.token_hash == hash_token(raw_token))
    )).scalar_one_or_none()
    if invite is None or invite.revoked:
        return None
    if invite.expires_at and invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    if invite.max_uses is not None and invite.uses >= invite.max_uses:
        return None
    return invite


async def peek_invite(db: AsyncSession, raw_token: str) -> Collection | None:
    """What a link points at, without joining — so the join screen can name the
    collection before asking someone to commit to it.

    Items are eager-loaded: the caller counts them, and a lazy load on an async
    session raises rather than querying.
    """
    invite = await _live_invite(db, raw_token)
    if invite is None:
        return None
    return (await db.execute(
        select(Collection)
        .options(selectinload(Collection.items))
        .where(Collection.id == invite.collection_id)
    )).scalar_one_or_none()


async def redeem_invite(
    db: AsyncSession, raw_token: str, user_id: uuid.UUID
) -> tuple[Collection | None, bool]:
    """Join a collection by link. Returns (collection, joined).

    ``joined`` is False when the caller was already a member — clicking a link
    twice is not an error, and it must not burn a use or reset their role. An
    invalid, revoked, expired or exhausted link returns (None, False).
    """
    # Resolve the invite REGARDLESS of liveness first, so an existing member
    # re-clicking gets "you're already in" rather than "this link is dead". A
    # one-use link is spent the moment its recipient joins, and they are exactly
    # the person most likely to click it again.
    invite = (await db.execute(
        select(CollectionInvite).where(CollectionInvite.token_hash == hash_token(raw_token))
    )).scalar_one_or_none()
    if invite is None:
        return None, False

    collection = (await db.execute(
        select(Collection).where(Collection.id == invite.collection_id)
    )).scalar_one_or_none()
    if collection is None:
        return None, False

    if await get_membership(db, collection.id, user_id) is not None:
        return collection, False

    # Only now does the link's state matter — this caller is actually joining.
    if await _live_invite(db, raw_token) is None:
        return None, False

    db.add(CollectionMember(collection_id=collection.id, user_id=user_id, role="member"))
    # Only a redemption that created a membership counts against max_uses.
    invite.uses += 1
    await db.flush()
    return collection, True


async def revoke_invite(db: AsyncSession, collection: Collection, invite_id: uuid.UUID) -> bool:
    invite = (await db.execute(
        select(CollectionInvite).where(
            CollectionInvite.id == invite_id,
            CollectionInvite.collection_id == collection.id,
        )
    )).scalar_one_or_none()
    if invite is None:
        return False
    invite.revoked = True
    await db.flush()
    return True


async def remove_member(
    db: AsyncSession, collection: Collection, user_id: uuid.UUID
) -> None:
    """Remove a member. The owner cannot be removed — including by themselves.

    Their items stay. `added_by` is SET NULL on user delete and left intact here:
    a departure must not delete books the rest of the collection is reading.
    """
    if user_id == collection.user_id:
        raise CollectionForbidden("The owner cannot leave their own collection")
    member = await get_membership(db, collection.id, user_id)
    if member is not None:
        await db.delete(member)
        await db.flush()


async def add_book(
    db: AsyncSession, collection: Collection, book_id: uuid.UUID, added_by: uuid.UUID
) -> None:
    """Add a canonical book to a shared collection.

    Takes a ``book_id``, not an entry: the item belongs to the collection, not to
    one member's library. Idempotent — two members adding the same book get one
    item, credited to whoever got there first.
    """
    exists = (await db.execute(select(Book.id).where(Book.id == book_id))).scalar_one_or_none()
    if exists is None:
        raise CollectionError("Book not found")
    max_pos = (await db.execute(
        select(func.max(CollectionItem.position)).where(CollectionItem.collection_id == collection.id)
    )).scalar()
    stmt = pg_insert(CollectionItem).values(
        collection_id=collection.id,
        book_id=book_id,
        added_by=added_by,
        position=(max_pos or 0) + 1,
    ).on_conflict_do_nothing(constraint="uq_collection_book")
    await db.execute(stmt)
    await db.flush()


async def remove_book(
    db: AsyncSession,
    collection: Collection,
    book_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    """Remove an item. Members may remove only what they added; the owner, any."""
    item = (await db.execute(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection.id,
            CollectionItem.book_id == book_id,
        )
    )).scalar_one_or_none()
    if item is None:
        return
    if actor_id != collection.user_id and item.added_by != actor_id:
        raise CollectionForbidden("You can only remove books you added")
    await db.delete(item)
    await db.flush()


async def list_joined_collections(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Collections this reader is a MEMBER of but does not own.

    Without this a member has no surface at all: the profile lists collections
    where `collections.user_id` is you, so someone who accepted an invite landed
    back on their own study and saw nothing. They had joined a room with no door.

    Owned collections are excluded on purpose — those already have a home on the
    profile, and listing them twice would read as two different things.
    """
    rows = (await db.execute(
        select(Collection, CollectionMember.joined_at, User.handle)
        .join(CollectionMember, CollectionMember.collection_id == Collection.id)
        .join(User, User.id == Collection.user_id)
        .where(
            CollectionMember.user_id == user_id,
            Collection.user_id != user_id,
        )
        .order_by(CollectionMember.joined_at.desc())
    )).all()
    if not rows:
        return []

    ids = [c.id for c, _, _ in rows]
    counts = dict((await db.execute(
        select(CollectionItem.collection_id, func.count(CollectionItem.id))
        .where(CollectionItem.collection_id.in_(ids))
        .group_by(CollectionItem.collection_id)
    )).all())
    members = dict((await db.execute(
        select(CollectionMember.collection_id, func.count(CollectionMember.id))
        .where(CollectionMember.collection_id.in_(ids))
        .group_by(CollectionMember.collection_id)
    )).all())

    return [
        {
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "owner_handle": handle,
            "joined_at": joined_at,
            "book_count": counts.get(c.id, 0),
            "member_count": members.get(c.id, 0),
        }
        for c, joined_at, handle in rows
    ]
