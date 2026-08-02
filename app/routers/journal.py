"""Journal endpoints — CRUD on ciphertext, plus the key bundle.

What the server does here: validate structure, store blobs, serve them back, and
read the plaintext tags for DNA. What it cannot do, anywhere in this file: read an
entry, search entries, decrypt a key, or recover a journal for a user who has lost
both their password and their recovery code. See ``journalCryptoContract.md``.

Note the absence of a search endpoint and of any ``q`` parameter on the list
route. That is deliberate and permanent (contract §4).
"""

import logging
import uuid
from datetime import date

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryListResponse,
    JournalEntryResponse,
    JournalEntryUpdate,
    JournalKeyBundleIn,
    JournalKeyBundleOut,
    JournalKeyBundleReWrap,
    JournalTagsUpdate,
)
from app.services.auth_service import verify_password
from app.services.background import recalculate_dna
from app.services.journal_service import (
    InvalidJournalCursor,
    KeyBundleExists,
    KeyBundleMissing,
    create_entry,
    create_key_bundle,
    delete_entry,
    get_entry,
    get_key_bundle,
    list_entries,
    replace_key_bundle,
    set_entry_tags,
    update_entry,
)

logger = logging.getLogger("bibliome.journal")

# Writes are per-user and unshared, so the cap only needs to stop runaway clients
# and blob-storage abuse — not spam, since there is no one to spam.
journal_limiter = RateLimiter(max_requests=120, window_seconds=60, prefix="journal")

router = APIRouter(prefix="/journal", tags=["journal"])


async def _dirty_dna(db: AsyncSession, user: User, background_tasks: BackgroundTasks):
    """A changed tag changes the DNA — journal emotions feed the same pipeline as
    book emotions (VISION §6). Nothing feeds the *book* aggregates: those are a
    population's reaction to a specific book, and a journal entry has no book."""
    user.dna_dirty = True
    await db.flush()
    background_tasks.add_task(recalculate_dna, user.id)


# ── Key management ──

@router.get("/key", response_model=JournalKeyBundleOut)
async def read_key_bundle(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch the wrapped key material so the client can unwrap it locally.

    Serving this is safe: without the password or the recovery code it is inert,
    and the session that fetches it already belongs to its owner.
    """
    bundle = await get_key_bundle(db, current_user.id)
    if bundle is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No journal key set up for this account.",
        )
    return JournalKeyBundleOut.model_validate(bundle)


@router.post("/key", response_model=JournalKeyBundleOut, status_code=status.HTTP_201_CREATED)
async def setup_key_bundle(
    request: Request,
    data: JournalKeyBundleIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One-time journal setup: store both wrappings of the client's data key.

    409 on a second call. Overwriting a bundle orphans every entry sealed under
    the old key, so it can only happen through the explicit re-wrap route.
    """
    await journal_limiter.check(request)
    try:
        bundle = await create_key_bundle(db, current_user.id, data)
    except KeyBundleExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A journal key already exists. Overwriting it would make every "
                "existing entry permanently unreadable. Use PUT /journal/key to "
                "re-wrap the same key."
            ),
        )
    logger.info("Journal key bundle created for user %s", current_user.id)
    return JournalKeyBundleOut.model_validate(bundle)


@router.put("/key", response_model=JournalKeyBundleOut)
async def rewrap_key_bundle(
    request: Request,
    data: JournalKeyBundleReWrap,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace the bundle with a re-wrap of the same data key.

    The normal path after unlocking with the recovery code (e.g. following a
    password reset): the client re-wraps under the current password and PUTs the
    result. Gated on the account password so a hijacked session alone cannot
    overwrite the bundle and lock the owner out of their own journal.

    The server cannot check that the new bundle wraps the *same* key — it has
    neither key. If a client gets this wrong, the entries are unreadable, and no
    amount of server-side logic could have prevented it.
    """
    await journal_limiter.check(request)
    if not await verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    bundle_data = JournalKeyBundleIn.model_validate(
        data.model_dump(exclude={"current_password"})
    )
    try:
        bundle = await replace_key_bundle(db, current_user.id, bundle_data)
    except KeyBundleMissing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No journal key set up for this account. Use POST /journal/key.",
        )
    logger.info("Journal key bundle re-wrapped for user %s", current_user.id)
    return JournalKeyBundleOut.model_validate(bundle)


# ── Entries ──

@router.get("/entries", response_model=JournalEntryListResponse)
async def get_journal_entries(
    cursor: str | None = Query(default=None, description="Opaque cursor from next_cursor"),
    limit: int = Query(default=30, ge=1, le=100),
    date_from: date | None = Query(default=None, description="Inclusive lower bound on entry_date"),
    date_to: date | None = Query(default=None, description="Inclusive upper bound on entry_date"),
    emotion: str | None = Query(default=None, max_length=30, description="Filter by tag slug"),
    untagged: bool | None = Query(
        default=None, description="true → only unnamed days; false → only named ones"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Page the journal, newest day first — one continuous book, not a card list.

    Returns ciphertext + dates + tags; the client decrypts. There is no `q`
    parameter and there will never be one: the server cannot read these blobs, so
    it cannot search them. Search happens client-side after decryption.
    """
    try:
        entries, total, next_cursor = await list_entries(
            db, current_user.id,
            limit=limit, cursor=cursor,
            date_from=date_from, date_to=date_to,
            emotion=emotion, untagged=untagged,
        )
    except InvalidJournalCursor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor.",
        )
    return JournalEntryListResponse(
        entries=[JournalEntryResponse.model_validate(e) for e in entries],
        total=total,
        next_cursor=next_cursor,
        has_more=next_cursor is not None,
    )


@router.post("/entries", response_model=JournalEntryResponse, status_code=status.HTTP_201_CREATED)
async def create_journal_entry(
    request: Request,
    data: JournalEntryCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Store one sealed entry.

    Requires a key bundle first: an entry with no bundle on file is ciphertext
    nobody can ever open, and accepting it would be accepting garbage.
    """
    await journal_limiter.check(request)
    if await get_key_bundle(db, current_user.id) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Set up a journal key first (POST /journal/key).",
        )

    entry = await create_entry(db, current_user.id, data)
    if data.emotions:
        await _dirty_dna(db, current_user, background_tasks)
    return JournalEntryResponse.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=JournalEntryResponse)
async def get_journal_entry(
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One entry, still sealed."""
    entry = await get_entry(db, entry_id, current_user.id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return JournalEntryResponse.model_validate(entry)


@router.put("/entries/{entry_id}", response_model=JournalEntryResponse)
async def update_journal_entry(
    request: Request,
    entry_id: uuid.UUID,
    data: JournalEntryUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Replace an entry's ciphertext, date, and/or tags.

    Editing text means re-encrypting client-side, so `ciphertext` and `nonce`
    arrive together (the schema enforces it) — a fresh nonce per encryption is not
    optional, and this API will not let a client half-update its way into reuse.
    """
    await journal_limiter.check(request)
    entry = await update_entry(db, entry_id, current_user.id, data)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    if data.emotions is not None:
        await _dirty_dna(db, current_user, background_tasks)
    return JournalEntryResponse.model_validate(entry)


@router.put("/entries/{entry_id}/tags", response_model=JournalEntryResponse)
async def set_journal_entry_tags(
    request: Request,
    entry_id: uuid.UUID,
    data: JournalTagsUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Name a day (or rename it) without touching its ciphertext.

    Tagging is never a gate: the page can stay blank of tags for as long as the
    writer likes, and naming a feeling in retrospect is both less friction and
    better data than a mood picker before writing.
    """
    await journal_limiter.check(request)
    entry = await set_entry_tags(db, entry_id, current_user.id, data.emotions)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    await _dirty_dna(db, current_user, background_tasks)
    return JournalEntryResponse.model_validate(entry)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_journal_entry(
    request: Request,
    entry_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an entry and its tags. Gone from the DNA on the next recalc."""
    await journal_limiter.check(request)
    deleted = await delete_entry(db, entry_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    await _dirty_dna(db, current_user, background_tasks)
