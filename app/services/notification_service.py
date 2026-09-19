"""Notification service (B4.1): classify tier, apply prefs + quiet hours + batching.

Precedence rules (blueprint Feature 5):
  - Tier 0 (security) is immediate, non-disableable, and bypasses quiet hours.
  - Tier 1/2 respect the user's per-tier toggles and quiet hours.
  - Tier 1 batches by `batch_key` so N events collapse into one item
    ("3 readers responded…") rather than N pings.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.push_service import push_to_user
from app.services.realtime_service import publish as realtime_publish
from app.models.notification import (
    Notification,
    NotificationPrefs,
    TIER_DIGEST,
    TIER_DIRECT,
    TIER_SECURITY,
)
from app.services.social_service import is_blocked_between

logger = logging.getLogger("bibliome.notifications")

# How long a thread stays quiet after one knock. A burst of messages is one
# buzz; a conversation still going twenty minutes later earns another, because
# the alternative — what this used to do — was silence until the reader next
# opened the app, however long that took.
REPUSH_AFTER = timedelta(minutes=15)

# How far back the deferred sweep looks. Bounded so a first run, or a restart
# after downtime, cannot re-ring a backlog of old quiet-hours deferrals.
SWEEP_WINDOW = timedelta(hours=1)


def _pushed_recently(payload: dict, now: datetime) -> bool:
    stamp = payload.get("_pushed_at")
    if not stamp:
        return False
    try:
        return now - datetime.fromisoformat(stamp) < REPUSH_AFTER
    except (TypeError, ValueError):
        return False


async def _push(db: AsyncSession, n: Notification, now: datetime) -> None:
    """Knock, and record when — so the next batched event onto the same
    notification can tell a fresh knock from a repeat.

    The stamp lives in the payload rather than a column: no migration, and it
    travels with the row the batching already rewrites. Best effort throughout —
    a failed push must never fail the write that caused it.
    """
    try:
        await push_to_user(db, n.user_id, n.kind, n.payload)
    except Exception:  # noqa: BLE001 - a courtesy layer cannot break the caller
        logger.exception("push failed for notification %s", n.id)
        return
    # Reassigned, not mutated: JSONB changes in place are invisible to SQLAlchemy.
    n.payload = {**n.payload, "_pushed_at": now.isoformat()}
    await db.flush()


async def get_or_create_prefs(db: AsyncSession, user_id: uuid.UUID) -> NotificationPrefs:
    prefs = (await db.execute(
        select(NotificationPrefs).where(NotificationPrefs.user_id == user_id)
    )).scalar_one_or_none()
    if prefs is None:
        prefs = NotificationPrefs(user_id=user_id)
        db.add(prefs)
        await db.flush()
    return prefs


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _quiet_until(now_utc: datetime, prefs: NotificationPrefs) -> datetime | None:
    """If `now` is inside the user's quiet hours, return the UTC instant they end;
    otherwise None. Handles overnight windows (e.g. 22→7)."""
    s, e = prefs.quiet_hours_start, prefs.quiet_hours_end
    if s is None or e is None or s == e:
        return None
    local = now_utc.astimezone(_tz(prefs.timezone))
    h = local.hour + local.minute / 60.0
    overnight = s > e
    in_quiet = (s <= h < e) if not overnight else (h >= s or h < e)
    if not in_quiet:
        return None
    end_today = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=e)
    if end_today <= local:
        end_today += timedelta(days=1)
    return end_today.astimezone(timezone.utc)


def _merge_batch(existing: dict, incoming: dict) -> dict:
    """Collapse a repeat tier-1 event into an existing unread notification.

    `count` tracks distinct actors, not raw events — a reactor switching from
    one reaction kind to another fires two calls to `notify()` for the same
    message (set the new kind; the unset of the old one is silent), and
    blindly incrementing here would turn "1 reader reconsidered" into "2
    readers reacted". Only a genuinely new actor moves the count.
    """
    p = dict(existing)
    actors = list(p.get("actors", []))
    new_actors = [a for a in incoming.get("actors", []) if a and a not in actors]
    if new_actors:
        p["count"] = int(p.get("count", 1)) + len(new_actors)
        actors.extend(new_actors)
        p["actors"] = actors[:5]
    # Any other scalar field (e.g. chat_reaction's `kind`) reflects the most
    # recent event even when the actor was already counted — a reader who
    # switches reactions changes what's true NOW, not just how many times
    # something happened.
    for key, value in incoming.items():
        if key in ("actors", "count"):
            continue
        p[key] = value
    return p


async def notify(
    db: AsyncSession,
    user_id: uuid.UUID,
    tier: int,
    kind: str,
    payload: dict,
    batch_key: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> Notification | None:
    """Create (or coalesce) a notification, honoring tier/prefs/quiet-hours/blocks.
    Returns the notification, or None if suppressed."""
    # Suppress self-notifications and anything from/to a blocked actor.
    if actor_id is not None:
        if actor_id == user_id:
            return None
        if await is_blocked_between(db, user_id, actor_id):
            return None

    now = datetime.now(timezone.utc)
    deliver_after = now

    if tier != TIER_SECURITY:
        prefs = await get_or_create_prefs(db, user_id)
        if tier == TIER_DIRECT and not prefs.reply_enabled:
            return None
        if tier == TIER_DIGEST and not prefs.digest_enabled:
            return None
        quiet_until = _quiet_until(now, prefs)
        if quiet_until is not None:
            deliver_after = quiet_until

    if batch_key is not None:
        existing = (await db.execute(
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.batch_key == batch_key,
                Notification.read_at.is_(None),
            )
            .order_by(Notification.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if existing is not None:
            existing.payload = _merge_batch(existing.payload, payload)
            # Deliver at the earliest appropriate time across batched events.
            if deliver_after < existing.deliver_after.replace(tzinfo=timezone.utc):
                existing.deliver_after = deliver_after
            await db.flush()
            if deliver_after <= now:
                # One knock per REPUSH_AFTER rather than one per event: a burst
                # still collapses into a single buzz, but a conversation that is
                # still going later gets knocked on again. Suppressing every
                # repeat for as long as the notification stays unread is how a
                # closed app goes permanently silent on an active thread.
                if not _pushed_recently(existing.payload, now):
                    await _push(db, existing, now)
                # Realtime is a data-sync nudge, not a buzz — an open thread
                # should refresh on message #2 regardless of the push cooldown.
                await realtime_publish(user_id, {"type": "notify", "kind": kind})
            return existing

    n = Notification(
        user_id=user_id, tier=tier, kind=kind, payload=payload,
        batch_key=batch_key, deliver_after=deliver_after,
    )
    db.add(n)
    await db.flush()

    # Push rides on the SAME decisions made above — prefs, blocks, self-suppression
    # and quiet hours — rather than re-deriving them. Anything that stopped a
    # notification being created has already returned; anything deferred by quiet
    # hours is not pushed now, because the point of quiet hours is the phone
    # staying silent. Best effort: a failed push must never fail the write that
    # caused it.
    if deliver_after <= now:
        await _push(db, n, now)
        # Instant in-app delivery for any tab this user has open. Best-effort and
        # already swallows its own errors.
        await realtime_publish(user_id, {"type": "notify", "kind": kind})

    return n


async def sweep_deferred_pushes(db: AsyncSession) -> int:
    """Knock for notifications whose quiet-hours deferral has just elapsed.

    ``notify()`` cannot push these: when they were written the phone was meant
    to stay silent. Nothing revisited them afterwards, so the deferral was not
    "quiet until 7am" but "silent forever" — the reader found out by opening the
    app. This is the other half of quiet hours.

    Only genuine deferrals (``deliver_after > created_at``) that matured inside
    SWEEP_WINDOW and were never pushed are eligible, so a restart re-rings
    nothing.
    """
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(Notification)
        .where(
            Notification.read_at.is_(None),
            Notification.deliver_after <= now,
            Notification.deliver_after > now - SWEEP_WINDOW,
            Notification.deliver_after > Notification.created_at,
            Notification.payload["_pushed_at"].astext.is_(None),
        )
        .order_by(Notification.deliver_after)
        # ponytail: unindexed scan capped at 500 a sweep. Add a partial index on
        # (deliver_after) where read_at is null if the table ever gets big.
        .limit(500)
    )).scalars().all()

    for n in rows:
        await _push(db, n, now)
        await realtime_publish(n.user_id, {"type": "notify", "kind": n.kind})
    return len(rows)


async def list_notifications(db: AsyncSession, user_id: uuid.UUID, limit: int = 30) -> list[Notification]:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.deliver_after <= now)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def unread_count(db: AsyncSession, user_id: uuid.UUID) -> int:
    now = datetime.now(timezone.utc)
    return (await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.deliver_after <= now,
        )
    )).scalar() or 0


async def mark_read(db: AsyncSession, user_id: uuid.UUID, ids: list[uuid.UUID] | None = None) -> None:
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if ids:
        stmt = stmt.where(Notification.id.in_(ids))
    await db.execute(stmt.values(read_at=datetime.now(timezone.utc)))
    await db.flush()
