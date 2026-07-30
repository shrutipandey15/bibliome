"""Journal persistence — CRUD over blobs the server cannot read.

Every function here treats ``ciphertext`` as a value to move, never to inspect.
There is no filter, sort, or comparison on it anywhere in this module, and there
is no ``q`` parameter in ``list_entries``: server-side search over ciphertext is
not a missing feature, it is arithmetically impossible
(``journalCryptoContract.md`` §4).

Nothing in this module logs ciphertext.
"""

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.journal import JournalEmotion, JournalEntry, JournalKeyBundle
from app.schemas.entry import EmotionIn
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryUpdate,
    JournalKeyBundleIn,
)
from app.utils.emotions import canonicalize


class InvalidJournalCursor(ValueError):
    """Malformed pagination cursor. Router maps this to 400."""


class KeyBundleExists(Exception):
    """A bundle is already stored. Overwriting one destroys the journal, so the
    create path refuses rather than silently replacing it."""


class KeyBundleMissing(Exception):
    """No bundle stored — the journal was never set up, so there is no key to
    re-wrap and no key any entry could have been sealed under."""


# ── Key bundle ──

async def get_key_bundle(
    db: AsyncSession, user_id: uuid.UUID
) -> JournalKeyBundle | None:
    return (await db.execute(
        select(JournalKeyBundle).where(JournalKeyBundle.user_id == user_id)
    )).scalar_one_or_none()


async def create_key_bundle(
    db: AsyncSession, user_id: uuid.UUID, data: JournalKeyBundleIn
) -> JournalKeyBundle:
    """Store the wrapped key material for the first time. Idempotency here would
    be a data-loss bug, so a second create is a conflict."""
    if await get_key_bundle(db, user_id) is not None:
        raise KeyBundleExists()

    bundle = JournalKeyBundle(user_id=user_id, **data.model_dump())
    db.add(bundle)
    await db.flush()
    return bundle


async def replace_key_bundle(
    db: AsyncSession, user_id: uuid.UUID, data: JournalKeyBundleIn
) -> JournalKeyBundle:
    """Replace the bundle with a re-wrap of the *same* data key.

    The server cannot verify that claim — it has neither key. What it can do is
    require that a bundle already existed and clear the stale flag, so the honest
    state ("your password no longer opens this") is only ever cleared by a client
    that actually did the work.
    """
    bundle = await get_key_bundle(db, user_id)
    if bundle is None:
        raise KeyBundleMissing()

    for field, value in data.model_dump().items():
        setattr(bundle, field, value)
    bundle.password_wrap_stale = False
    await db.flush()
    # server_onupdate expires updated_at; refresh so serializing it doesn't try to
    # lazy-load outside the async context.
    await db.refresh(bundle)
    return bundle


async def mark_password_wrap_stale(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Record that the account password changed without a re-wrap.

    Called on password reset, and on a password change where the client didn't
    send a new bundle. Returns True if there was a journal to invalidate — the
    caller uses that to decide whether to tell the user their journal is now
    recovery-code-only. We do NOT delete the bundle: the recovery wrapping is
    independent of the password and is the user's last way in.
    """
    bundle = await get_key_bundle(db, user_id)
    if bundle is None:
        return False
    bundle.password_wrap_stale = True
    await db.flush()
    return True


# ── Entries ──

def _encode_cursor(entry: JournalEntry) -> str:
    """Opaque keyset cursor: entry_date + id, so several entries on one day page
    without skips or repeats."""
    return f"{entry.entry_date.isoformat()}|{entry.id}"


def _decode_cursor(cursor: str) -> tuple[date, uuid.UUID]:
    try:
        date_raw, id_raw = cursor.rsplit("|", 1)
        return date.fromisoformat(date_raw), uuid.UUID(id_raw)
    except (ValueError, AttributeError):
        raise InvalidJournalCursor(f"Malformed journal cursor: {cursor!r}")


async def _set_tags(
    db: AsyncSession, entry: JournalEntry, emotions: list[EmotionIn]
) -> None:
    """Replace an entry's tags. Canonicalizes on the way in so a legacy slug from
    an old client lands on the same emotion the book path would give it."""
    for existing in list(entry.emotions):
        await db.delete(existing)
    await db.flush()

    seen: set[str] = set()
    for emo in emotions:
        slug = canonicalize(emo.emotion_id) or emo.emotion_id
        if slug in seen:      # the unique constraint would reject the duplicate
            continue
        seen.add(slug)
        db.add(JournalEmotion(
            journal_entry_id=entry.id, emotion_id=slug, strength=emo.strength,
        ))
    await db.flush()


async def get_entry(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID
) -> JournalEntry | None:
    """Fetch one entry with its tags. Ownership is in the WHERE clause, not a
    post-hoc check — a journal has no reader but its author."""
    return (await db.execute(
        select(JournalEntry)
        .options(selectinload(JournalEntry.emotions))
        .where(JournalEntry.id == entry_id, JournalEntry.user_id == user_id)
    )).scalar_one_or_none()


async def create_entry(
    db: AsyncSession, user_id: uuid.UUID, data: JournalEntryCreate
) -> JournalEntry:
    entry = JournalEntry(
        user_id=user_id,
        entry_date=data.entry_date,
        ciphertext=data.ciphertext,
        nonce=data.nonce,
        key_version=data.key_version,
    )
    db.add(entry)
    await db.flush()

    for emo in data.emotions:
        slug = canonicalize(emo.emotion_id) or emo.emotion_id
        db.add(JournalEmotion(
            journal_entry_id=entry.id, emotion_id=slug, strength=emo.strength,
        ))
    await db.flush()
    return await get_entry(db, entry.id, user_id)


async def list_entries(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 30,
    cursor: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    emotion: str | None = None,
    untagged: bool | None = None,
) -> tuple[list[JournalEntry], int, str | None]:
    """Page the journal newest-day-first. Returns (entries, total, next_cursor).

    Filters are all over *metadata*: a date window, a tag, or "not yet named".
    There is no text filter, by construction — see the module docstring.

    ``untagged=True`` powers the batch-tag prompt ("five days unnamed — name
    them?"), which is lower friction and better data than tagging up front.
    """
    filters = [JournalEntry.user_id == user_id]
    if date_from:
        filters.append(JournalEntry.entry_date >= date_from)
    if date_to:
        filters.append(JournalEntry.entry_date <= date_to)
    if emotion:
        canon = canonicalize(emotion) or emotion
        filters.append(JournalEntry.emotions.any(JournalEmotion.emotion_id == canon))
    if untagged is True:
        filters.append(~JournalEntry.emotions.any())
    elif untagged is False:
        filters.append(JournalEntry.emotions.any())

    total = (await db.execute(
        select(func.count(JournalEntry.id)).where(*filters)
    )).scalar_one()

    query = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.emotions))
        .where(*filters)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
    )

    if cursor:
        cursor_date, cursor_id = _decode_cursor(cursor)
        # Composite keyset — strictly "earlier" in (entry_date, id). Expressed as
        # an OR rather than a row comparison because entry_date is a DATE and the
        # tuple form on mixed date/uuid is needlessly fragile.
        query = query.where(
            (JournalEntry.entry_date < cursor_date)
            | ((JournalEntry.entry_date == cursor_date) & (JournalEntry.id < cursor_id))
        )

    # Over-fetch one row to learn whether there's a next page without a 2nd COUNT.
    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_next = len(rows) > limit
    entries = rows[:limit]
    next_cursor = _encode_cursor(entries[-1]) if has_next and entries else None
    return entries, total, next_cursor


async def update_entry(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID, data: JournalEntryUpdate
) -> JournalEntry | None:
    entry = await get_entry(db, entry_id, user_id)
    if not entry:
        return None

    fields = data.model_dump(exclude_unset=True, exclude={"emotions"})
    for field, value in fields.items():
        if value is not None:
            setattr(entry, field, value)

    if data.emotions is not None:
        await _set_tags(db, entry, data.emotions)

    await db.flush()
    db.expire(entry)
    return await get_entry(db, entry_id, user_id)


async def set_entry_tags(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID, emotions: list[EmotionIn]
) -> JournalEntry | None:
    """Tags-only write: name a day after the fact without re-sending its blob."""
    entry = await get_entry(db, entry_id, user_id)
    if not entry:
        return None
    await _set_tags(db, entry, emotions)
    db.expire(entry)
    return await get_entry(db, entry_id, user_id)


async def delete_entry(
    db: AsyncSession, entry_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    entry = await get_entry(db, entry_id, user_id)
    if not entry:
        return False
    await db.delete(entry)
    await db.flush()
    return True


# ── DNA feed ──

async def load_emotion_sources(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Journal tags as DNA emotion sources — the only reason tags are readable.

    Journal emotions are just another emotion source: the same shape the book
    loader produces, so ``dna_signals.entry_sig`` consumes them unchanged and DNA
    spans reading *and* life.

    Two shaping decisions:
    - Untagged entries are skipped. They carry no signal, and counting them would
      make silence look like indifference.
    - An entry's "intensity" is the mean of its own tag strengths. A journal day
      has no equivalent of a book's overall rating, and inventing a default 5
      would flatten the intensity math with fabricated data.
    """
    rows = (await db.execute(
        select(JournalEntry)
        .options(selectinload(JournalEntry.emotions))
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.entry_date.asc())
    )).scalars().all()

    out: list[dict] = []
    for entry in rows:
        if not entry.emotions:
            continue
        strengths = [em.strength for em in entry.emotions]
        out.append({
            "emotions": [em.emotion_id for em in entry.emotions],
            "intensity": round(sum(strengths) / len(strengths)),
            # The day it's *about*, not the day it was typed — same reasoning as
            # finished_at over created_at on the book side.
            "ts": datetime.combine(entry.entry_date, time.min, tzinfo=timezone.utc),
            "status": "finished",
            "source": "journal",
        })
    return out
