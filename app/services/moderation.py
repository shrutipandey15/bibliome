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

from app.models.echo import Echo, EchoReply
from app.models.social import Report

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


async def _target_status_model(target_type: str):
    return Echo if target_type == "echo" else EchoReply


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
    Model = {"echo": Echo, "reply": EchoReply}.get(target_type)
    if Model is not None:
        pressure = await _weighted_open_pressure(db, target_type, target_id)
        if pressure >= REPORT_THROTTLE_THRESHOLD:
            obj = (await db.execute(select(Model).where(Model.id == target_id))).scalar_one_or_none()
            if obj is not None and obj.status == "active":
                obj.status = "held"
                await db.flush()

    return created


async def list_open_reports(db: AsyncSession, limit: int = 100) -> list[dict]:
    """Moderation queue: distinct targets with open reports, most-reported first."""
    r = await db.execute(
        select(
            Report.target_type,
            Report.target_id,
            func.count(Report.id).label("n"),
            func.min(Report.created_at).label("first"),
        )
        .where(Report.status == "open")
        .group_by(Report.target_type, Report.target_id)
        .order_by(func.count(Report.id).desc())
        .limit(limit)
    )
    rows = r.all()
    out = []
    for target_type, target_id, n, first in rows:
        cats = await db.execute(
            select(Report.category).where(
                Report.target_type == target_type,
                Report.target_id == target_id,
                Report.status == "open",
            )
        )
        out.append({
            "target_type": target_type,
            "target_id": str(target_id),
            "report_count": n,
            "categories": sorted(set(cats.scalars().all())),
            "first_reported_at": first.isoformat() if first else None,
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
    the reports and restores a held item. Returns False if the target is unknown."""
    Model = Echo if target_type == "echo" else EchoReply
    obj = (await db.execute(select(Model).where(Model.id == target_id))).scalar_one_or_none()
    if obj is None:
        return False

    new_report_status = "resolved" if action == "remove" else "dismissed"
    reports = (await db.execute(
        select(Report).where(
            Report.target_type == target_type,
            Report.target_id == target_id,
            Report.status == "open",
        )
    )).scalars().all()
    for rep in reports:
        rep.status = new_report_status
        rep.resolved_by = admin_id
        rep.resolution = action

    obj.status = "removed" if action == "remove" else "active"
    await db.flush()
    return True
