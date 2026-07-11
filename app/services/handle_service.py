"""Pseudonymous handle model (B3.1).

The handle is the only public identifier on an Echo. Changes are rate-limited and
the previous handle is kept for a grace window so links/mentions still resolve
("previously known as") before the name can be reused.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.social import HandleHistory
from app.models.user import User

HANDLE_RE = re.compile(r"^[a-zA-Z0-9_-]{3,50}$")
HANDLE_CHANGE_COOLDOWN_DAYS = 30
HANDLE_GRACE_WINDOW_DAYS = 30


class HandleError(ValueError):
    """Invalid or disallowed handle change (router maps to 400/409/429)."""


async def change_handle(db: AsyncSession, user: User, new_handle: str) -> None:
    new_handle = (new_handle or "").strip()
    if not HANDLE_RE.match(new_handle):
        raise HandleError("Handle must be 3–50 chars: letters, numbers, _ or -")
    if new_handle == user.handle:
        raise HandleError("That is already your handle")

    now = datetime.now(timezone.utc)
    if user.handle_changed_at is not None:
        last = user.handle_changed_at.replace(tzinfo=timezone.utc)
        if now - last < timedelta(days=HANDLE_CHANGE_COOLDOWN_DAYS):
            raise HandleError(
                f"Handles can only be changed every {HANDLE_CHANGE_COOLDOWN_DAYS} days",
            )

    # Uniqueness against current handles.
    taken = await db.execute(select(User.id).where(User.handle == new_handle))
    if taken.scalar_one_or_none() is not None:
        raise HandleError("That handle is taken")

    # Uniqueness against recently-released handles still in their grace window.
    cutoff = now - timedelta(days=HANDLE_GRACE_WINDOW_DAYS)
    recent = await db.execute(
        select(HandleHistory.user_id).where(
            HandleHistory.old_handle == new_handle,
            HandleHistory.changed_at >= cutoff,
            HandleHistory.user_id != user.id,
        )
    )
    if recent.scalar_one_or_none() is not None:
        raise HandleError("That handle was recently in use")

    db.add(HandleHistory(user_id=user.id, old_handle=user.handle))
    user.handle = new_handle
    user.handle_changed_at = now
    await db.flush()


async def resolve_handle(db: AsyncSession, handle: str) -> tuple[User | None, str | None]:
    """Resolve a handle to a user. Returns (user, canonical_handle).

    Falls back to the grace window: a recently-changed old handle still resolves
    to its owner, and the caller can redirect to the canonical handle.
    """
    result = await db.execute(select(User).where(User.handle == handle))
    user = result.scalar_one_or_none()
    if user is not None:
        return user, user.handle

    cutoff = datetime.now(timezone.utc) - timedelta(days=HANDLE_GRACE_WINDOW_DAYS)
    hist = await db.execute(
        select(HandleHistory)
        .where(HandleHistory.old_handle == handle, HandleHistory.changed_at >= cutoff)
        .order_by(HandleHistory.changed_at.desc())
        .limit(1)
    )
    h = hist.scalar_one_or_none()
    if h is None:
        return None, None
    owner = (await db.execute(select(User).where(User.id == h.user_id))).scalar_one_or_none()
    return owner, (owner.handle if owner else None)
