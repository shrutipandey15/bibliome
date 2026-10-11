"""The weekly question: saving a reader's answer, and showing everyone's.

Privacy, all enforced here:
  - An answer counts toward anything others see only if the reader ticked
    "share" when answering. Off by default.
  - Others never see who answered. Results carry no ids, names or handles.
  - A written note is shown only after Shruti approves it (status 'approved').
    Changing the note sends it back for review.
  - Nothing is shown until at least MIN_ANSWERS readers have shared.
"""

import uuid
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book_entry import BookEntry
from app.models.digest_extra import QuestionAnswer
from app.services.book_search import normalize

MIN_ANSWERS = 3
TOP_BOOKS = 5
MAX_NOTES = 8


async def save_answer(db: AsyncSession, user_id: uuid.UUID, period: str, question_id: str, *,
                      entry_id: uuid.UUID | None, book_title: str | None, book_author: str | None,
                      note: str | None, share: bool) -> QuestionAnswer:
    """Create or replace this reader's answer for the week. Raises ValueError when empty."""
    if entry_id is not None:
        e = (await db.execute(select(BookEntry).where(
            BookEntry.id == entry_id, BookEntry.user_id == user_id))).scalar_one_or_none()
        if e is None:
            raise ValueError("That book isn't on your shelf")
        book_title, book_author = e.title, e.author
    book_title = (book_title or "").strip()[:300] or None
    book_author = (book_author or "").strip()[:200] or None
    note = (note or "").strip()[:280] or None
    if not book_title and not note:
        raise ValueError("Pick a book or write a few words")

    a = (await db.execute(select(QuestionAnswer).where(
        QuestionAnswer.user_id == user_id, QuestionAnswer.period == period))).scalar_one_or_none()
    if a is None:
        a = QuestionAnswer(user_id=user_id, period=period, question_id=question_id)
        db.add(a)
    note_changed = a.note != note
    a.question_id, a.book_title, a.book_author, a.note, a.share = question_id, book_title, book_author, note, share
    if not (note and share):
        a.status, a.reviewed_at = "none", None
    elif note_changed or a.status == "none":
        a.status, a.reviewed_at = "pending", None      # a new or edited note is reviewed again
    await db.flush()
    return a


def mine(a: QuestionAnswer | None) -> dict | None:
    if a is None:
        return None
    return {"book_title": a.book_title, "book_author": a.book_author, "note": a.note,
            "share": a.share, "status": a.status}


async def results(db: AsyncSession, period: str) -> dict | None:
    """What readers who shared said. None until MIN_ANSWERS have shared."""
    rows = (await db.execute(select(QuestionAnswer).where(
        QuestionAnswer.period == period, QuestionAnswer.share.is_(True)))).scalars().all()
    if len(rows) < MIN_ANSWERS:
        return None
    titles: Counter = Counter()
    shown: dict[str, tuple[str, str | None]] = {}
    for r in rows:
        if r.book_title:
            k = normalize(r.book_title)
            titles[k] += 1
            shown.setdefault(k, (r.book_title, r.book_author))
    books = [{"title": shown[k][0], "author": shown[k][1], "count": n} for k, n in titles.most_common(TOP_BOOKS)]
    approved = sorted((r for r in rows if r.note and r.status == "approved"), key=lambda r: r.reviewed_at)
    notes = [{"note": r.note, "book_title": r.book_title} for r in approved[:MAX_NOTES]]
    return {"count": len(rows), "books": books, "notes": notes}


async def pending(db: AsyncSession) -> list[QuestionAnswer]:
    return list((await db.execute(select(QuestionAnswer).where(QuestionAnswer.status == "pending")
                                  .order_by(QuestionAnswer.created_at))).scalars().all())


async def review(db: AsyncSession, answer_id: uuid.UUID, approve: bool) -> bool:
    a = await db.get(QuestionAnswer, answer_id)
    if a is None or a.status != "pending":
        return False
    a.status = "approved" if approve else "rejected"
    a.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return True
