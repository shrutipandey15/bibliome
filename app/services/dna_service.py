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
from app.services.dna_engine import calculate_personality, dna_type_slug_for
from app.services.dna_insights import build_dna
from app.services.journal_service import load_emotion_sources as load_journal_sources
from app.services.notification_service import notify


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
            "emotions": [em.emotion_id for em in e.emotions],
            "intensity": e.intensity,
            "created_at": e.created_at,
            "finished_at": e.finished_at,
            "status": e.status,
            "arc_start": e.arc_start_emotion_id,
            "arc_end": e.arc_end_emotion_id,
            "dnf_reason": e.dnf_reason,
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


def card_payload(user: User) -> dict | None:
    """The one shape every public surface renders.

    Reads the cache only — a public path must never recompute, and must never see a
    different engine than the owner's own DNA tab. Returns None when there is no
    card to show, which the caller renders as "not ready yet" rather than filling
    in with a second opinion from somewhere else.
    """
    v2 = user.cached_dna_v2
    if not v2 or not v2.get("enough") or not v2.get("archetype"):
        return None
    return {
        "archetype": v2["archetype"],
        "archetype_scores": v2["archetype_scores"],
        "margin": v2.get("margin"),
        # The hedge has to travel with the label. build_dna decides a close call is
        # too close to assert outright and names the runner-up; a card that drops
        # it asserts the noun flatly for exactly the readers the engine was least
        # sure about. Same reader, two surfaces, different confidence is the same
        # class of bug as P0-1 — the numbers agreeing is not enough if the hedging
        # doesn't.
        "runner_up": v2.get("runner_up"),
        "basis": v2.get("basis"),
        "book_count": v2["book_count"],
        # Books only, deliberately: `profiles.current` spans the journal, and a
        # stranger must not be able to read emotion frequencies out of someone's
        # private life even in aggregate. A cache written before `current_books`
        # existed simply carries no emotions rather than falling back to the
        # journal-spanning vector.
        "top_emotions": [
            {"emotion_id": s, "weight": round(w, 4)}
            for s, w in sorted(
                v2["profiles"].get("current_books", {}).items(), key=lambda kv: -kv[1]
            )[:5]
            if w > 0
        ],
    }


async def compute_and_cache(db: AsyncSession, user: User) -> dict:
    """Recompute both payloads and store them on the user row. Returns the private
    Phase-7 payload (what the owner's mirror renders). No snapshot side effects."""
    raw = await _load_raw(db, user.id)
    sigs = [sig.entry_sig(r) for r in raw]
    journal_sigs = await _load_journal_sigs(db, user.id)
    ctx = await _snapshot_context(db, user.id)

    v2 = build_dna(sigs, user.reads_for, journal_sigs=journal_sigs,
                   prev_snapshot=ctx.prev_emotion_data, snapshot_count=ctx.count)

    # Legacy public signature — books ONLY, deliberately. This payload is reused as
    # the *public* profile signature, and the journal is private: a stranger must
    # not be able to read emotion frequencies out of someone's private life, even
    # in aggregate. The private mirror (v2, above) is where life and reading meet.
    legacy = calculate_personality(raw)
    legacy["book_count"] = len(raw)

    user.cached_dna_v2 = v2
    user.cached_dna_profile = legacy
    # The headline archetype now comes from the recency-weighted profile so it can
    # change (B7.5); None until there's enough data.
    user.personality_type = v2["archetype"]["name"] if v2.get("archetype") else None
    user.dna_dirty = False
    await db.flush()
    return v2


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
        },
        book_count=v2["book_count"],
        year=now.year,
        trigger="manual",
    )
    db.add(snapshot)
    await db.flush()
    return snapshot


async def maybe_snapshot_and_notify(db: AsyncSession, user: User) -> DNASnapshot | None:
    """Capture a snapshot on drift or monthly cadence; notify on archetype shift.

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
    vector_sigs = sigs + await _load_journal_sigs(db, user.id)
    current = sig.frequency_vector(vector_sigs, weighted=True)
    enduring = sig.frequency_vector(vector_sigs, weighted=False)
    archetype_id, _, _ = sig.score_archetype(current)
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

    should = (
        ctx.prev_emotion_data is None
        or (snap_drift is not None and snap_drift >= sig.DRIFT_SNAPSHOT_THRESHOLD)
        or (age_days is not None and age_days >= sig.MONTHLY_CADENCE_DAYS)
    )
    if not should:
        return None

    trigger = "drift" if (snap_drift is not None and snap_drift >= sig.DRIFT_SNAPSHOT_THRESHOLD) \
        else ("cadence" if ctx.prev_emotion_data is not None else "manual")

    snapshot = DNASnapshot(
        user_id=user.id,
        personality_type=archetype_name,
        dna_type_slug=dna_type_slug_for(archetype_id),
        emotion_data={
            "enduring_vector": enduring,
            "current_vector": current,
            "archetype_id": archetype_id,
            "drift": snap_drift,
        },
        book_count=len(raw),
        year=now.year,
        trigger=trigger,
    )
    db.add(snapshot)
    await db.flush()

    # The honest return hook: the archetype genuinely changed. Tier-1 (B7.4).
    if ctx.last_archetype and ctx.last_archetype != archetype_name:
        await notify(
            db, user.id, TIER_DIRECT, "dna_shifted",
            payload={"old": ctx.last_archetype, "new": archetype_name},
        )
    return snapshot
