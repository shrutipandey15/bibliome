"""Moderation + safety core (B3.7 / B3.8 / B3.9).

Ships WITH Echo, not after. Three layers:
  - Pre-publish classifier: slurs/threats/PII → hold-for-review; self-harm → the
    supportive crisis path (never a punitive block).
  - Report → reputation-weighted pressure → auto-throttle above threshold → queue.
The classifier is deliberately lightweight (keyword/regex), so the request path
stays fast and dependency-free; a real ML classifier can slot in behind classify_text.
"""

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.echo import Echo, EchoReply
from app.models.resonance import ResonanceMessage, ResonanceThread
from app.models.social import Report
from app.models.user import User

# Verdicts from the pre-publish classifier.
VERDICT_OK = "ok"
VERDICT_HOLD = "hold"       # publish but mark held / route to review
VERDICT_CRISIS = "crisis"   # self-harm → supportive interstitial, not a block

# Weighted open-report pressure at which an echo is auto-held.
REPORT_THROTTLE_THRESHOLD = 3.0

CRISIS_RESOURCES = {
    "message": "It sounds like you're going through something painful. You're not alone.",
    "resources": [
        {"name": "988 Suicide & Crisis Lifeline (US)", "contact": "Call or text 988"},
        {"name": "Crisis Text Line", "contact": "Text HOME to 741741"},
        {"name": "International Association for Suicide Prevention", "contact": "https://www.iasp.info/resources/Crisis_Centres/"},
    ],
}

# Phrases suggesting the *author* may be in crisis (first person). Kept narrow to
# avoid flagging book discussion ("the character kills himself").
_SELF_HARM_RE = re.compile(
    r"\b(i (want|'m going|am going|plan) to (die|kill myself|end (it|my life))"
    r"|i (want|need) to (die|disappear)"
    r"|kill(ing)? myself|end(ing)? my life|take my (own )?life|"
    r"i (can'?t|cannot) go on|no reason to live|better off dead)\b",
    re.IGNORECASE,
)

_SLUR_THREAT_RE = re.compile(
    r"\b(i('?ll| will) (kill|hurt|find) you|kill yourself|kys|"
    r"go die|you should die|i hope you die)\b",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\-\s().]{8,}\d)(?!\d)")


def classify_text(text: str) -> tuple[str, str | None]:
    """Return (verdict, reason). Self-harm wins (care over punishment)."""
    if _SELF_HARM_RE.search(text):
        return VERDICT_CRISIS, "self_harm"
    if _SLUR_THREAT_RE.search(text):
        return VERDICT_HOLD, "threat"
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        return VERDICT_HOLD, "pii"
    return VERDICT_OK, None


async def reporter_weight(db: AsyncSession, reporter_id: uuid.UUID) -> float:
    """Weight a reporter's signal by track record, to resist report-bombing.

    New/clean reporters count fully (1.0). Reporters whose past reports were
    dismissed count for less, down to a floor — a brigade of bad-faith accounts
    can't cross the throttle threshold on volume alone.
    """
    r = await db.execute(
        select(Report.status, func.count(Report.id))
        .where(Report.reporter_id == reporter_id, Report.status.in_(("resolved", "dismissed")))
        .group_by(Report.status)
    )
    counts = {status: n for status, n in r.all()}
    resolved = counts.get("resolved", 0)
    dismissed = counts.get("dismissed", 0)
    total = resolved + dismissed
    if total == 0:
        return 1.0
    # Fraction of adjudicated reports that were upheld, floored so no one hits 0.
    return max(0.2, min(1.5, (resolved + 1) / (total + 1)))


# Public surfaces: throttleable (hiding one costs nobody a conversation) and
# resolvable by removal. Private threads are neither — see resolve_target.
PUBLIC_MODELS = {"echo": Echo, "reply": EchoReply}
REPORTABLE_TYPES = frozenset({*PUBLIC_MODELS, "thread"})


async def _weighted_open_pressure(db: AsyncSession, target_type: str, target_id: uuid.UUID) -> float:
    r = await db.execute(
        select(Report.reporter_id).where(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == "open",
        )
    )
    reporter_ids = list(r.scalars().all())
    total = 0.0
    for rid in reporter_ids:
        total += await reporter_weight(db, rid)
    return total


async def submit_report(
    db: AsyncSession,
    reporter_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    category: str,
) -> bool:
    """Record a report (idempotent per reporter+target) and auto-throttle the
    target if weighted open-report pressure crosses the threshold. Returns True
    if this call created a new report.
    """
    stmt = (
        pg_insert(Report)
        .values(
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
            category=category,
        )
        .on_conflict_do_nothing(constraint="uq_report_once")
        .returning(Report.id)
    )
    result = await db.execute(stmt)
    created = result.scalar_one_or_none() is not None
    await db.flush()

    # Auto-throttle: hide the target from the feed while it awaits review. Only
    # public surfaces can be throttled this way — a reported private thread goes
    # to the queue, but auto-hiding it would silence a conversation on one
    # party's say-so. There, blocking (which the reporter does themselves) is the
    # remedy, not moderation pressure.
    Model = PUBLIC_MODELS.get(target_type)
    if Model is not None:
        pressure = await _weighted_open_pressure(db, target_type, target_id)
        if pressure >= REPORT_THROTTLE_THRESHOLD:
            obj = (await db.execute(select(Model).where(Model.id == target_id))).scalar_one_or_none()
            if obj is not None and obj.status == "active":
                obj.status = "held"
                await db.flush()

    return created


# How much of a reported body the queue shows. Enough to judge a report on
# without turning the queue into a reading surface for held content.
PREVIEW_CHARS = 280


async def _public_context(
    db: AsyncSession, target_type: str, ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Body preview + author handle + live status for echoes/replies, in one query."""
    Model = PUBLIC_MODELS[target_type]
    rows = (await db.execute(
        select(Model.id, Model.body, Model.status, User.handle)
        .join(User, User.id == Model.author_id)
        .where(Model.id.in_(ids))
    )).all()
    return {
        row_id: {
            "status": status,
            "author_handle": handle,
            "preview": body[:PREVIEW_CHARS],
            "truncated": len(body) > PREVIEW_CHARS,
        }
        for row_id, body, status, handle in rows
    }


async def _thread_context(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Context for a reported private thread: who is in it and how big it is.

    Deliberately no body preview. _resolve_thread declines to delete message
    bodies because the transcript is the evidence; listing that transcript in an
    admin queue is a *different* privacy decision, and resolving a report does
    not require it. An admin who genuinely needs the contents should have to go
    get them deliberately, not have them arrive in a table.
    """
    threads = (await db.execute(
        select(ResonanceThread)
        .options(selectinload(ResonanceThread.match))
        .where(ResonanceThread.id.in_(ids))
    )).scalars().all()
    if not threads:
        return {}

    counts = dict((await db.execute(
        select(ResonanceMessage.thread_id, func.count(ResonanceMessage.id))
        .where(ResonanceMessage.thread_id.in_(ids))
        .group_by(ResonanceMessage.thread_id)
    )).all())

    party_ids = {uid for t in threads for uid in (t.match.user_a, t.match.user_b)}
    handles = dict((await db.execute(
        select(User.id, User.handle).where(User.id.in_(party_ids))
    )).all())

    return {
        t.id: {
            "status": t.status,
            "author_handle": None,
            "preview": None,
            "truncated": False,
            "participants": sorted(
                h for h in (handles.get(t.match.user_a), handles.get(t.match.user_b)) if h
            ),
            "message_count": counts.get(t.id, 0),
        }
        for t in threads
    }


async def list_open_reports(db: AsyncSession, limit: int = 100) -> list[dict]:
    """Moderation queue: distinct targets with open reports, most-reported first.

    Each row carries enough context to actually adjudicate on — a body preview,
    the author, and the target's live status. Without those an admin is choosing
    remove-vs-dismiss against a bare UUID, and held content is filtered out of
    the public feed, so there is nowhere else to go look it up.

    ``target_exists`` is not cosmetic: resolve_target returns False for a target
    whose author already deleted it, which the router turns into a 404. Such a
    report can never be closed through the API, so the queue has to show it as
    unresolvable rather than offering a button that fails.
    """
    r = await db.execute(
        select(
            Report.target_type,
            Report.target_id,
            func.count(Report.id).label("n"),
            func.min(Report.created_at).label("first"),
            func.array_agg(func.distinct(Report.category)).label("cats"),
        )
        .where(Report.status == "open")
        .group_by(Report.target_type, Report.target_id)
        .order_by(func.count(Report.id).desc())
        .limit(limit)
    )
    rows = r.all()
    if not rows:
        return []

    # Batch the context lookups by target type — one query per type, not per row.
    by_type: dict[str, list[uuid.UUID]] = {}
    for target_type, target_id, *_ in rows:
        by_type.setdefault(target_type, []).append(target_id)

    context: dict[str, dict[uuid.UUID, dict]] = {}
    for target_type, ids in by_type.items():
        if target_type in PUBLIC_MODELS:
            context[target_type] = await _public_context(db, target_type, ids)
        elif target_type == "thread":
            context[target_type] = await _thread_context(db, ids)
        else:
            context[target_type] = {}

    out = []
    for target_type, target_id, n, first, cats in rows:
        ctx = context.get(target_type, {}).get(target_id)
        out.append({
            "target_type": target_type,
            "target_id": str(target_id),
            "report_count": n,
            "categories": sorted(cats or []),
            "first_reported_at": first.isoformat() if first else None,
            "target_exists": ctx is not None,
            **(ctx or {"status": None, "author_handle": None, "preview": None, "truncated": False}),
        })
    return out


async def resolve_target(
    db: AsyncSession,
    admin_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    action: str,
) -> bool:
    """Resolve a reported target. action='remove' takes it down; 'dismiss' clears
    the reports and restores a held item; 'clear' closes reports whose target is
    already gone. Returns False if the target is unknown.

    Handles threads as well as echoes and replies. The old version fell back to
    ``EchoReply`` for any unrecognised type, so a reported *thread* was looked up
    in the replies table, never found, and could never be closed out — every DM
    report sat open in the queue forever.

    A target that has since been deleted by its author is the other way into that
    same dead end; see _close_orphaned_reports.
    """
    if action == "clear":
        return await _clear_orphaned_reports(db, admin_id, target_type, target_id)

    if target_type == "thread":
        return await _resolve_thread(db, admin_id, target_id, action)

    Model = PUBLIC_MODELS.get(target_type)
    if Model is None:
        return False
    obj = (await db.execute(select(Model).where(Model.id == target_id))).scalar_one_or_none()
    if obj is None:
        return False

    await _close_open_reports(db, admin_id, target_type, target_id, action)
    obj.status = "removed" if action == "remove" else "active"
    await db.flush()
    return True


async def _target_exists(db: AsyncSession, target_type: str, target_id: uuid.UUID) -> bool:
    if target_type == "thread":
        Model = ResonanceThread
    else:
        Model = PUBLIC_MODELS.get(target_type)
        if Model is None:
            return False
    return (await db.execute(
        select(Model.id).where(Model.id == target_id)
    )).scalar_one_or_none() is not None


async def _clear_orphaned_reports(
    db: AsyncSession, admin_id: uuid.UUID, target_type: str, target_id: uuid.UUID
) -> bool:
    """Close reports whose target no longer exists ('clear').

    An author deleting their own content is usually the outcome the report was
    asking for, but it leaves the reports open with nothing left to act on — the
    same permanently-stuck queue the thread bug caused, reached by a different
    route. There is no object left to remove or restore, so this is a distinct
    action rather than an overload of remove/dismiss: 'remove' on a missing
    target still returns False, because silently succeeding there would hide a
    genuinely bad target id.

    Refuses to clear a target that still exists — that would close reports
    without anyone having looked at the content.
    """
    if await _target_exists(db, target_type, target_id):
        return False

    reports = (await db.execute(
        select(Report).where(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == "open",
        )
    )).scalars().all()
    if not reports:
        return False
    for rep in reports:
        rep.status = "resolved"
        rep.resolved_by = admin_id
        rep.resolution = "target_gone"
    await db.flush()
    return True


async def _close_open_reports(
    db: AsyncSession, admin_id: uuid.UUID, target_type: str, target_id: uuid.UUID, action: str
) -> None:
    new_status = "resolved" if action == "remove" else "dismissed"
    reports = (await db.execute(
        select(Report).where(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == "open",
        )
    )).scalars().all()
    for rep in reports:
        rep.status = new_status
        rep.resolved_by = admin_id
        rep.resolution = action


async def _resolve_thread(
    db: AsyncSession, admin_id: uuid.UUID, thread_id: uuid.UUID, action: str
) -> bool:
    """Resolve a reported private thread.

    'remove' shuts the conversation down and declines the match, so the pair is
    never suggested to each other again. 'dismiss' only clears the reports: a
    thread is never auto-held (submit_report deliberately doesn't throttle
    private surfaces), so there is nothing to restore.

    Message bodies are left alone. Deleting one side of a two-party transcript
    would destroy the evidence the report was filed about.
    """
    thread = (await db.execute(
        select(ResonanceThread)
        .options(selectinload(ResonanceThread.match))
        .where(ResonanceThread.id == thread_id)
    )).scalar_one_or_none()
    if thread is None:
        return False

    await _close_open_reports(db, admin_id, "thread", thread_id, action)

    if action == "remove":
        thread.status = "closed"
        thread.closed_by = admin_id
        thread.match.status = "declined"
        thread.match.declined_by = admin_id

    await db.flush()
    return True
