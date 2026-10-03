"""Print the share card's daily counts (DNA card spec, "Goals and measures").

    python -m scripts.card_counts            # last 30 days
    python -m scripts.card_counts 90         # last 90

Aggregate totals only; there is nothing per-reader to print.
"""

import asyncio
import sys
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select

from app.database import async_session
from app.models.card import CardCount


async def main(days: int) -> None:
    since = date.today() - timedelta(days=days)
    async with async_session() as db:
        rows = (await db.execute(
            select(CardCount).where(CardCount.day >= since).order_by(CardCount.day)
        )).scalars().all()
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        totals[(r.metric, r.key)] += r.n
    print(f"Card counts since {since.isoformat()}:")
    for (metric, key), n in sorted(totals.items()):
        print(f"  {metric:8s} {key:10s} {n:>8d}")
    shares = sum(n for (m, _), n in totals.items() if m == "share")
    visits = totals.get(("visit", "human"), 0)
    previews = sum(n for (m, _), n in totals.items() if m == "preview")
    signups = totals.get(("signup", "card"), 0)
    print(f"\n  shares {shares} · page visits {visits} · preview fetches {previews} · sign-ups {signups}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 30))
