"""Public surface — reduced to the one legitimate case (Phase 5 B5.1).

The old username/entry-based public endpoints (stream, echoes, card, room, per-echo
images) are gone — they bypassed the visibility spine. Server-side OG/card image
generation is retired (no longer part of the product). What remains is the
share-token DNA card as JSON: a revocable, opt-in capability link the user creates
for themselves (visibility spine, B2.1). Echo is the one real public surface.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.rate_limit import RateLimiter
from app.services.dna_service import card_payload
from app.services.visibility import resolve_share_token

logger = logging.getLogger("bibliome.public")

router = APIRouter(prefix="/public", tags=["public"])

# Unauthenticated → rate-limited (audit P1-6). No longer heavy: the card is a
# cached-column read now, which is also why the Redis layer that used to sit in
# front of the live DNA compute is gone.
public_limiter = RateLimiter(max_requests=30, window_seconds=60, prefix="public")


@router.get("/shared/{token}")
async def get_shared_card(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """DNA profile via a revocable share token — the one way to share a profile."""
    await public_limiter.check(request)
    # Token validity is always resolved live (two indexed lookups) so revocation
    # and expiry are immediate.
    user = await resolve_share_token(db, token)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link invalid or expired")

    # Cache read only, and the same cache the owner's own DNA tab renders. This
    # endpoint used to recompute a *second*, older engine live, which meant a
    # reader's share link could name a different archetype than the app showed
    # them — and could name one at all for a 3-book reader the app told to keep
    # reading. One engine, every surface.
    card = card_payload(user)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="This reader's DNA isn't ready yet")

    return {"handle": user.handle, "share_token": token, **card}
