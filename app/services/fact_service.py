"""The daily book fact: one true, odd little thing about books, every morning.

Where a day's fact comes from, first match wins:
  1. A writer on the reader's own shelf was born, or died, on this day
     (Wikipedia "On this day").
     The in-app notice says it's on their shelf; the push doesn't (lock screens
     aren't private).
  2. Today's date in books, the same for everyone: a book event or book holiday
     ("1813: Pride and Prejudice is first published"), else a writer born or
     died today.
     Every date has one. Used every other day, so dated facts and the bank take
     turns.
  3. The hand-checked bank (fact_bank.FACTS), only facts marked ok, in order,
     never one a reader already had.
Nothing true to say today: nothing is sent.

Wikipedia's text goes out as written by its editors: nothing is generated, and
nothing is filtered for tone. The only check is topic: is it about books at
all, and about books or the people who write them. If Wikipedia can't be reached, the day falls back to the bank.

On by default, at the reader's fact_hour (10 am) in their timezone. A day more
than 12 hours late is skipped, never doubled up.
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.models.book_entry import BookEntry
from app.models.notification import TIER_DIGEST, DailyFact, NotificationPrefs
from app.models.user import User
from app.services.fact_bank import FACTS

logger = logging.getLogger("bibliome.fact")

LATE_LIMIT = timedelta(hours=12)
ONTHISDAY = "https://en.wikipedia.org/api/rest_v1/feed/onthisday/all/{m:02d}/{d:02d}"
RETRY_AFTER = timedelta(minutes=30)

WRITER = re.compile(r"\b(novelist|writer|poet|author|playwright|essayist)\b", re.I)
# Topic, not taste: a television or film writer isn't a book person.
NOT_BOOKS = re.compile(r"\b(television|screenwriter|songwriter|film)\b", re.I)
BOOK_EVENT = re.compile(
    r"\b(novels?|novella|poems?|poetry|books?|short stor(y|ies)|first edition|manuscripts?|tale|"
    r"trilogy|copies|literary|literature|bookshop|bookstore|library|"
    r"novelist|poet|author|playwright|essayist)\b", re.I)
BOOK_HOLIDAY = re.compile(r"\b(books?|literature|poetry|library|libraries|reading|writers?)\b", re.I)


@dataclass
class Hook:
    id: str
    kind: str          # "birth" | "event" | "holiday"
    text: str
    source: str
    name: str = ""     # births: whose birthday


def _page_url(item: dict) -> str | None:
    pages = item.get("pages") or []
    return (((pages[0].get("content_urls") or {}).get("desktop") or {}).get("page")) if pages else None


def parse_onthisday(data: dict) -> tuple[list[Hook], list[Hook]]:
    """Wikipedia's feed → (writer births, book events and holidays). Pure."""
    births = []      # writers born or died today (author-related, like the rest)
    for key, verb in (("births", "Born"), ("deaths", "Died")):
        for b in data.get(key) or []:
            pages = b.get("pages") or []
            desc = (pages[0].get("description") or "") if pages else ""
            url = _page_url(b)
            if not url or not WRITER.search(desc) or NOT_BOOKS.search(desc):
                continue
            name = (pages[0].get("titles") or {}).get("normalized") or b.get("text", "").split(",")[0]
            what = re.sub(r"\s*\([^)]*\)\s*$", "", desc).strip()
            births.append(Hook(id=f"wp:{key[:-1]}:{b.get('year')}:{name}", kind=key[:-1], name=name,
                               source=url, text=f"{verb} on this day in {b.get('year')}: {name}, {what}."))

    general, seen = [], set()
    for e in (data.get("selected") or []) + (data.get("events") or []):
        text, url = (e.get("text") or "").strip(), _page_url(e)
        # The linked pages' own descriptions ("1813 novel by Jane Austen") catch
        # the events whose sentence names a title but never says "book".
        about = " ".join([text, *((p.get("description") or "") for p in e.get("pages") or [])])
        if not url or not BOOK_EVENT.search(about) or text in seen or url in seen:
            continue
        seen.update((text, url))      # one fact per page: no near-duplicate events
        general.append(Hook(id=f"wp:event:{e.get('year')}:{text[:120]}", kind="event", source=url,
                            text=f"On this day in {e.get('year')}: {text}"))
    for h in data.get("holidays") or []:
        text, url = (h.get("text") or "").strip(), _page_url(h)
        if url and BOOK_HOLIDAY.search(text):
            general.append(Hook(id=f"wp:holiday:{text[:120]}", kind="holiday", source=url,
                                text=f"Today is {text}."))
    return births, general


_cache: dict[date, tuple[datetime, tuple[list[Hook], list[Hook]] | None]] = {}


async def hooks_for(day: date) -> tuple[list[Hook], list[Hook]]:
    """Today's Wikipedia hooks, fetched once per date per worker. Never raises."""
    now = datetime.now(timezone.utc)
    hit = _cache.get(day)
    if hit and (hit[1] is not None or now - hit[0] < RETRY_AFTER):
        return hit[1] or ([], [])
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={
            "User-Agent": f"Bibliome/1.0 ({get_settings().FRONTEND_URL})"}) as client:
            r = await client.get(ONTHISDAY.format(m=day.month, d=day.day))
            r.raise_for_status()
            parsed = parse_onthisday(r.json())
    except Exception as e:  # noqa: BLE001 — the bank covers the day
        logger.warning("On-this-day fetch failed for %s: %s", day, e)
        parsed = None
    for old in [d for d in _cache if d < day - timedelta(days=2)]:
        del _cache[old]
    _cache[day] = (now, parsed)
    return parsed or ([], [])


def _wrote(name: str, author: str) -> bool:
    """Is `name` (from Wikipedia) the author on this shelf entry? Token match, so
    'Austen, Jane' and 'Jane Austen' agree."""
    want = {t for t in re.findall(r"\w+", name.lower()) if len(t) > 1}
    have = set(re.findall(r"\w+", author.lower()))
    return bool(want) and want <= have


def choose(day: date, authors: list[str], history: list[dict],
           births: list[Hook], general: list[Hook]) -> dict | None:
    """Today's fact for one reader, or None. Pure.

    `history` is the reader's past facts, newest first: {"fact_id", "kind"}.
    """
    sent = {h["fact_id"] for h in history}
    for b in births:
        mine = [a for a in authors if a and _wrote(b.name, a)]
        if mine and b.id not in sent:
            return {"fact_id": b.id, "kind": b.kind, "text": b.text, "source": b.source,
                    "tone": "fun", "shelf_note": "They're on your shelf."}

    bank = [f for f in FACTS if f["ok"] and f["id"] not in sent]
    # Book events and holidays first; any writer born today otherwise. Every day
    # has some, so a dated fact is always there when it's that one's turn.
    fresh = [g for g in general if g.id not in sent] or [b for b in births if b.id not in sent]
    last = history[0]["kind"] if history else None
    if fresh and (last == "bank" or not bank):
        g = fresh[int(hashlib.sha256(day.isoformat().encode()).hexdigest(), 16) % len(fresh)]
        return {"fact_id": g.id, "kind": g.kind, "text": g.text, "source": g.source, "tone": "weird"}
    if bank:
        f = bank[0]
        return {"fact_id": f["id"], "kind": "bank", "text": f["text"], "source": f["source"], "tone": f["tone"]}
    return None


def due_at(prefs: NotificationPrefs | None, now: datetime) -> datetime:
    """The most recent moment today's fact was due: fact_hour, local time."""
    try:
        tz = ZoneInfo(prefs.timezone if prefs else "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local = now.astimezone(tz)
    due = local.replace(hour=prefs.fact_hour if prefs else 10, minute=0, second=0, microsecond=0)
    return due if due <= local else due - timedelta(days=1)


async def deliver_fact(user: User, prefs: NotificationPrefs | None, now: datetime, *, force: bool = False) -> bool:
    """Send this reader's fact for the day if it's due. True if sent.

    Claiming the day, choosing and notifying happen in one transaction: any
    failure keeps nothing, and the next tick tries again (within 12 hours).
    """
    from app.database import async_session
    from app.services.notification_service import notify

    due = now if force else due_at(prefs, now)
    if not force and now - due > LATE_LIMIT:
        return False
    day = due.date()
    async with async_session() as db:
        if (await db.execute(select(DailyFact.id).where(
                DailyFact.user_id == user.id, DailyFact.day == day))).scalar_one_or_none():
            return False
    births, general = await hooks_for(day)      # network, outside any transaction

    async with async_session() as db:
        async with db.begin():
            authors = (await db.execute(select(BookEntry.author).where(
                BookEntry.user_id == user.id, BookEntry.author.isnot(None)))).scalars().all()
            rows = (await db.execute(select(DailyFact.fact_id, DailyFact.payload).where(
                DailyFact.user_id == user.id).order_by(DailyFact.day.desc()))).all()
            history = [{"fact_id": fid, "kind": (p or {}).get("kind")} for fid, p in rows]
            fact = choose(day, list(authors), history, births, general)
            if fact is None:
                return False
            payload = {k: v for k, v in fact.items() if k != "fact_id"} | {"day": day.isoformat()}
            db.add(DailyFact(user_id=user.id, day=day, fact_id=fact["fact_id"], payload=payload))
            await db.flush()      # IntegrityError = another worker got here first
            n = await notify(db, user.id, TIER_DIGEST, "daily_fact", payload=payload)
    return n is not None


async def run_due_facts(now: datetime | None = None, *, force: bool = False) -> int:
    """Send every fact that is due. Returns how many went out."""
    from app.database import async_session

    now = now or datetime.now(timezone.utc)
    async with async_session() as db:
        rows = (await db.execute(
            select(User, NotificationPrefs).outerjoin(NotificationPrefs, NotificationPrefs.user_id == User.id)
        )).all()
    sent = 0
    for user, prefs in rows:
        if prefs is not None and not prefs.fact_enabled:
            continue
        try:
            if await deliver_fact(user, prefs, now, force=force):
                sent += 1
        except IntegrityError:
            continue
        except Exception:
            logger.exception("Daily fact failed for user %s; retrying next tick", user.id)
    return sent
