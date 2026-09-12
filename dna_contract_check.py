#!/usr/bin/env python3
"""Cross-repo DNA contract check (REVIEW.md Part 1 / Part 4).

Compares the backend's source-of-truth DNA constants against the frontend's
mirrored copies, so a slug/count drift (the class of bug that produced the
42.7% dual-engine mismatch) fails loudly instead of shipping silently.

Checks:
  - emotion slugs: app/utils/emotions.py VALID_SLUGS == frontend SEED slugs
  - archetype count: len(PERSONALITY_TYPES) == frontend ARCHETYPE_COUNT
  - opened-book statuses: dna_signals.OPENED_STATUSES == frontend OPENED_STATUSES
  - landing-page archetype preview (name/color/glyph) matches PERSONALITY_TYPES

Usage: python dna_contract_check.py [path/to/bookDNA-frontend]
Defaults to a sibling ../bookDNA-frontend directory.
"""

import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent


def backend_emotion_slugs() -> set[str]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.utils.emotions import VALID_SLUGS
    return set(VALID_SLUGS)


def backend_personality_types() -> list[dict]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.services.dna_engine import PERSONALITY_TYPES
    return PERSONALITY_TYPES


def backend_opened_statuses() -> set[str]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.services.dna_signals import OPENED_STATUSES
    return set(OPENED_STATUSES)


def frontend_emotion_slugs(frontend_root: Path) -> set[str]:
    text = (frontend_root / "src" / "services" / "emotions.js").read_text()
    # SEED rows look like: ["slug", "family", ...],
    return set(re.findall(r'\["(\w+)",\s*"[^"]+",\s*"[^"]+",', text))


def frontend_archetype_count(frontend_root: Path) -> int | None:
    text = (frontend_root / "src" / "components" / "dna" / "constants.js").read_text()
    m = re.search(r"ARCHETYPE_COUNT\s*=\s*(\d+)", text)
    return int(m.group(1)) if m else None


def frontend_opened_statuses(frontend_root: Path) -> set[str] | None:
    text = (frontend_root / "src" / "components" / "dna" / "constants.js").read_text()
    m = re.search(r"OPENED_STATUSES\s*=\s*\[([^\]]*)\]", text)
    if not m:
        return None
    return set(re.findall(r'"(\w+)"', m.group(1)))


def frontend_landing_preview(frontend_root: Path) -> list[dict]:
    """LandingPage.jsx hand-maintains a 3-archetype preview for logged-out
    visitors (deliberate exception — see the file's own comment)."""
    path = frontend_root / "src" / "pages" / "LandingPage.jsx"
    if not path.exists():
        return []
    text = path.read_text()
    entries = []
    for m in re.finditer(
        r'id:\s*"(?P<id>\w+)".*?name:\s*"(?P<name>[^"]+)".*?color:\s*"(?P<color>#\w+)".*?glyph:\s*"(?P<glyph>[^"]+)"',
        text, re.DOTALL,
    ):
        entries.append(m.groupdict())
    return entries


def main(frontend_arg: str | None = None) -> int:
    frontend_root = Path(frontend_arg) if frontend_arg else BACKEND_ROOT.parent / "bookDNA-frontend"
    if not frontend_root.exists():
        print(f"SKIP: frontend repo not found at {frontend_root}")
        return 0

    failures: list[str] = []

    be_emotions = backend_emotion_slugs()
    fe_emotions = frontend_emotion_slugs(frontend_root)
    if be_emotions != fe_emotions:
        failures.append(
            f"emotion slugs mismatch: backend-only={be_emotions - fe_emotions} "
            f"frontend-only={fe_emotions - be_emotions}"
        )

    be_types = backend_personality_types()
    fe_count = frontend_archetype_count(frontend_root)
    if fe_count is not None and fe_count != len(be_types):
        failures.append(f"ARCHETYPE_COUNT={fe_count} but backend defines {len(be_types)}")

    be_statuses = backend_opened_statuses()
    fe_statuses = frontend_opened_statuses(frontend_root)
    if fe_statuses is not None and be_statuses != fe_statuses:
        failures.append(
            f"OPENED_STATUSES mismatch: backend-only={be_statuses - fe_statuses} "
            f"frontend-only={fe_statuses - be_statuses}"
        )

    by_id = {t["id"]: t for t in be_types}
    for preview in frontend_landing_preview(frontend_root):
        be = by_id.get(preview["id"])
        if be is None:
            failures.append(f"landing preview references unknown archetype id={preview['id']}")
            continue
        if be["name"] != preview["name"]:
            failures.append(f"landing preview name drift for {preview['id']}: {preview['name']!r} != {be['name']!r}")
        if be["color"] != preview["color"]:
            failures.append(f"landing preview color drift for {preview['id']}: {preview['color']!r} != {be['color']!r}")
        if be["glyph"] != preview["glyph"]:
            failures.append(f"landing preview glyph drift for {preview['id']}: {preview['glyph']!r} != {be['glyph']!r}")

    if failures:
        print("DNA CONTRACT CHECK FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"DNA contract check passed: {len(be_emotions)} emotions, {len(be_types)} archetypes in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
