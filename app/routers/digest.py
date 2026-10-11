"""The weekly digest page.

- GET  /digest/{period}         the week's digest, nothing held back.
- POST /digest/{period}/click   the reader opened it, or tapped through a section.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.book_entry import BookEntry
from app.models.notification import DigestClick, NotificationDigest
from app.models.user import User

router = APIRouter(prefix="/digest", tags=["digest"])


@router.get("/{period}")
async def get_digest(
    period: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(select(NotificationDigest).where(
        NotificationDigest.user_id == current_user.id, NotificationDigest.period == period,
    ))).scalar_one_or_none()
    if row is None or not (row.payload or {}).get("sections"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No digest for that week")
    d = row.payload
    sections = []
    for s in d["sections"]:
        s = {k: v for k, v in s.items() if k != "tease"}
        # The past-self note is read live, never copied into the digest: if the
        # reader edited or deleted it since, the page says what's there now.
        if s["kind"] == "past_self":
            e = (await db.execute(select(BookEntry).where(
                BookEntry.id == uuid.UUID(s["data"]["entry_id"]), BookEntry.user_id == current_user.id,
            ))).scalar_one_or_none()
            s["reveal"] = {"note": e.notes, "finish_thought": e.finish_thought} if e else None
        sections.append(s)
    return {"period": row.period, "subject": row.subject, "intro": d.get("intro"),
            "sections": sections, "action": d.get("action"), "sent_at": row.sent_at}


class ClickIn(BaseModel):
    # "open" (the page itself), a section kind, or "action".
    section: str = Field(min_length=1, max_length=30)


@router.post("/{period}/click", status_code=status.HTTP_204_NO_CONTENT)
async def record_click(
    period: str,
    data: ClickIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Click-through, once per (digest, section): the digest's success metric."""
    digest_id = (await db.execute(select(NotificationDigest.id).where(
        NotificationDigest.user_id == current_user.id, NotificationDigest.period == period,
    ))).scalar_one_or_none()
    if digest_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No digest for that week")
    await db.execute(insert(DigestClick).values(
        id=uuid.uuid4(), digest_id=digest_id, section=data.section,
    ).on_conflict_do_nothing(constraint="uq_digest_click"))
