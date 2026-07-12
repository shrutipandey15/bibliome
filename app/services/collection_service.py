"""Collection CRUD (Feature 2). All operations are owner-scoped."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.collection import Collection, CollectionItem


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
