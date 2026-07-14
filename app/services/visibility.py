"""Visibility spine (B2.1 / blueprint §2.3).

One place that answers "can this viewer see this profile?" and manages share
tokens as revocable, optionally-expiring capability links — replacing the old
tangle of `is_public`, strict-public checks, and a single `share_token` column.
"""

import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.share_token import ShareToken
from app.models.user import User, VISIBILITY_VALUES
from app.services.auth_service import hash_token

# Access classes a viewer can have to a profile.
VIEWER_ANON = "anon"        # not signed in
VIEWER_MEMBER = "member"    # signed in, not the owner
VIEWER_OWNER = "owner"      # the profile owner


def is_valid_visibility(value: str) -> bool:
    return value in VISIBILITY_VALUES


def can_view_profile(user: User, viewer_class: str) -> bool:
    """Whether a viewer of the given class may see `user`'s profile at all.

    - private   → owner only
    - community → any signed-in member (and owner)
    - public    → anyone
    Share-token access is handled separately (a capability link bypasses this).
    """
    if viewer_class == VIEWER_OWNER:
        return True
    vis = user.profile_visibility
    if vis == "public":
        return True
    if vis == "community":
        return viewer_class == VIEWER_MEMBER
    return False  # private


async def create_share_token(
    db: AsyncSession, user_id, expires_at: datetime | None = None
) -> str:
    """Mint a new capability link. Returns the raw token (shown once)."""
    raw = secrets.token_urlsafe(24)
    db.add(ShareToken(user_id=user_id, token_hash=hash_token(raw), expires_at=expires_at))
    await db.flush()
    return raw


async def resolve_share_token(db: AsyncSession, raw_token: str) -> User | None:
    """Return the user a valid, unrevoked, unexpired share token points to."""
    result = await db.execute(
        select(ShareToken).where(ShareToken.token_hash == hash_token(raw_token))
    )
    st = result.scalar_one_or_none()
    if not st or st.revoked:
        return None
    if st.expires_at and st.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return None
    user_result = await db.execute(select(User).where(User.id == st.user_id))
    return user_result.scalar_one_or_none()


async def revoke_share_tokens(db: AsyncSession, user_id) -> int:
    """Revoke all of a user's active share tokens. Returns the count revoked."""
    result = await db.execute(
        select(ShareToken).where(ShareToken.user_id == user_id, ShareToken.revoked == False)
    )
    tokens = result.scalars().all()
    for st in tokens:
        st.revoked = True
    await db.flush()
    return len(tokens)
