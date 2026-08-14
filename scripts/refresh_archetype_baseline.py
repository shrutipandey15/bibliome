"""Recompute BASELINE_VECTOR in app/services/dna_signals.py from real readers.

The archetype scorer measures each reader as deviation from the population mean.
The prior currently in the code is a stand-in; once there are enough readers with
enough books, replace it with the real thing and this stops being an estimate.

Run:  python -m scripts.refresh_archetype_baseline           # print only
      python -m scripts.refresh_archetype_baseline --write   # patch dna_signals.py

Two properties this deliberately preserves:

- One reader, one vote. The mean is taken over per-reader vectors, not over raw
  tag counts, so the heaviest logger does not set the baseline for everybody.
- One entry, one vote. Each entry's weight is split across its tags, matching
  ``frequency_vector``. If these two disagreed, the baseline would be centered on
  a vector no reader is ever scored against.

Unweighted (enduring) on purpose: the baseline is what readers are *like*, not
what they've been like lately, and it must not drift with the seasons.
"""

import argparse
import asyncio
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.book_entry import BookEntry
from app.services.dna_signals import _ALL_SLUGS
from app.utils.emotions import canonicalize

MIN_READERS = 30          # below this the mean is one person's taste, not a baseline
MIN_BOOKS_PER_READER = 5  # same gate the mirror itself uses
SMOOTHING = 0.002         # keeps never-tagged slugs off exactly zero

TARGET = Path("app/services/dna_signals.py")


async def collect() -> tuple[dict[str, float], int]:
    async with async_session() as db:
        rows = (await db.execute(
            select(BookEntry).options(selectinload(BookEntry.emotions))
        )).scalars().all()

    by_user: dict[str, list[list[str]]] = {}
    for entry in rows:
        tags = [c for c in (canonicalize(em.emotion_id) for em in entry.emotions) if c]
        if tags:
            by_user.setdefault(str(entry.user_id), []).append(sorted(set(tags)))

    vectors = []
    for entries in by_user.values():
        if len(entries) < MIN_BOOKS_PER_READER:
            continue
        counts: Counter = Counter()
        for tags in entries:
            for slug in tags:
                counts[slug] += 1.0 / len(tags)      # one entry, one vote
        total = sum(counts.values())
        vectors.append({s: counts.get(s, 0.0) / total for s in _ALL_SLUGS})

    if len(vectors) < MIN_READERS:
        return {}, len(vectors)

    mean = {s: sum(v[s] for v in vectors) / len(vectors) for s in _ALL_SLUGS}
    smoothed = {s: mean[s] + SMOOTHING for s in _ALL_SLUGS}
    total = sum(smoothed.values())
    return {s: round(smoothed[s] / total, 4) for s in _ALL_SLUGS}, len(vectors)


def render(vec: dict[str, float]) -> str:
    ordered = sorted(vec.items(), key=lambda kv: -kv[1])
    lines, row = [], []
    for slug, val in ordered:
        row.append(f'"{slug}": {val}')
        if len(row) == 4:
            lines.append("    " + ", ".join(row) + ",")
            row = []
    if row:
        lines.append("    " + ", ".join(row) + ",")
    return "BASELINE_VECTOR: dict[str, float] = {\n" + "\n".join(lines) + "\n}"


def write(block: str) -> None:
    src = TARGET.read_text()
    new = re.sub(
        r"BASELINE_VECTOR: dict\[str, float\] = \{.*?\n\}",
        block.replace("\\", "\\\\"),
        src,
        count=1,
        flags=re.S,
    )
    if new == src:
        raise SystemExit("BASELINE_VECTOR block not found — patch it by hand.")
    TARGET.write_text(new)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    vec, n = await collect()
    if not vec:
        print(f"Only {n} readers clear {MIN_BOOKS_PER_READER} tagged books "
              f"(need {MIN_READERS}). Keeping the existing prior — a stand-in "
              f"baseline beats one fitted to a handful of people.")
        return

    print(f"# baseline from {n} readers")
    print(render(vec))
    print("\nAfter writing, run: python -m scripts.dna_bias_probe")
    print("and: pytest tests/test_dna_archetype_calibration.py")
    if args.write:
        write(render(vec))
        print(f"\nWrote {TARGET}.")


if __name__ == "__main__":
    asyncio.run(main())
