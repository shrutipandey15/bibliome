"""Block / mute enforcement (B3.6).

Blocks are bidirectional and cross-surface: if either party blocks the other,
neither sees the other's content anywhere. Mutes are one-way (the muter stops
seeing the muted). Both are silent to the other party.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resonance import ResonanceMatch, ResonanceThread
from app.models.social import Block, Mute


async def block_user(db: AsyncSession, blocker_id: uuid.UUID, blocked_id: uuid.UUID) -> None:
    if blocker_id == blocked_id:
        return
    stmt = pg_insert(Block).values(blocker_id=blocker_id, blocked_id=blocked_id).on_conflict_do_nothing(
        constraint="uq_block"
    )
    await db.execute(stmt)
    await db.flush()
    await _purge_resonance(db, blocker_id, blocked_id)


async def _purge_resonance(db: AsyncSession, blocker_id: uuid.UUID, blocked_id: uuid.UUID) -> None:
    """A block has to reach backwards, not just forwards.

    Excluding the pair from *future* matching is not enough: a suggestion banked
    by the batch job an hour ago is still sitting in the blocker's list, and a
    connected thread is still open. Both get closed here, at the moment of the
    block, so "I don't want to see this person" means it everywhere at once.

    Kept in this module rather than resonance_service because block_user is the
    single chokepoint every block passes through, and resonance_service already
    imports from here.
    """
    a, b = (blocker_id, blocked_id) if str(blocker_id) < str(blocked_id) else (blocked_id, blocker_id)
    pair = (ResonanceMatch.user_a == a) & (ResonanceMatch.user_b == b)

    matches = (await db.execute(select(ResonanceMatch).where(pair))).scalars().all()
    if not matches:
        return

    for match in matches:
        match.status = "declined"
        match.declined_by = blocker_id
    await db.execute(
        update(ResonanceThread)
        .where(ResonanceThread.match_id.in_([m.id for m in matches]))
        .values(status="closed", closed_by=blocker_id)
    )
    await db.flush()


async def unblock_user(db: AsyncSession, blocker_id: uuid.UUID, blocked_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Block).where(Block.blocker_id == blocker_id, Block.blocked_id == blocked_id)
    )
    block = result.scalar_one_or_none()
    if block:
        await db.delete(block)
        await db.flush()


async def mute_user(db: AsyncSession, muter_id: uuid.UUID, muted_id: uuid.UUID) -> None:
    if muter_id == muted_id:
        return
    stmt = pg_insert(Mute).values(muter_id=muter_id, muted_id=muted_id).on_conflict_do_nothing(
        constraint="uq_mute"
    )
    await db.execute(stmt)
    await db.flush()


async def unmute_user(db: AsyncSession, muter_id: uuid.UUID, muted_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Mute).where(Mute.muter_id == muter_id, Mute.muted_id == muted_id)
    )
    mute = result.scalar_one_or_none()
    if mute:
        await db.delete(mute)
        await db.flush()


async def hidden_author_ids(db: AsyncSession, viewer_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Author ids whose content must be hidden from `viewer_id`:
    anyone the viewer blocked, anyone who blocked the viewer (bidirectional),
    and anyone the viewer muted. Anonymous viewers hide nothing.
    """
    if viewer_id is None:
        return set()
    hidden: set[uuid.UUID] = set()

    r = await db.execute(select(Block.blocked_id).where(Block.blocker_id == viewer_id))
    hidden.update(r.scalars().all())
    r = await db.execute(select(Block.blocker_id).where(Block.blocked_id == viewer_id))
    hidden.update(r.scalars().all())
    r = await db.execute(select(Mute.muted_id).where(Mute.muter_id == viewer_id))
    hidden.update(r.scalars().all())

    return hidden


async def is_blocked_between(db: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID) -> bool:
    """True if either user has blocked the other (used to forbid interaction)."""
    r = await db.execute(
        select(Block.id).where(
            ((Block.blocker_id == a_id) & (Block.blocked_id == b_id))
            | ((Block.blocker_id == b_id) & (Block.blocked_id == a_id))
        ).limit(1)
    )
    return r.scalar_one_or_none() is not None
