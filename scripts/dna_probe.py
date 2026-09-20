"""Why does ONE reader score the archetype they score? Read-only.

    python -m scripts.dna_probe reader@example.com

`dna_real_audit` answers "what does the engine say about everyone"; this answers
"why this label for this person", which is the question a reader actually asks
when their card names something they don't recognise.

Prints three things the card itself never shows:

  - the archetype ranking under the SHIPPED weighting (recency x intensity) and
    under plain all-time counts, side by side. When they disagree, the label is a
    statement about the last few months, not about the shelf.
  - which individual books carry the vector, as a share of it.
  - the effective sample size. A 70-book shelf whose weight is concentrated in
    three recent reads has an effective n near 3, and every "based on 70 books"
    reading of the label is wrong by a factor of twenty.

Writes nothing, and never touches the cache — it recomputes from the entries so
what it prints is the engine's answer, not a stored one.
"""

import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models.user import User
from app.services import dna_signals as sig
from app.services.dna_engine import PERSONALITY_TYPES
from app.services.dna_service import _load_journal_sigs, _load_raw


def _rank(label: str, vec: dict) -> str:
    aid, scores, gap = sig.score_archetype(vec)
    rows = sorted(scores.items(), key=lambda kv: -kv[1])
    out = [f"\n{label}", f"  winner: {aid}   lead over 2nd: {gap:+.4f}"]
    out += [f"    {k:<24} {v:+.4f}" for k, v in rows[:4]]
    return "\n".join(out)


async def probe(email: str) -> None:
    async with async_session() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            sys.exit(f"no user with email {email}")

        all_sigs = [sig.entry_sig(r) for r in await _load_raw(db, user.id)]
        opened = sig.opened_only(all_sigs)
        journal = await _load_journal_sigs(db, user.id)

    tagged = [s for s in opened if s.emotions]
    print(f"{user.username}  <{email}>")
    print(f"  shelf rows {len(all_sigs)}  |  opened {len(opened)}  |  tagged {len(tagged)}"
          f"  |  journal days {len(journal)}")
    print(f"  card currently says: {user.personality_type}")

    books = Counter()
    for s in opened:
        for e in set(s.emotions):
            books[e] += 1
    print("\nbooks per register, all-time:")
    for slug, n in books.most_common(8):
        print(f"  {slug:<14} {n:>4} / {len(opened)}")

    vec_sigs = opened + journal
    print(_rank("AS SHIPPED — recency x intensity (this is your card)",
                sig.frequency_vector(vec_sigs, weighted=True, intensity_weighted=True)))
    print(_rank("ALL-TIME — one book, one vote, no decay",
                sig.frequency_vector(vec_sigs, weighted=False)))

    # Who actually carries the shipped vector.
    now = datetime.now(timezone.utc)
    weighted = [s for s in vec_sigs if s.emotions]
    mean_int = sum(s.intensity for s in weighted) / len(weighted) if weighted else 1.0
    rows = sorted(
        ((sig.recency_weight((now - s.ts).days) * (s.intensity / mean_int),
          (now - s.ts).days, s.intensity, sorted(s.emotions)) for s in weighted),
        reverse=True,
    )
    total = sum(r[0] for r in rows) or 1.0
    print(f"\nwhat carries that vector (half-life {sig.HALF_LIFE_DAYS}d):")
    for w, age, i, em in rows[:6]:
        print(f"  {w / total * 100:5.1f}%  age {age:>5}d  int {i:>2}  {','.join(em)}")
    for k in (1, 3, 10):
        if k < len(rows):
            print(f"  top {k:>2} = {sum(r[0] for r in rows[:k]) / total * 100:5.1f}%")
    eff = total ** 2 / sum(r[0] ** 2 for r in rows)
    print(f"\n  median book age {sorted(r[1] for r in rows)[len(rows) // 2]}d")
    print(f"  EFFECTIVE SAMPLE SIZE: {eff:.1f} books, out of {len(rows)} tagged")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(probe(sys.argv[1]))
