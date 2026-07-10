"""
Audit all emotion slugs currently in the database and suggest canonical mappings.

Usage:
    python scripts/audit_emotions.py
    python scripts/audit_emotions.py > audit_output.txt

No database writes are performed.
"""

import asyncio
import sys
from pathlib import Path

# Allow imports from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from app.database import async_session
from app.utils.emotions import VALID_SLUGS, EMOTIONS_13


# Hand-authored heuristic: maps common old slug patterns → best canonical guess
_HEURISTIC_MAP = {
    "obsession":  "desire",
    "healing":    "catharsis",
    "seen":       None,          # no clear canonical match
    "nostalgia":  "longing",
    "nothing":    None,          # no canonical match
    "2am":        "two_am",
    "melancholy": "grief",
    "loneliness": "longing",
    "joy":        "awe",
    "love":       "tenderness",
    "fear":       "dread",
    "anger":      "rage",
    "sadness":    "grief",
    "hope":       "catharsis",
}


def _suggest(slug: str) -> str:
    if slug in VALID_SLUGS:
        return slug
    # Exact heuristic
    if slug in _HEURISTIC_MAP:
        target = _HEURISTIC_MAP[slug]
        return target if target else "WARNING: no obvious match"
    # Partial-name match against canonical names
    for e in EMOTIONS_13:
        if slug in e["name"].lower() or e["slug"] in slug:
            return e["slug"]
    return "WARNING: no obvious match"


async def run_audit() -> None:
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                ee.emotion_id   AS slug,
                COUNT(ee.id)    AS usage_count
            FROM entry_emotions ee
            GROUP BY ee.emotion_id
            ORDER BY usage_count DESC
        """))
        rows = result.fetchall()

    if not rows:
        print("No emotions found in entry_emotions table.")
        return

    col_slug  = 20
    col_canon = 18
    col_count = 10
    col_note  = 30

    header = (
        f"{'SLUG':<{col_slug}}"
        f"{'CANONICAL TARGET':<{col_canon}}"
        f"{'USAGE':>{col_count}}"
        f"  {'NOTE':<{col_note}}"
    )
    sep = "-" * len(header)

    print(sep)
    print(header)
    print(sep)

    for row in rows:
        slug  = row.slug
        count = row.usage_count
        is_canonical = slug in VALID_SLUGS
        suggestion   = _suggest(slug)
        note = "" if is_canonical else ("already canonical" if suggestion == slug else suggestion)

        print(
            f"{slug:<{col_slug}}"
            f"{(slug if is_canonical else suggestion):<{col_canon}}"
            f"{count:>{col_count}}"
            f"  {note:<{col_note}}"
        )

    print(sep)
    print(f"\nTotal distinct slugs: {len(rows)}")

    non_canonical = [r for r in rows if r.slug not in VALID_SLUGS]
    if non_canonical:
        print(f"\nNon-canonical slugs ({len(non_canonical)}) — update LEGACY_EMOTION_MAP in app/utils/emotions.py:")
        for r in non_canonical:
            s = _suggest(r.slug)
            if "WARNING" in s:
                print(f"  # LEGACY_EMOTION_MAP[\"{r.slug}\"] = ???   <-- no obvious match, decide manually")
            else:
                print(f'  LEGACY_EMOTION_MAP["{r.slug}"] = "{s}",')
    else:
        print("\nAll slugs are already canonical. LEGACY_EMOTION_MAP can stay empty.")


if __name__ == "__main__":
    asyncio.run(run_audit())
