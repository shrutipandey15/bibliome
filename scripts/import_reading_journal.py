"""
Import a reading-journal .xlsx (Books + Echoes sheets) into Book DNA.

The workbook's vocabulary is the app's own vocabulary, so the mapping is direct:

    Books.emotions   -> entry_emotions.emotion_id   (the 18 canonical slugs)
    Books.doors      -> the emotion *families*; UI-only, not stored (verified only)
    Books.status     -> book_entries.status         (Finished -> finished, ...)
    Books.read_again -> book_entries.verdict        (Yes -> yes, Not sure -> not_sure)
    Books.*quote*    -> book_entries.quote          (hardest line first, extras after)
    Echoes.feelings  -> echoes.primary/secondary_emotion, via each emotion's `phrase`
    Echoes.visibility-> echoes.visibility           (Community -> community)

Everything is validated against app.utils.emotions before a single row is written;
any unknown term aborts the whole run.

Read-only by default. Pass --commit to write.

    python scripts/import_reading_journal.py --user-email you@example.com
    python scripts/import_reading_journal.py --user-email you@example.com --commit
"""

import argparse
import asyncio
import logging
import re
import sys
import zipfile
from collections import Counter
from datetime import date, datetime, time, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import async_session, engine
from app.models.book_entry import BookEntry, EntryEmotion
from app.models.user import User
from app.services.background import recalculate_dna
from app.services.book_search import bump_popularity
from app.services.echo_service import create_echo
from app.services.import_service import _dedupe_keys
from app.utils.emotions import EMOTIONS, VALID_SLUGS

# app.database builds its engine with echo=True under ENVIRONMENT=development,
# which buries this script's report under every statement. echo=True routes
# through SQLAlchemy's InstanceLogger, which ignores logger levels — so clearing
# the flag on the engine is the only thing that actually quiets it.
engine.echo = False
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# --- Vocabulary, derived from the app so the two can never drift ------------

FAMILIES = {e["family"] for e in EMOTIONS}
FAMILY_OF = {e["slug"]: e["family"] for e in EMOTIONS}
SLUG_BY_PHRASE = {e["phrase"]: e["slug"] for e in EMOTIONS}

STATUS_MAP = {"Want to Read": "want_to_read", "Reading": "reading", "Finished": "finished"}
VERDICT_MAP = {"Yes": "yes", "No": "no", "Not sure": "not_sure"}
VISIBILITY_MAP = {"Community": "community", "Public": "public"}

# The sheet records which emotions a book provoked, not how hard. Rather than
# invent a per-emotion number, every tag lands at the model's own default — the
# same stance app/services/import_service.py takes on CSV imports.
DEFAULT_STRENGTH = 5
DEFAULT_INTENSITY = 5

MAX_TITLE, MAX_AUTHOR, MAX_ECHO_BODY = 300, 200, 500


class Abort(Exception):
    """Validation failure — raised before anything is written."""


# --- Workbook parsing (stdlib only; openpyxl is not a project dependency) ---


def _read_sheet(z: zipfile.ZipFile, path: str, shared: list[str]) -> list[dict]:
    """Return a sheet's rows as dicts keyed by the header row's values."""
    rows = []
    for row in ET.fromstring(z.read(path)).iter(NS + "row"):
        cells = {}
        for c in row.iter(NS + "c"):
            col = "".join(ch for ch in c.get("r", "") if ch.isalpha())
            kind = c.get("t")
            if kind == "inlineStr":
                v = "".join(t.text or "" for t in c.iter(NS + "t"))
            elif kind == "s":
                node = c.find(NS + "v")
                v = shared[int(node.text)] if node is not None else ""
            else:
                node = c.find(NS + "v")
                v = node.text if node is not None else ""
            cells[col] = (v or "").strip()
        rows.append(cells)

    if not rows:
        return []
    header = rows[0]
    cols = sorted(header, key=lambda k: (len(k), k))
    return [{header[c]: r.get(c, "") for c in cols} for r in rows[1:]]


def parse_workbook(path: Path) -> tuple[list[dict], list[dict]]:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [
                "".join(t.text or "" for t in si.iter(NS + "t"))
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")
            ]

        rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        targets = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = {}
        for s in ET.fromstring(z.read("xl/workbook.xml")).iter(NS + "sheet"):
            t = (targets.get(s.get(rid)) or "").lstrip("/")
            sheets[s.get("name")] = t if t.startswith("xl/") else "xl/" + t

        for required in ("Books", "Echoes"):
            if required not in sheets:
                raise Abort(f"Workbook has no {required!r} sheet (found: {list(sheets)})")

        return _read_sheet(z, sheets["Books"], shared), _read_sheet(z, sheets["Echoes"], shared)


# --- Translation ------------------------------------------------------------


def _split(value: str, sep: str = ",") -> list[str]:
    return [p.strip() for p in (value or "").split(sep) if p.strip()]


def _parse_date(value: str, label: str, row_no: int) -> date | None:
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise Abort(f"Books row {row_no}: {label} is not YYYY-MM-DD: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise Abort(f"Books row {row_no}: {label} is not a real date: {value!r}") from e


def _build_quote(hardest: str, additional: str) -> str | None:
    """One quote column, so: the hardest line first, the rest beneath it."""
    parts = ([hardest] if hardest else []) + _split(additional, "|")
    return "\n\n".join(parts) or None


def translate(books: list[dict], echoes: list[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Validate everything and return (entries, echo_rows, warnings).

    Raises Abort on the first unrecoverable problem — nothing is written unless
    the whole workbook is clean.
    """
    book_cols = {
        "title", "author", "status", "started", "finished", "doors", "emotions",
        "read_again", "line_that_hit_hardest", "additional_quotes", "private_notes",
    }
    echo_cols = {"anchor_title", "echo_text", "feelings", "visibility"}
    if books and not book_cols.issubset(books[0]):
        raise Abort(f"Books sheet is missing columns: {sorted(book_cols - set(books[0]))}")
    if echoes and not echo_cols.issubset(echoes[0]):
        raise Abort(f"Echoes sheet is missing columns: {sorted(echo_cols - set(echoes[0]))}")

    warnings: list[str] = []
    entries: list[dict] = []
    seen_titles: dict[str, int] = {}

    for i, row in enumerate(books, start=2):  # row 1 is the header
        title = row["title"]
        if not title:
            raise Abort(f"Books row {i}: title is empty")
        if len(title) > MAX_TITLE:
            raise Abort(f"Books row {i}: title exceeds {MAX_TITLE} chars")
        if title in seen_titles:
            raise Abort(f"Books row {i}: duplicate title {title!r} (also row {seen_titles[title]})")
        seen_titles[title] = i

        author = row["author"] or None
        if author and len(author) > MAX_AUTHOR:
            raise Abort(f"Books row {i}: author exceeds {MAX_AUTHOR} chars")

        raw_status = row["status"]
        if raw_status and raw_status not in STATUS_MAP:
            raise Abort(f"Books row {i}: unknown status {raw_status!r} (expected {sorted(STATUS_MAP)})")
        status = STATUS_MAP.get(raw_status, "finished")

        raw_verdict = row["read_again"]
        if raw_verdict and raw_verdict not in VERDICT_MAP:
            raise Abort(f"Books row {i}: unknown read_again {raw_verdict!r}")
        verdict = VERDICT_MAP.get(raw_verdict)

        started = _parse_date(row["started"], "started", i)
        finished = _parse_date(row["finished"], "finished", i)
        if started and finished and finished < started:
            raise Abort(f"Books row {i}: finished {finished} precedes started {started}")
        if status == "finished" and not finished:
            raise Abort(f"Books row {i}: status is Finished but no finish date")
        if status != "finished" and finished:
            raise Abort(f"Books row {i}: status is {raw_status!r} but a finish date is set")

        slugs = _split(row["emotions"])
        unknown = [s for s in slugs if s not in VALID_SLUGS]
        if unknown:
            raise Abort(f"Books row {i}: emotions not in the app vocabulary: {unknown}")
        if not slugs:
            raise Abort(f"Books row {i}: no emotions — nothing to record")
        if len(set(slugs)) != len(slugs):
            raise Abort(f"Books row {i}: repeated emotion tag (entry_emotions is unique per pair)")

        # Doors are the emotion families — a UI grouping the app deliberately does
        # not store. Validate them, and flag any door with no emotion under it so
        # the discrepancy is visible rather than silently dropped.
        doors = _split(row["doors"])
        bad_doors = [d for d in doors if d not in FAMILIES]
        if bad_doors:
            raise Abort(f"Books row {i}: unknown doors {bad_doors} (expected {sorted(FAMILIES)})")
        covered = {FAMILY_OF[s] for s in slugs}
        for orphan in sorted(set(doors) - covered):
            warnings.append(f"row {i:>3} {title[:38]:<38} door {orphan!r} has no emotion tag under it")

        # created_at drives DNA recency weighting and orders the shelf, so anchor
        # it to when the book was actually read rather than to import time.
        anchor = finished or started
        created_at = (
            datetime.combine(anchor, time(12, 0), tzinfo=timezone.utc)
            if anchor else datetime.now(timezone.utc)
        )

        entries.append({
            "row": i,
            "title": title,
            "author": author,
            "status": status,
            "verdict": verdict,
            "quote": _build_quote(row["line_that_hit_hardest"], row["additional_quotes"]),
            "notes": row["private_notes"] or None,
            "started_at": started,
            "finished_at": finished,
            "created_at": created_at,
            "emotions": slugs,
        })

    # --- Echoes -------------------------------------------------------------
    by_title = {e["title"]: e for e in entries}
    echo_rows = []
    for i, row in enumerate(echoes, start=2):
        anchor_title, body = row["anchor_title"], row["echo_text"]
        if not anchor_title or not body:
            raise Abort(f"Echoes row {i}: anchor_title and echo_text are both required")
        if anchor_title not in by_title:
            raise Abort(f"Echoes row {i}: anchor_title {anchor_title!r} matches no book on the Books sheet")
        if len(body) > MAX_ECHO_BODY:
            raise Abort(f"Echoes row {i}: echo_text is {len(body)} chars, limit is {MAX_ECHO_BODY}")

        feelings = _split(row["feelings"])
        unknown = [f for f in feelings if f not in SLUG_BY_PHRASE]
        if unknown:
            raise Abort(f"Echoes row {i}: feelings not in the echo vocabulary: {unknown}")
        if len(feelings) > 2:
            raise Abort(f"Echoes row {i}: {len(feelings)} feelings — the app stores at most 2")
        slugs = [SLUG_BY_PHRASE[f] for f in feelings]

        raw_vis = row["visibility"]
        if raw_vis and raw_vis not in VISIBILITY_MAP:
            raise Abort(f"Echoes row {i}: unknown visibility {raw_vis!r}")

        anchor_entry = by_title[anchor_title]
        echo_rows.append({
            "row": i,
            "body": body,
            "book_title": anchor_title,
            "book_author": anchor_entry["author"],
            "primary_emotion": slugs[0] if slugs else None,
            "secondary_emotion": slugs[1] if len(slugs) > 1 else None,
            "visibility": VISIBILITY_MAP.get(raw_vis, "community"),
            "created_at": anchor_entry["created_at"],
        })

    return entries, echo_rows, warnings


# --- Reporting --------------------------------------------------------------


def report(entries: list[dict], echo_rows: list[dict], warnings: list[str], seen: set[str]) -> list[dict]:
    """Print the plan; return the entries that would actually be inserted."""
    fresh, dupes = [], []
    local: set[str] = set()
    for e in entries:
        keys = _dedupe_keys(e["title"], e["author"], None)
        (dupes if keys & (seen | local) else fresh).append(e)
        local |= keys

    print(f"  books parsed    : {len(entries)}")
    print(f"  new             : {len(fresh)}")
    print(f"  already on shelf: {len(dupes)}")
    for d in dupes[:10]:
        print(f"      skip  {d['title']}")
    if len(dupes) > 10:
        print(f"      ... and {len(dupes) - 10} more")

    by_status = Counter(e["status"] for e in fresh)
    print(f"  status          : {dict(by_status)}")
    print(f"  verdict         : {dict(Counter(e['verdict'] for e in fresh))}")
    print(f"  with a quote    : {sum(1 for e in fresh if e['quote'])}")
    print(f"  with notes      : {sum(1 for e in fresh if e['notes'])}")

    tags = Counter(s for e in fresh for s in e["emotions"])
    print(f"  emotion tags    : {sum(tags.values())} across {len(tags)}/{len(VALID_SLUGS)} slugs")
    for fam in sorted(FAMILIES):
        members = [(s, n) for s, n in tags.most_common() if FAMILY_OF[s] == fam]
        print(f"      {fam:<22} {', '.join(f'{s} {n}' for s, n in members)}")
    unused = sorted(VALID_SLUGS - set(tags))
    if unused:
        print(f"      never tagged: {', '.join(unused)}  (these become blind spots)")

    titles = {e["title"] for e in fresh}
    live_echoes = [e for e in echo_rows if e["book_title"] in titles]
    print(f"  echoes          : {len(live_echoes)} "
          f"({Counter(e['visibility'] for e in live_echoes) and dict(Counter(e['visibility'] for e in live_echoes))})")
    if len(live_echoes) != len(echo_rows):
        print(f"      {len(echo_rows) - len(live_echoes)} skipped (their anchor book is already on the shelf)")

    dates = [e["created_at"].date() for e in fresh]
    if dates:
        print(f"  created_at span : {min(dates)} -> {max(dates)}  (anchored to the read date)")

    if warnings:
        print(f"\n  {len(warnings)} doors with no emotion under them "
              f"(families are UI-only and are not stored — emotions win):")
        for w in warnings:
            print(f"      {w}")

    return fresh


# --- Import -----------------------------------------------------------------


async def run(path: Path, user_email: str, commit: bool) -> int:
    books, echoes = parse_workbook(path)
    print(f"Workbook: {path.name} — {len(books)} book rows, {len(echoes)} echo rows\n")

    entries, echo_rows, warnings = translate(books, echoes)
    print(f"Validation passed against the app's own vocabulary "
          f"({len(VALID_SLUGS)} emotions, {len(FAMILIES)} families).\n")

    async with async_session() as db:
        user = (
            await db.execute(select(User).where(User.email == user_email.lower().strip()))
        ).scalar_one_or_none()
        if user is None:
            raise Abort(f"No user with email {user_email!r}. Nothing was written.")
        print(f"Target user: {user.username} <{user.email}>  ({user.id})\n")

        existing = await db.execute(
            select(BookEntry.title, BookEntry.author, BookEntry.isbn)
            .where(BookEntry.user_id == user.id)
        )
        seen: set[str] = set()
        for title, author, isbn in existing.all():
            seen |= _dedupe_keys(title, author, isbn)

        print("Plan:")
        fresh = report(entries, echo_rows, warnings, seen)

        if not fresh:
            print("\nNothing to do.")
            return 0
        if not commit:
            print(f"\nDRY RUN — nothing written. Re-run with --commit to apply.")
            return 0

        print(f"\nWriting {len(fresh)} entries...")
        for e in fresh:
            entry = BookEntry(
                user_id=user.id,
                title=e["title"],
                author=e["author"],
                status=e["status"],
                verdict=e["verdict"],
                intensity=DEFAULT_INTENSITY,
                quote=e["quote"],
                notes=e["notes"],
                started_at=e["started_at"],
                # Set directly rather than via create_entry(), whose
                # _default_finished_at would stamp today onto books read years ago.
                finished_at=e["finished_at"],
                created_at=e["created_at"],
                updated_at=e["created_at"],
            )
            db.add(entry)
            await db.flush()
            for slug in e["emotions"]:
                db.add(EntryEmotion(entry_id=entry.id, emotion_id=slug, strength=DEFAULT_STRENGTH))
            # Same catalog feed the create-entry endpoint performs.
            await bump_popularity(db, e["title"], e["author"], None, None)

        imported_titles = {e["title"] for e in fresh}
        live_echoes = [r for r in echo_rows if r["book_title"] in imported_titles]
        print(f"Writing {len(live_echoes)} echoes...")
        held = []
        for r in live_echoes:
            echo, verdict, reason = await create_echo(
                db,
                author_id=user.id,
                body=r["body"],
                book_title=r["book_title"],
                book_author=r["book_author"],
                primary_emotion=r["primary_emotion"],
                secondary_emotion=r["secondary_emotion"],
                visibility=r["visibility"],
            )
            # Spread them across the reading history instead of dropping 20 items
            # into the feed at one timestamp.
            echo.created_at = r["created_at"]
            if echo.status != "active":
                held.append((r["book_title"], echo.status, reason))

        user.dna_dirty = True
        await db.commit()
        print("Committed.")
        if held:
            print(f"\n{len(held)} echo(s) held by the pre-publish classifier "
                  f"(created but NOT visible — review them in the app):")
            for title, st, reason in held:
                print(f"      {title}  [{st}] {reason}")
        user_id = user.id

    # Recomputes both DNA payloads, may capture a snapshot, and clears the
    # heatmap/stats caches — so the app reflects the import immediately.
    print("\nRecalculating DNA...")
    await recalculate_dna(user_id)

    async with async_session() as db:
        u = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        n = (await db.execute(
            select(BookEntry).where(BookEntry.user_id == user_id)
        )).scalars().all()
        print(f"  entries on shelf : {len(n)}")
        print(f"  personality type : {u.personality_type or '(none yet)'}")
        print(f"  dna_dirty        : {u.dna_dirty}")

    print("\nDone.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="reading_journal_import.xlsx", type=Path)
    ap.add_argument("--user-email", required=True, help="owner of the imported entries")
    ap.add_argument("--commit", action="store_true",
                    help="actually write (default is a read-only dry run)")
    args = ap.parse_args()

    if not args.file.exists():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 1
    try:
        return asyncio.run(run(args.file, args.user_email, args.commit))
    except Abort as e:
        print(f"\nABORTED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
