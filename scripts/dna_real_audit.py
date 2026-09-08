"""What the archetype engine actually says about the REAL readers in the DB.

    python -m scripts.dna_real_audit           # summary
    python -m scripts.dna_real_audit --readers # one line per reader too

Read-only. Books only (matches the public card), recency-weighted `current`
vector, same `score_archetype` the mirror uses. No synthetic anything.
"""

import sys
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.book_entry import BookEntry
from app.services import dna_signals as sig
from app.services.dna_engine import PERSONALITY_TYPES


async def _load() -> dict:
    async with async_session() as db:
        rows = (await db.execute(
            select(BookEntry).options(selectinload(BookEntry.emotions))
        )).scalars().all()
    by_user = defaultdict(list)
    for e in rows:
        by_user[e.user_id].append(sig.entry_sig({
            "emotions": [em.emotion_id for em in e.emotions],
            "intensity": e.intensity,
            "created_at": e.created_at,
            "finished_at": e.finished_at,
            "status": e.status,
        }))
    return by_user


async def main() -> None:
    show_readers = "--readers" in sys.argv
    by_user = await _load()

    assigned = Counter()          # archetype -> n readers
    gaps = defaultdict(list)      # archetype -> [gap, ...]
    vecsum = defaultdict(Counter) # archetype -> summed emotion vector of its readers
    hedged = 0
    not_enough = abstained = 0

    for uid, sigs in by_user.items():
        books = sig.opened_only(sigs)
        tagged = [s for s in books if s.emotions]
        if len(tagged) < sig.MIN_BOOKS_FOR_DNA:
            not_enough += 1
            continue
        current = sig.frequency_vector(books, weighted=True, intensity_weighted=True)
        best, scores, gap = sig.score_archetype(current)
        if best is None:
            abstained += 1
            continue
        assigned[best] += 1
        gaps[best].append(gap)
        for slug, w in current.items():
            vecsum[best][slug] += w
        if gap < sig.HEDGE_ARCHETYPE_GAP:
            hedged += 1
        if show_readers:
            top = ", ".join(f"{s}:{w:.2f}" for s, w in
                            sorted(current.items(), key=lambda kv: -kv[1])[:4] if w > 0)
            hedge = "  (hedged)" if gap < sig.HEDGE_ARCHETYPE_GAP else ""
            print(f"  {str(uid)[:8]}  {len(tagged):3d}bk  {best:24} gap={gap:.4f}  [{top}]{hedge}")

    n = sum(assigned.values())
    print(f"\n{n} readers labelled   ({not_enough} under the 5-book gate, {abstained} abstained)")
    if not n:
        return
    print(f"{hedged}/{n} hedged (gap < {sig.HEDGE_ARCHETYPE_GAP})\n")
    print(f"  {'archetype':26} {'readers':>8} {'share':>7} {'avg gap':>9}   driven by (mean vector, top 4)")
    for aid, cnt in assigned.most_common():
        avg_gap = sum(gaps[aid]) / cnt
        mean = ", ".join(f"{s}:{w/cnt:.2f}" for s, w in vecsum[aid].most_common(4))
        print(f"  {aid:26} {cnt:>8} {cnt/n:>6.0%} {avg_gap:>9.4f}   {mean}")

    zero = [t["id"] for t in PERSONALITY_TYPES if not assigned[t["id"]]]
    if zero:
        print(f"\n  never assigned: {', '.join(zero)}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
