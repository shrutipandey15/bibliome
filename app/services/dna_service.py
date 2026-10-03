"""DNA orchestration (Phase 7): load → compute → cache, and snapshot-on-drift.

Keeps the pure math (dna_signals / dna_insights) free of I/O. Two responsibilities:

- ``compute_and_cache`` — build the private Phase-7 payload (recency profiles +
  insights) AND the legacy public signature, and store both on the user row. A
  plain read; no side effects on history.
- ``maybe_snapshot_and_notify`` — capture a snapshot when the reader has moved far
  enough (drift) or on a monthly cadence, and fire the honest "your DNA shifted"
  notification when the archetype actually changes (B7.4).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.dna_snapshot import DNASnapshot
from app.models.notification import TIER_DIRECT
from app.models.user import User
from app.services import dna_signals as sig
from app.services.dna_card import card_payload_from
from app.services.dna_engine import ARCHETYPE_TABLE_REV, calculate_personality, dna_type_slug_for
from app.services.dna_insights import build_dna
from app.services.journal_service import load_emotion_sources as load_journal_sources
from app.services.notification_service import notify

# The stamp a snapshot carries so a label change can be told apart from a change
# in our own rules: the archetype table AND the climate's switch rules. A shift
# across a change in either is ours, not the reader's — snapshot, don't notify.
RULES_REV = f"{ARCHETYPE_TABLE_REV}/{sig.CLIMATE_RULES}"


async def _load_raw(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Slim load — only the columns the signal math needs (Part 4 perf)."""
    result = await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.asc())
    )
    rows = result.scalars().all()
    return [
        {
            # id/title/updated_at feed the aliveness layer: the echo finds the
            # book just saved, and moments are named by their book.
            "id": str(e.id),
            "title": e.title,
            "updated_at": e.updated_at,
            "emotions": [em.emotion_id for em in e.emotions],
            "intensity": e.intensity,
            "created_at": e.created_at,
            "finished_at": e.finished_at,
            "status": e.status,
            "arc_start": e.arc_start_emotion_id,
            "arc_end": e.arc_end_emotion_id,
            "dnf_reason": e.dnf_reason,
            "verdict": e.verdict,
        }
        for e in rows
    ]


async def _load_journal_sigs(db: AsyncSession, user_id: uuid.UUID) -> list[sig.EntrySig]:
    """Named journal days as EntrySigs — the same shape books produce.

    Journal emotions are just another emotion source. The prose is ciphertext we
    cannot read and never load; only the plaintext tags come through here, which is
    the entire reason those tags are stored readable (VISION §6).
    """
    return [sig.entry_sig(r) for r in await load_journal_sources(db, user_id)]


@dataclass
class _SnapContext:
    prev_emotion_data: dict | None
    count: int
    last_generated_at: datetime | None
    last_archetype: str | None


async def _snapshot_context(db: AsyncSession, user_id: uuid.UUID) -> _SnapContext:
    latest = (await db.execute(
        select(DNASnapshot)
        .where(DNASnapshot.user_id == user_id)
        .order_by(DNASnapshot.generated_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    count = (await db.execute(
        select(func.count(DNASnapshot.id)).where(DNASnapshot.user_id == user_id)
    )).scalar() or 0
    if latest is None:
        return _SnapContext(None, 0, None, None)
    return _SnapContext(latest.emotion_data, count, latest.generated_at, latest.personality_type)


def is_fresh(cache: dict | None) -> bool:
    """Whether a `cached_dna_v2` payload is the SHAPE we serve today.

    A payload cached before a shape change is stale in a way `dna_dirty` can't
    know about: nothing changed about the reader, only about what we serve. Lives
    here rather than in the DNA router because the profile card reads the same
    cache and has to make the same call — when they disagreed, the DNA tab
    recomputed and the profile served last month's shape, which is precisely how
    the same reader ends up looking at two different cards.

      - `snapshot_count`: added as a top-level field the client depends on.
      - locked rows without `need`: cached before the "Not yet" copy started
        naming each gate's real population and the reader's count against it.
      - no `earned` key: cached before the Register's positive column existed.
      - no `emotion_counts`: cached before the tally the card's fingerprint is
        drawn from was returned at all, so the card falls back to a share vector
        and mislabels it as a book count.
      - no `echo` / `eras`: cached before the aliveness layer, and under the old
        archetype rule.
      - a season without `counts`: cached before the share card's season story.
    """
    if not cache:
        return False
    if "snapshot_count" not in cache:
        return False
    if cache.get("enough") and ("earned" not in cache or "emotion_counts" not in cache):
        return False
    # Cached before the aliveness layer (echo, seasons, eras, moments) existed.
    if "alive_state" not in cache or (cache.get("enough") and "eras" not in cache):
        return False
    # Cached before the share card drew the season story's own bloom.
    if (cache.get("season") or {}).get("id") and "counts" not in cache["season"]:
        return False
    return all("need" in row for row in (cache.get("locked") or []))


async def _shelf_stamp(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """A cheap fingerprint of the shelf a payload was computed from.

    One indexed aggregate — deliberately not a hash of the contents. It has to be
    cheaper than the recompute it guards, and count + last-touched catches every
    add, delete and edit, because every write path bumps `updated_at`.
    """
    n, last = (await db.execute(
        select(func.count(BookEntry.id), func.max(BookEntry.updated_at))
        .where(BookEntry.user_id == user_id)
    )).one()
    return {"n": n or 0, "last": last.isoformat() if last else None}


async def cache_is_current(db: AsyncSession, user: User) -> bool:
    """Whether the cached payload still matches the shelf as it stands NOW.

    `dna_dirty` is the fast path and stays the primary mechanism. This is the net
    under it, because that flag is a promise made by whoever cleared it: a recalc
    that was dropped, crashed, or raced another worker clears it over a payload
    computed from a shelf that has since moved, and from then on every read
    trusts the flag and serves the stale card — forever, since nothing ever
    re-examines a cache marked clean. That failure used to need a human with a
    backfill script to notice and undo. Now the next read undoes it.

    A payload cached before this stamp existed has no `shelf_stamp` at all, so it
    fails the comparison and gets recomputed once, which is exactly the repair the
    backfill was for.
    """
    cache = user.cached_dna_v2
    if not is_fresh(cache):
        return False
    return cache.get("shelf_stamp") == await _shelf_stamp(db, user.id)


def card_payload(user: User, *, owner: bool = False) -> dict | None:
    """The one shape every card surface renders (dna_card.card_payload_from).

    Reads the cache only — a public path must never recompute, and must never see a
    different engine than the owner's own DNA tab. Returns None when there is no
    card to show, which the caller renders as "not ready yet" rather than filling
    in with a second opinion from somewhere else.

    Strangers get the reader's share choices applied (season, red flag) on every
    surface: the /s/ page, its link preview, a public profile. The owner's own
    view (`owner=True`, the DNA tab's share sheet) carries both, plus the
    switches themselves, so the sheet can preview either setting without a
    refetch.
    """
    if owner:
        return card_payload_from(
            user.cached_dna_v2,
            choices={"season": user.card_show_season, "red_flag": user.card_show_red_flag},
        )
    return card_payload_from(
        user.cached_dna_v2,
        show_season=user.card_show_season, show_red_flag=user.card_show_red_flag,
    )


async def compute_and_cache(db: AsyncSession, user: User) -> dict:
    """Recompute both payloads and store them on the user row. Returns the private
    Phase-7 payload (what the owner's mirror renders). No snapshot side effects."""
    raw = await _load_raw(db, user.id)
    sigs = [sig.entry_sig(r) for r in raw]
    journal_sigs = await _load_journal_sigs(db, user.id)
    ctx = await _snapshot_context(db, user.id)

    prev = user.cached_dna_v2
    v2 = build_dna(sigs, user.reads_for, journal_sigs=journal_sigs,
                   prev_snapshot=ctx.prev_emotion_data, snapshot_count=ctx.count,
                   echo_entry_id=_latest_touched(raw),
                   # What the reader was last shown, if that cache is of this
                   # layer's shape; the echo's extra line is measured against it.
                   prev_state=(prev or {}).get("alive_state") if is_fresh(prev) else None)
    if v2.get("enough"):
        v2["eras"] = _with_books_that_moved(v2["eras"], sigs)

    # Legacy public signature — books ONLY, deliberately. This payload is reused as
    # the *public* profile signature, and the journal is private: a stranger must
    # not be able to read emotion frequencies out of someone's private life, even
    # in aggregate. The private mirror (v2, above) is where life and reading meet.
    legacy = calculate_personality(raw)
    legacy["book_count"] = len(raw)

    # Stamp the shelf this payload was computed from, so a later read can catch a
    # `dna_dirty` that was cleared over stale math. Written INSIDE the same
    # transaction as the flag it backs up.
    v2["shelf_stamp"] = await _shelf_stamp(db, user.id)

    user.cached_dna_v2 = v2
    user.cached_dna_profile = legacy
    # The headline archetype now comes from the recency-weighted profile so it can
    # change (B7.5); None until there's enough data.
    user.personality_type = v2["archetype"]["name"] if v2.get("archetype") else None
    # The first archetype a reader is given is not a "shift": record it as seen,
    # so the shift card only ever announces a change that happened after it.
    if v2.get("archetype") and user.dna_seen_archetype is None:
        user.dna_seen_archetype = v2["archetype"]["id"]
    user.dna_dirty = False
    await db.flush()
    return v2


def _latest_touched(raw: list[dict]) -> str | None:
    """The opened book with a feeling that was saved most recently — the one the
    echo speaks about. The client shows the echo only when this matches the book
    it just saved, and only for saves that should get one (not imports, not a
    notes-only edit), so picking "the latest" here is safe."""
    rows = [r for r in raw if r.get("emotions") and r.get("status") in sig.OPENED_STATUSES
            and r.get("updated_at") is not None]
    if not rows:
        return None
    return max(rows, key=lambda r: r["updated_at"])["id"]


def _with_books_that_moved(eras: list[dict], sigs: list[sig.EntrySig]) -> list[dict]:
    """For the current era, when it follows another: up to three books, from the
    dozen read up to the change, that point to the new archetype — strongest
    first. These are what the shift card names."""
    if len(eras) < 2 or not eras[0].get("from"):
        return eras
    new_id, start = eras[0]["id"], eras[0]["from"]
    in_order = [s for s in sig._reading_order(sigs)
                if s.emotions and s.ts.date().isoformat() <= start][-12:]
    movers = [s for s in in_order if sig.book_archetype_of([s]) == new_id]
    movers.sort(key=lambda s: -s.intensity)
    head = {**eras[0], "books_that_moved": [
        {"entry_id": s.entry_id, "title": s.title} for s in movers[:3]
    ]}
    return [head, *eras[1:]]


def shift_unseen(user: User, v2: dict | None) -> bool:
    """Whether the DNA page should open with the shift card: the archetype changed
    since the reader last acknowledged one. Per request, not cached, because
    acknowledging it doesn't recompute the DNA."""
    if not v2 or not v2.get("enough") or not v2.get("archetype"):
        return False
    return len(v2.get("eras") or []) >= 2 and user.dna_seen_archetype != v2["archetype"]["id"]


async def manual_snapshot(db: AsyncSession, user: User, v2: dict) -> DNASnapshot:
    """Capture a snapshot the reader asked for (POST /dna/generate), from the same
    payload their DNA tab renders.

    The caller must have checked that ``v2`` has an archetype — a snapshot is a
    permanent record of what the reader was told, so it must never contain a label
    the mirror declined to show them. Writes the same ``emotion_data`` shape as
    ``maybe_snapshot_and_notify`` so the evolution timeline is homogeneous
    regardless of which path created a point on it.
    """
    ctx = await _snapshot_context(db, user.id)
    archetype = v2["archetype"]
    current = v2["profiles"]["current"]
    prev_current = (ctx.prev_emotion_data or {}).get("current_vector")
    now = datetime.now(timezone.utc)

    snapshot = DNASnapshot(
        user_id=user.id,
        personality_type=archetype["name"],
        dna_type_slug=dna_type_slug_for(archetype["id"]),
        emotion_data={
            "enduring_vector": v2["profiles"]["enduring"],
            "current_vector": current,
            "archetype_id": archetype["id"],
            "archetype_scores": v2["archetype_scores"],
            "margin": v2.get("margin"),
            "drift": sig.drift(prev_current, current) if prev_current else None,
            "archetype_table_rev": RULES_REV,
        },
        book_count=v2["book_count"],
        year=now.year,
        trigger="manual",
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def maybe_snapshot_and_notify(
    db: AsyncSession, user: User, *, notify_shift: bool = True,
) -> DNASnapshot | None:
    """Capture a snapshot on drift, a changed archetype, or monthly cadence;
    notify on archetype shift.

    Called from the post-commit recalc and /dna/generate — never from a plain read.
    Guarded by the drift gate so early noise never snapshots.
    """
    raw = await _load_raw(db, user.id)
    if len(raw) < sig.GATES["drift"]:
        return None
    sigs = [sig.entry_sig(r) for r in raw]
    ctx = await _snapshot_context(db, user.id)

    # Snapshot the same vectors the mirror renders — books plus named journal days.
    # If these two diverged, drift would be measured against a profile the reader
    # was never shown, and the "your DNA shifted" notice would be unfalsifiable.
    journal_sigs = await _load_journal_sigs(db, user.id)
    vector_sigs = sigs + journal_sigs
    current = sig.frequency_vector(vector_sigs, weighted=True)
    enduring = sig.frequency_vector(vector_sigs, weighted=False)
    # The replayed climate, the same function build_dna uses, so the snapshot's
    # label can't disagree with the one the mirror shows.
    archetype_id = sig.replay_climate(sigs, journal_sigs)["archetype_id"]
    if archetype_id is None:
        # Nothing to name, so nothing has shifted. A snapshot here would record an
        # archetype the engine declined to give.
        return None
    archetype_name = sig.archetype_dict(archetype_id)["name"]

    now = datetime.now(timezone.utc)
    prev_current = (ctx.prev_emotion_data or {}).get("current_vector")
    snap_drift = sig.drift(prev_current, current) if prev_current else None
    age_days = (now - ctx.last_generated_at.replace(tzinfo=timezone.utc)).days \
        if ctx.last_generated_at else None

    # A changed NAME is its own reason to snapshot, independent of how far the
    # vector moved. Near a boundary one book flips the archetype on a drift far
    # below the threshold — the mirrors all re-render under the new name (they
    # read `cached_dna_v2`, rewritten on every recalc), while the timeline still
    # ends on the old one and the reader is never told, because the "you shifted"
    # notice below only fires on a snapshot. That is the same reader looking at
    # two different labels, which is the one thing every cache here exists to
    # prevent.
    drifted = snap_drift is not None and snap_drift >= sig.DRIFT_SNAPSHOT_THRESHOLD
    renamed = bool(ctx.last_archetype) and ctx.last_archetype != archetype_name
    should = (
        ctx.prev_emotion_data is None
        or drifted
        or renamed
        or (age_days is not None and age_days >= sig.MONTHLY_CADENCE_DAYS)
    )
    if not should:
        return None

    trigger = "drift" if drifted \
        else ("archetype" if renamed
              else ("cadence" if ctx.prev_emotion_data is not None else "manual"))

    snapshot = DNASnapshot(
        user_id=user.id,
        personality_type=archetype_name,
        dna_type_slug=dna_type_slug_for(archetype_id),
        emotion_data={
            "enduring_vector": enduring,
            "current_vector": current,
            "archetype_id": archetype_id,
            "drift": snap_drift,
            "archetype_table_rev": RULES_REV,
        },
        book_count=len(raw),
        year=now.year,
        trigger=trigger,
    )
    db.add(snapshot)
    await db.flush()

    # The honest return hook: the archetype genuinely changed — AND it changed
    # under the same archetype table. If the previous snapshot was written under a
    # different rev (or before the field existed), a re-anchor is at least partly
    # responsible, so we take the snapshot but don't tell the reader they shifted.
    prev_rev = (ctx.prev_emotion_data or {}).get("archetype_table_rev")
    same_table = prev_rev == RULES_REV
    # A bulk import moves the shelf in one go; announcing the result as "your DNA
    # moved" would describe our catching up, not their reading.
    if notify_shift and same_table and ctx.last_archetype and ctx.last_archetype != archetype_name:
        await notify(
            db, user.id, TIER_DIRECT, "dna_shifted",
            payload={"old": ctx.last_archetype, "new": archetype_name},
        )
    return snapshot
