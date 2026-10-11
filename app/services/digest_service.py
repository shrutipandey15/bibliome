"""The weekly digest: one letter a week about the reader, delivered in the app.

Two layers:
  - ``build_digest``: pure. Shelf + DNA + past digests in, a dict of sections out.
    No database, no clock of its own, so every rule is testable in isolation.
  - ``run_due_digests``: who is due (their day and hour, in their timezone),
    claim the week, notify (in-app + push).

The notification carries the subject and the lead's tease: the one thing held
back. The digest page (/digest/<period>) shows everything.

In-app, not email, on purpose: the free email plan is kept for password resets
and other mail that matters.

All text lives in ``digest_copy``. Feelings and archetypes come from
``app.utils.emotions`` and ``dna_engine``, never from a list kept here.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.notification import NotificationDigest, NotificationPrefs, TIER_DIGEST
from app.models.user import User
from app.services import digest_copy as copy
from app.services.dna_engine import DISCERNING_READER, PERSONALITY_TYPES
from app.utils.emotions import EMOTIONS, EMOTIONS_BY_SLUG, canonicalize

logger = logging.getLogger("bibliome.digest")

ARCHETYPES = [*PERSONALITY_TYPES, DISCERNING_READER]
EMOTION_SLUGS = [e["slug"] for e in EMOTIONS]
LATE_LIMIT = timedelta(hours=36)
# Near-anniversaries for "a line from your past self", in days, ± the slack.
ANNIVERSARIES = (30, 91, 182, 365)
ANNIVERSARY_SLACK = 7


# ── Inputs ────────────────────────────────────────────────────────────────────

@dataclass
class Book:
    id: str
    title: str
    status: str
    created_at: datetime
    author: str | None = None
    finished_at: date | None = None
    note: str | None = None          # notes, else the finish thought
    emotions: dict[str, int] = field(default_factory=dict)   # slug -> strength

    @property
    def opened(self) -> bool:
        return self.status != "want_to_read"

    @property
    def day(self) -> date:
        return self.finished_at or self.created_at.date()


def book_from_entry(e: BookEntry) -> Book:
    emotions: dict[str, int] = {}
    for em in e.emotions:
        slug = canonicalize(em.emotion_id)
        if slug:
            emotions[slug] = max(emotions.get(slug, 0), em.strength or 5)
    note = (e.notes or "").strip() or (e.finish_thought or "").strip() or None
    return Book(id=str(e.id), title=e.title, status=e.status, created_at=e.created_at,
                finished_at=e.finished_at, note=note, emotions=emotions, author=e.author)


# ── Small helpers ─────────────────────────────────────────────────────────────

class _Fields(dict):
    """str.format_map that leaves an unknown field visible instead of raising."""
    def __missing__(self, key):
        return "{" + key + "}"


def _fill(text: str, **fields) -> str:
    return text.format_map(_Fields(fields))


def _ename(slug: str) -> str:
    return EMOTIONS_BY_SLUG[slug]["name"]


def _num(n: int) -> str:
    return copy.NUMBER_WORDS[n] if 0 <= n < len(copy.NUMBER_WORDS) else str(n)


def _ago(days: int) -> str:
    """'six weeks', 'three months', 'a year' — never a raw day count."""
    if days < 60:
        weeks = max(1, round(days / 7))
        return "a week" if weeks == 1 else f"{_num(weeks)} weeks"
    if days < 330:
        months = round(days / 30.4)
        return f"{_num(months)} months"
    years = max(1, round(days / 365))
    return "a year" if years == 1 else f"{_num(years)} years"


def _pick(seed: str, items: list):
    """A stable choice per (reader, week), so a re-render shows the same thing."""
    return items[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(items)]


def question_for(period: str) -> dict:
    """The week's question, the same for every reader; walks the list in order."""
    y, w = (int(x) for x in period.split("-"))
    weeks = (date.fromisocalendar(y, w, 1) - date(2024, 1, 1)).days // 7
    return copy.QUESTIONS[weeks % len(copy.QUESTIONS)]


def previous_period(period: str) -> str:
    y, w = (int(x) for x in period.split("-"))
    return period_of(datetime.combine(date.fromisocalendar(y, w, 1) - timedelta(days=7), datetime.min.time()))


def emotion_of_week(week_monday: date) -> str:
    """Same for every reader in a week; walks the whole list before repeating."""
    weeks = (week_monday - date(2024, 1, 1)).days // 7
    return EMOTION_SLUGS[weeks % len(EMOTION_SLUGS)]


def _section(kind: str, body: str, link: str, tease: str | None = None, **data) -> dict:
    return {"kind": kind, "title": copy.TITLES[kind], "body": body,
            "tease": tease or body, "link": link, "data": data}


# ── The sections ──────────────────────────────────────────────────────────────
# Each returns a section dict, or None when it has nothing true to say.

def _past_self(books, today, history, period):
    recent = {h.get("past_entry_id") for h in history[:12]}
    noted = [b for b in books if b.note and b.id not in recent and (today - b.day).days >= 21]
    if not noted:
        return None

    def miss(b):  # distance from the nearest anniversary
        return min(abs((today - b.day).days - a) for a in ANNIVERSARIES)
    b = min(noted, key=lambda b: (miss(b), -(today - b.day).days))
    when = _ago((today - b.day).days)
    # Only the book is named; the note itself is read live by the digest page.
    tease = _fill(copy.PAST_SELF_TEASE, when=when.capitalize(), book=b.title)
    s = _section("past_self", tease, f"/digest/{period}#past_self", tease,
                 entry_id=b.id, book=b.title, when=when)
    s["anniversary"] = miss(b) <= ANNIVERSARY_SLACK
    return s


def _letter(archetype_id, name, books, top_emotion, history, period):
    sent = {h.get("letter_id") for h in history}
    recent = next((b.title for b in sorted(books, key=lambda b: b.created_at, reverse=True) if b.opened), None)
    for letter in copy.ARCHETYPE_LETTERS.get(archetype_id, []):
        if letter["id"] in sent or ("{book}" in letter["text"] and not recent):
            continue
        text = _fill(letter["text"], name=name, book=recent or "", emotion=top_emotion)
        first = text.split("\n\n")[1] if text.count("\n\n") >= 2 else text
        tease = first.split(". ")[0].rstrip(".") + "."
        return _section("archetype_letter", text, f"/?view=dna&letter={period}", tease,
                        letter_id=letter["id"], archetype=archetype_id)
    return None


def _weather(books, today, archetype, period):
    def tally(bs):
        t: dict[str, int] = {}
        for b in bs:
            for slug, strength in b.emotions.items():
                t[slug] = t.get(slug, 0) + strength
        return t
    counts = tally([b for b in books if (today - b.day).days <= 30])
    if len(counts) < 3:
        counts = tally(books)
    if len(counts) < 3 and archetype and archetype.get("primary_emotions"):
        counts = {s: 3 - i for i, s in enumerate(archetype["primary_emotions"])}
    if len(counts) < 3:
        return None
    top = sorted(counts, key=lambda s: -counts[s])[:3]
    # The runner-up's share, to the nearest ten, kept off the extremes.
    p = min(90, max(20, round(10 * counts[top[1]] / sum(counts.values())) * 10))
    tmpl = _pick(period, copy.WEATHER)
    text = _fill(tmpl["text"], e1=_ename(top[0]), e2=_ename(top[1]), e3=_ename(top[2]), p=p)
    tease = text.split(". ")[0].rstrip(".") + "."
    return _section("weather", text, f"/digest/{period}#weather", tease,
                    emotions=top, emotion=top[0], p=p, forecast=text)


def _news_section(n):
    if n["type"] == "spike":
        body = _fill(copy.NEWS_SPIKE, book=n["book"], x=n["x"])
    elif n["type"] == "published":
        d = date.fromisoformat(n["date"])
        body = _fill(copy.NEWS_PUBLISHED, book=n["book"], n=n["years"], date=f"{d.day} {d:%B %Y}")
    else:
        tmpl = copy.NEWS_AUTHOR_TURNS if n["alive"] else copy.NEWS_AUTHOR_BORN
        body = _fill(tmpl, book=n["book"], author=n["author"], n=n["years"])
    data = {k: v for k, v in n.items() if k not in ("id",)}
    return _section("while_away", body, f"/?entry={n['entry_id']}", copy.WHILE_AWAY_TEASE,
                    **data, news_id=n["id"])


def _while_away(books, today, news=(), history=()):
    """Outside news about the shelf first (never the same item twice in a year),
    then the reader's own anniversary: a year since they finished a book."""
    seen = {h.get("news_id") for h in history[:52]}
    for n in news:
        if n["id"] not in seen:
            return _news_section(n)
    for b in sorted(books, key=lambda b: b.day):
        if not b.finished_at:
            continue
        years = today.year - b.finished_at.year
        if years < 1:
            continue
        try:
            anniv = b.finished_at.replace(year=today.year)
        except ValueError:            # 29 February
            anniv = b.finished_at.replace(year=today.year, day=28)
        if abs((today - anniv).days) <= 3:
            when = "A year" if years == 1 else f"{_num(years).capitalize()} years"
            body = _fill(copy.ANNIVERSARY, when=when, book=b.title)
            return _section("while_away", body, f"/?entry={b.id}", copy.WHILE_AWAY_TEASE,
                            entry_id=b.id, book=b.title, years=years)
    return None


def _emotion_week(books, slug, period):
    essay = copy.EMOTION_ESSAYS[slug]
    holders = [b for b in books if slug in b.emotions]
    if holders:
        b = max(holders, key=lambda b: (b.emotions[slug], b.created_at))
        body = essay + "\n" + _fill(copy.EMOTION_CLOSEST, book=b.title)
        return _section("emotion_of_week", body, f"/digest/{period}#emotion_of_week",
                        essay + "\n" + copy.EMOTION_CLOSEST_TEASE,
                        emotion=slug, entry_id=b.id, book=b.title)
    if not books:   # nothing on the shelf to ask about yet
        return _section("emotion_of_week", essay, f"/digest/{period}#emotion_of_week", emotion=slug)
    return _section("emotion_of_week", essay + "\n" + copy.EMOTION_ASK,
                    f"/digest/{period}#emotion_of_week", emotion=slug)


def _drift(v2):
    profiles = (v2 or {}).get("profiles") or {}
    current, enduring = profiles.get("current") or {}, profiles.get("enduring") or {}
    if not current:
        return None
    deltas = {s: current.get(s, 0) - enduring.get(s, 0) for s in current if s in EMOTIONS_BY_SLUG}
    slug, delta = max(deltas.items(), key=lambda kv: kv[1], default=(None, 0))
    if slug and delta >= 0.015:
        degree = copy.DRIFT_DEGREES[0 if delta < 0.04 else 1 if delta < 0.08 else 2]
        body = _fill(copy.DRIFT_MOVED, degree=degree, emotion=_ename(slug))
        s = _section("drift", body, "/?view=dna", copy.DRIFT_TEASE, emotion=slug, degree=degree)
        s["notable"] = degree != copy.DRIFT_DEGREES[0]
        return s
    near = (v2.get("leaning") or {}).get("id")
    t = next((a for a in ARCHETYPES if a["id"] == near), None)
    if not t or not t["primary_emotions"]:
        return None
    body = _fill(copy.DRIFT_STILL, emotion=_ename(t["primary_emotions"][0]), archetype=t["name"])
    s = _section("drift", body, "/?view=dna", emotion=t["primary_emotions"][0], toward=near)
    s["notable"] = False
    return s


def _waiting(books, v2, zero_streak):
    tagged = [b for b in books if b.opened and b.emotions]
    needed = max(1, (v2 or {}).get("needed", 5) - len(tagged))
    progress = _fill(copy.WAITING_PROGRESS, n=_num(needed).capitalize(), s="" if needed == 1 else "s")
    shown = [copy.WAITING_HEADLINE, progress]
    extra = []      # held back for the app
    if not books:   # one more clue for every week the shelf stays empty
        extra.append(copy.WAITING_CLUES[min(zero_streak, len(copy.WAITING_CLUES) - 1)])
        shown.append(copy.WAITING_CLUE_TEASE)
    tally: dict[str, int] = {}
    for b in tagged:
        for slug in b.emotions:
            tally[slug] = tally.get(slug, 0) + 1
    if tally:
        top = max(tally, key=tally.get)
        k = sum(1 for a in ARCHETYPES if top in a["primary_emotions"])
        if k:
            extra.append(_fill(copy.WAITING_HINT, emotion=_ename(top), k=_num(k), total=_num(len(ARCHETYPES))))
    return _section("dna_waiting", "\n".join([*shown[:2], *extra]), "/?add=1", "\n".join(shown))


def _shelf_news(opened, outside, today):
    if not outside:
        return []
    from app.services.shelf_news import find_news, infos_from_json
    return find_news([{"entry_id": b.id, "title": b.title, "author": b.author} for b in opened],
                     infos_from_json(outside.get("infos")),
                     {t: tuple(v) for t, v in (outside.get("views") or {}).items()}, today)


def _question(period, last_count):
    """This week's question, plus last week's answers once three or more readers
    shared theirs (the answers themselves are only ever shown in the app)."""
    q = question_for(period)
    ask = _fill(copy.QUESTION_ASK, q=q["text"])
    data = {"question_id": q["id"], "question": q["text"], "question_period": period}
    body = ask
    if last_count >= 3:
        last = question_for(previous_period(period))
        body += "\n" + _fill(copy.QUESTION_RESULTS, n=_num(last_count), q=last["text"])
        data |= {"results_period": previous_period(period), "results_question": last["text"]}
    return _section("weekly_question", body, f"/digest/{period}#weekly_question", ask, **data)


# ── Assembly ──────────────────────────────────────────────────────────────────

PRIORITY_ACTIVE = ["past_self", "drift", "weather", "archetype_letter", "while_away", "emotion_of_week",
                   "weekly_question"]
PRIORITY_QUIET = ["while_away", "past_self", "drift", "archetype_letter", "weather", "emotion_of_week",
                  "weekly_question"]


def build_digest(*, name: str, books: list[Book], v2: dict | None, history: list[dict],
                 period: str, today: date, outside: dict | None = None,
                 question_answers: int | None = None) -> dict:
    """The week's digest for one reader, or {"skipped": True} on a rest week.

    ``history`` is this reader's past digest payloads, newest first.
    ``outside`` is shelf_news.gather()'s result (Wikidata + page views), or None.
    ``question_answers`` is how many readers shared an answer to last week's
    question; None leaves the weekly question out.
    """
    opened = [b for b in books if b.opened]
    enough = bool(v2 and v2.get("enough"))
    archetype_meta = (v2 or {}).get("archetype") if enough else None
    archetype = next((a for a in ARCHETYPES if archetype_meta and a["id"] == archetype_meta["id"]), None)
    quiet = not any((today - b.created_at.date()).days < 7 for b in books)

    zero_streak = 0
    for h in history:
        if not h.get("zero_books"):
            break
        zero_streak += 1
    zero = not books
    # After six weeks at zero, every other week: a reminder, never a nag.
    if zero and zero_streak >= 6 and zero_streak % 2 == 1:
        return {"skipped": True, "zero_books": True}

    current = ((v2 or {}).get("profiles") or {}).get("current") or {}
    top_emotion = _ename(max(current, key=current.get)) if current else None
    eow = emotion_of_week(today - timedelta(days=today.weekday()))

    available = {
        "past_self": _past_self(opened, today, history, period),
        "archetype_letter": _letter(archetype["id"] if archetype else "unwritten", name, books,
                                    top_emotion or _ename(eow), history, period),
        "weather": _weather(opened, today, archetype, period),
        "while_away": _while_away(opened, today, _shelf_news(opened, outside, today), history),
        "emotion_of_week": _emotion_week(opened, eow, period),
        "drift": _drift(v2) if enough else None,
        "weekly_question": _question(period, question_answers) if question_answers is not None else None,
    }
    available = {k: v for k, v in available.items() if v}
    last_lead = history[0].get("lead") if history else None

    if enough and archetype_meta and any(h.get("lead") == "dna_waiting" for h in history) \
            and not any(h.get("lead") == "revealed" for h in history):
        text = _fill(copy.REVEALED, archetype=archetype_meta["name"])
        lead = _section("revealed", text, "/?view=dna", copy.REVEALED_TEASE, archetype=archetype_meta["id"])
    elif not enough:
        lead = _waiting(books, v2, zero_streak)
    else:
        order = PRIORITY_QUIET if quiet else PRIORITY_ACTIVE
        # Only strong signals lead: an anniversary note, a real drift.
        ranked = [k for k in order if k in available
                  and not (k == "past_self" and not available[k]["anniversary"])
                  and not (k == "drift" and not available[k]["notable"])]
        ranked += [k for k in order if k in available and k not in ranked]
        # Real news from outside (a spike, a round anniversary) is worth leading with.
        if "while_away" in available and available["while_away"]["data"].get("news_id"):
            ranked.remove("while_away")
            ranked.insert(0, "while_away")
        pick = next((k for k in ranked if k != last_lead), ranked[0])
        lead = available.pop(pick)

    order = PRIORITY_QUIET if quiet else PRIORITY_ACTIVE
    rest = [available[k] for k in order if k in available and k != lead["kind"]]
    # Two weekly rituals ride along when they can: the feeling of the week (it
    # carries the easy action) and the weekly question. The lead is what varies.
    ritual = {"emotion_of_week": 0, "weekly_question": 1}
    rest.sort(key=lambda s: ritual.get(s["kind"], 2))
    smaller = rest[:2]

    kinds = {s["kind"] for s in smaller}
    if "emotion_of_week" in kinds and opened:
        e = next(s for s in smaller if s["kind"] == "emotion_of_week")["data"]["emotion"]
        action = {"label": _fill(copy.ACTIONS["tag_emotion"], emotion=_ename(e)),
                  "link": f"/digest/{period}#emotion_of_week", "section": "action"}
    elif lead["kind"] == "past_self":
        action = {"label": _fill(copy.ACTIONS["retag"], book=lead["data"]["book"]),
                  "link": f"/?entry={lead['data']['entry_id']}", "section": "action"}
    else:
        action = {"label": copy.ACTIONS["add_book"], "link": "/?add=1", "section": "action"}

    recent_subjects = {h.get("subject_id") for h in history[:8]}
    subjects = copy.SUBJECTS[lead["kind"]]
    subject = next((s for s in subjects if s["id"] not in recent_subjects), None)
    if subject is None:   # every one used inside 8 weeks: the least recent
        last_seen = {h.get("subject_id"): i for i, h in reversed(list(enumerate(history)))}
        subject = max(subjects, key=lambda s: last_seen.get(s["id"], 10**6))
    d = lead["data"]
    subject_text = _fill(subject["text"], name=name, book=d.get("book", ""),
                         when=d.get("when", ""), emotion=_ename(d["emotion"]) if d.get("emotion") else (top_emotion or ""))

    lead["button"] = copy.BUTTONS[lead["kind"]]
    letter = next((s for s in [lead, *smaller] if s["kind"] == "archetype_letter"), None)
    past = next((s for s in [lead, *smaller] if s["kind"] == "past_self"), None)
    return {
        "period": period,
        "lead": lead["kind"],
        "subject": subject_text,
        "subject_id": subject["id"],
        # A quiet week opens on a fallow line, whatever leads.
        "intro": _pick(period, copy.FALLOW_OPENERS) if quiet and opened else None,
        "sections": [lead, *smaller],
        "action": action,
        "letter_id": letter["data"]["letter_id"] if letter else None,
        "past_entry_id": past["data"]["entry_id"] if past else None,
        "news_id": next((s["data"].get("news_id") for s in [lead, *smaller] if s["kind"] == "while_away"), None),
        "zero_books": zero,
    }


# ── Scheduling ────────────────────────────────────────────────────────────────

def due_at(prefs: NotificationPrefs | None, now: datetime) -> datetime:
    """The most recent moment this reader's digest was due (their day and hour)."""
    try:
        tz = ZoneInfo(prefs.timezone if prefs else "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    day = prefs.digest_day if prefs else 6
    hour = prefs.digest_hour if prefs else 9
    local = now.astimezone(tz)
    due = (local - timedelta(days=(local.weekday() - day) % 7)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    if due > local:
        due -= timedelta(days=7)
    return due


def period_of(due: datetime) -> str:
    y, w, _ = due.isocalendar()
    return f"{y}-{w:02d}"


async def load_reader(db: AsyncSession, user: User) -> tuple[list[Book], dict | None, list[dict]]:
    """Shelf, DNA and past digests: everything build_digest reads."""
    from app.services.dna_service import cache_is_current, compute_and_cache

    entries = (await db.execute(
        select(BookEntry).options(selectinload(BookEntry.emotions)).where(BookEntry.user_id == user.id)
    )).scalars().all()
    v2 = user.cached_dna_v2 if await cache_is_current(db, user) else await compute_and_cache(db, user)
    rows = (await db.execute(
        select(NotificationDigest).where(NotificationDigest.user_id == user.id)
        .order_by(NotificationDigest.sent_at.desc())
    )).scalars().all()
    history = [{**(r.payload or {}), "lead": r.lead} for r in rows]
    return [book_from_entry(e) for e in entries], v2, history


async def build_for(db: AsyncSession, user: User, period: str, today: date, outside: dict | None = None) -> dict:
    from sqlalchemy import func
    from app.models.digest_extra import QuestionAnswer

    books, v2, history = await load_reader(db, user)
    shared = (await db.execute(select(func.count(QuestionAnswer.id)).where(
        QuestionAnswer.period == previous_period(period), QuestionAnswer.share.is_(True)))).scalar() or 0
    return build_digest(name=user.display_name or user.username, books=books, v2=v2,
                        history=history, period=period, today=today, outside=outside,
                        question_answers=shared)


async def outside_for(user_id, today: date) -> dict:
    """Outside data for this reader's recent opened books. Network; call it
    outside any transaction. Never raises."""
    from app.database import async_session
    from app.services.shelf_news import MAX_BOOKS, gather

    async with async_session() as db:
        rows = (await db.execute(
            select(BookEntry.id, BookEntry.title, BookEntry.author)
            .where(BookEntry.user_id == user_id, BookEntry.status != "want_to_read")
            .order_by(BookEntry.created_at.desc()).limit(MAX_BOOKS)
        )).all()
    return await gather([{"entry_id": str(i), "title": t, "author": a} for i, t, a in rows], today)


async def deliver_one(user: User, prefs: NotificationPrefs | None, now: datetime, *, force: bool = False) -> bool:
    """Build this reader's due digest and deliver it in-app (+ a push). True if delivered.

    Claiming the week, building and notifying happen in ONE transaction. If any
    of it fails, nothing is kept and the next tick (15 minutes later) tries again,
    until the digest would be more than 36 hours late. A failed push is retried
    separately by the push sweeper (notification_service.sweep_deferred_pushes).
    """
    from app.database import async_session
    from app.services.notification_service import notify

    due = now if force else due_at(prefs, now)
    if not force and now - due > LATE_LIMIT:
        return False
    period = period_of(due)
    async with async_session() as db:
        if (await db.execute(select(NotificationDigest.id).where(
                NotificationDigest.user_id == user.id, NotificationDigest.period == period))).scalar_one_or_none():
            return False
    outside = await outside_for(user.id, due.date())     # network, outside the transaction
    async with async_session() as db:
        async with db.begin():
            exists = (await db.execute(select(NotificationDigest.id).where(
                NotificationDigest.user_id == user.id, NotificationDigest.period == period))).scalar_one_or_none()
            if exists:
                return False
            user = await db.get(User, user.id)
            d = await build_for(db, user, period, due.date(), outside)
            db.add(NotificationDigest(user_id=user.id, period=period, payload=d,
                                      lead=None if d.get("skipped") else d["lead"], subject=d.get("subject")))
            await db.flush()      # IntegrityError here = another worker claimed it
            if d.get("skipped"):
                return False
            n = await notify(db, user.id, TIER_DIGEST, "weekly_digest", payload={
                "period": period, "subject": d["subject"], "tease": d["sections"][0]["tease"]})
    return n is not None


async def run_due_digests(now: datetime | None = None, *, force: bool = False) -> int:
    """Deliver every digest that is due. Returns how many went out."""
    from app.database import async_session

    now = now or datetime.now(timezone.utc)
    async with async_session() as db:
        rows = (await db.execute(
            select(User, NotificationPrefs).outerjoin(NotificationPrefs, NotificationPrefs.user_id == User.id)
        )).all()
    sent = 0
    for user, prefs in rows:
        if prefs is not None and not prefs.digest_enabled:
            continue
        try:
            if await deliver_one(user, prefs, now, force=force):
                sent += 1
        except IntegrityError:
            continue
        except Exception:
            logger.exception("Digest failed for user %s; retrying next tick", user.id)
    return sent


def missing_copy() -> list[str]:
    return copy.missing_copy(EMOTION_SLUGS, [a["id"] for a in ARCHETYPES])
