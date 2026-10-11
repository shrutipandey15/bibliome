"""What happened to a reader's shelf this week, from free outside data.

For "While you weren't looking" (digest section 4). Three kinds of news, all
about books the reader has logged:
  - spike: the book's Wikipedia article got far more views than usual this week
  - published: a round-number publication anniversary falls this week
  - author_born: the author's birthday falls this week

Sources, all free: Wikidata (what the book is, when it came out, who wrote it,
when they were born), Wikipedia search (to find books Wikidata's title search
misses), and Wikimedia page views. A book is matched only when Wikidata's author
matches the author on the shelf, so a common title never borrows a stranger's
news. Each (title, author) is looked up once and kept in book_wikidata.

Nothing here can fail a digest: any network trouble just means fewer news.
"""

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.models.digest_extra import BookWikidata
from app.services.book_search import normalize

logger = logging.getLogger("bibliome.shelf_news")

WD = "https://www.wikidata.org/w/api.php"
WP = "https://en.wikipedia.org/w/api.php"
PV = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
      "{title}/daily/{start}/{end}")

MAX_BOOKS = 30          # most recent opened books considered per reader
MAX_LOOKUPS = 8         # new Wikidata lookups per reader per run; the rest wait a week
RETRY_NOT_FOUND = timedelta(days=90)
REFRESH_FOUND = timedelta(days=365)
SPIKE_RATIO = 3.0       # this week's views vs the usual week
SPIKE_MIN_VIEWS = 1500  # and at least this many this week, so tiny pages don't "spike"
WINDOW_DAYS = 3         # "this week": within 3 days of the digest's day


@dataclass
class BookInfo:
    key: str
    found: bool
    qid: str | None = None
    wiki_title: str | None = None
    pub_date: date | None = None
    pub_precision: int | None = None
    author_name: str | None = None
    author_born: date | None = None
    author_died: date | None = None


def book_key(title: str, author: str | None) -> str:
    return f"{normalize(title)}|{normalize(author or '')}"[:520]


def parse_time(value: dict | None) -> tuple[date | None, int | None]:
    """A Wikidata time value → (date, precision). BCE and malformed → (None, None)."""
    if not value:
        return None, None
    m = re.match(r"^\+(\d{1,4})-(\d{2})-(\d{2})T", value.get("time") or "")
    if not m:
        return None, None
    y, mo, d = (int(x) for x in m.groups())
    precision = value.get("precision")
    try:
        return date(y, max(mo, 1), max(d, 1)), precision
    except ValueError:
        return None, None


def _claim_values(entity: dict, prop: str) -> list:
    out = []
    for c in (entity.get("claims") or {}).get(prop, []):
        dv = (c.get("mainsnak") or {}).get("datavalue")
        if dv:
            out.append(dv.get("value"))
    return out


def _same_name(a: str, b: str) -> bool:
    """Token match, so 'Austen, Jane' and 'Jane Austen' agree."""
    ta = {t for t in re.findall(r"\w+", normalize(a)) if len(t) > 1}
    tb = {t for t in re.findall(r"\w+", normalize(b)) if len(t) > 1}
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def pick_book(candidates: list[str], books: dict, authors: dict, title: str, author: str | None,
              key: str) -> BookInfo:
    """Choose the Wikidata item that IS this shelf book. Pure.

    It must name an author (P50) whose label matches the shelf's author, and its
    own label must match the title. Without an author on the shelf entry there's
    nothing to confirm against, so nothing is matched.
    """
    if not author:
        return BookInfo(key=key, found=False)
    matches = []
    for qid in candidates:
        e = books.get(qid) or {}
        label = ((e.get("labels") or {}).get("en") or {}).get("value", "")
        if not label or normalize(label) != normalize(title):
            continue
        for aid in [v.get("id") for v in _claim_values(e, "P50") if isinstance(v, dict)]:
            a = authors.get(aid) or {}
            aname = ((a.get("labels") or {}).get("en") or {}).get("value", "")
            if not _same_name(aname, author):
                continue
            pubs = [parse_time(v) for v in _claim_values(e, "P577")]
            pubs = [p for p in pubs if p[0]]
            # The earliest, most precise date is the first publication.
            pub, prec = min(pubs, key=lambda p: (p[0].year, -(p[1] or 0)), default=(None, None))
            born, _ = parse_time((_claim_values(a, "P569") or [None])[0])
            died, _ = parse_time((_claim_values(a, "P570") or [None])[0])
            wiki = ((e.get("sitelinks") or {}).get("enwiki") or {}).get("title")
            matches.append(BookInfo(key=key, found=True, qid=qid, wiki_title=wiki, pub_date=pub,
                                    pub_precision=prec, author_name=aname, author_born=born, author_died=died))
            break
    if matches:
        # Editions and adaptations share a title and author with the work itself.
        # The work is the one with a Wikipedia article and the earliest, most
        # precise first publication.
        return min(matches, key=lambda m: (m.wiki_title is None, -(m.pub_precision or 0),
                                           m.pub_date or date.max))
    return BookInfo(key=key, found=False)


def _anniv(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:      # 29 February
        return d.replace(year=year, day=28)


def _round(years: int) -> bool:
    return years in (5, 10, 20, 25, 30, 40) or (years >= 50 and years % 25 == 0)


def find_news(books: list[dict], infos: dict[str, BookInfo], views: dict[str, tuple[int, int]],
              today: date) -> list[dict]:
    """This week's news for a shelf, best first. Pure.

    `books` are the reader's opened books: {"entry_id", "title", "author"}.
    `views` maps a Wikipedia title to (views in the last 7 days, views in the 28 before).
    """
    spikes, published, born = [], [], []
    for b in books:
        info = infos.get(book_key(b["title"], b.get("author")))
        if not info or not info.found:
            continue
        base = {"entry_id": b["entry_id"], "book": b["title"]}
        if info.wiki_title and info.wiki_title in views:
            last7, prev28 = views[info.wiki_title]
            usual = prev28 / 4
            if last7 >= SPIKE_MIN_VIEWS and usual > 0 and last7 >= SPIKE_RATIO * usual:
                spikes.append({**base, "type": "spike", "x": int(last7 // usual), "id": f"spike:{info.qid}:{today:%G-%V}"})
        if info.pub_date and info.pub_precision == 11:
            years = today.year - info.pub_date.year
            if years > 0 and _round(years) and abs((_anniv(info.pub_date, today.year) - today).days) <= WINDOW_DAYS:
                published.append({**base, "type": "published", "years": years,
                                  "date": info.pub_date.isoformat(), "id": f"pub:{info.qid}:{today.year}"})
        if info.author_born:
            years = today.year - info.author_born.year
            if years > 0 and abs((_anniv(info.author_born, today.year) - today).days) <= WINDOW_DAYS:
                born.append({**base, "type": "author_born", "years": years, "author": info.author_name,
                             "alive": info.author_died is None, "id": f"born:{info.qid}:{today.year}"})
    # One item per author's birthday, however many of their books are shelved.
    seen, born_once = set(), []
    for n in born:
        if n["author"] not in seen:
            seen.add(n["author"])
            born_once.append(n)
    spikes.sort(key=lambda n: -n["x"])
    return spikes + published + born_once


# ── network ───────────────────────────────────────────────────────────────────

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0, headers={"User-Agent": f"Bibliome/1.0 ({get_settings().FRONTEND_URL})"})


async def _lookup(client: httpx.AsyncClient, title: str, author: str | None, key: str) -> BookInfo:
    if not author:
        return BookInfo(key=key, found=False)
    cands: list[str] = []
    r = await client.get(WD, params={"action": "wbsearchentities", "search": title, "language": "en",
                                     "type": "item", "limit": 10, "format": "json"})
    cands += [x["id"] for x in r.json().get("search", [])]
    r = await client.get(WP, params={"action": "query", "list": "search", "srsearch": f"{title} {author}",
                                     "srlimit": 3, "format": "json"})
    titles = [x["title"] for x in r.json().get("query", {}).get("search", [])]
    if titles:
        r = await client.get(WP, params={"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                         "titles": "|".join(titles), "format": "json"})
        for p in r.json().get("query", {}).get("pages", {}).values():
            qid = (p.get("pageprops") or {}).get("wikibase_item")
            if qid and qid not in cands:
                cands.append(qid)
    if not cands:
        return BookInfo(key=key, found=False)
    r = await client.get(WD, params={"action": "wbgetentities", "ids": "|".join(cands[:15]), "format": "json",
                                     "props": "labels|claims|sitelinks", "languages": "en", "sitefilter": "enwiki"})
    books = r.json().get("entities", {})
    aids = sorted({v.get("id") for e in books.values() for v in _claim_values(e, "P50") if isinstance(v, dict)})
    authors = {}
    for i in range(0, len(aids), 40):
        r = await client.get(WD, params={"action": "wbgetentities", "ids": "|".join(aids[i:i + 40]),
                                         "props": "labels|claims", "languages": "en", "format": "json"})
        authors.update(r.json().get("entities", {}))
    return pick_book(cands, books, authors, title, author, key)


async def _views(client: httpx.AsyncClient, wiki_title: str, today: date) -> tuple[int, int] | None:
    start, end = today - timedelta(days=35), today - timedelta(days=1)
    r = await client.get(PV.format(title=quote(wiki_title.replace(" ", "_"), safe=""),
                                   start=f"{start:%Y%m%d}", end=f"{end:%Y%m%d}"))
    if r.status_code != 200:
        return None
    daily = [(i["timestamp"][:8], i["views"]) for i in r.json().get("items", [])]
    cut = f"{today - timedelta(days=7):%Y%m%d}"
    return sum(v for t, v in daily if t >= cut), sum(v for t, v in daily if t < cut)


_view_cache: dict[tuple[str, date], tuple[int, int] | None] = {}


async def gather(books: list[dict], today: date) -> dict:
    """Look up what's needed for find_news. Never raises.

    Returns {"infos": {key: BookInfo-as-dict}, "views": {wiki_title: [last7, prev28]}},
    the JSON-able shape build_digest takes.
    """
    from app.database import async_session

    books = books[:MAX_BOOKS]
    keys = {book_key(b["title"], b.get("author")): b for b in books}
    infos: dict[str, BookInfo] = {}
    views: dict[str, tuple[int, int]] = {}
    try:
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            rows = (await db.execute(select(BookWikidata).where(BookWikidata.key.in_(list(keys))))).scalars().all()
        for r in rows:
            age = now - r.resolved_at
            if age < (REFRESH_FOUND if r.found else RETRY_NOT_FOUND):
                infos[r.key] = BookInfo(key=r.key, found=r.found, qid=r.qid, wiki_title=r.wiki_title,
                                        pub_date=r.pub_date, pub_precision=r.pub_precision,
                                        author_name=r.author_name, author_born=r.author_born,
                                        author_died=r.author_died)
        todo = [k for k in keys if k not in infos][:MAX_LOOKUPS]
        async with _client() as client:
            fresh = []
            for k in todo:
                try:
                    info = await _lookup(client, keys[k]["title"], keys[k].get("author"), k)
                except Exception as e:  # noqa: BLE001 — one book's trouble isn't the shelf's
                    logger.info("Wikidata lookup failed for %s: %s", k, e)
                    continue
                infos[k] = info
                fresh.append(info)
                await asyncio.sleep(0.1)    # be polite to a free API
            if fresh:
                async with async_session() as db:
                    async with db.begin():
                        for info in fresh:
                            row = asdict(info) | {"resolved_at": now}
                            await db.execute(insert(BookWikidata).values(**row).on_conflict_do_update(
                                index_elements=["key"], set_={k: v for k, v in row.items() if k != "key"}))
            for info in infos.values():
                t = info.wiki_title
                if not (info.found and t) or t in views:
                    continue
                hit = _view_cache.get((t, today))
                if hit is None and (t, today) not in _view_cache:
                    try:
                        hit = await _views(client, t, today)
                    except Exception as e:  # noqa: BLE001
                        logger.info("Pageviews failed for %s: %s", t, e)
                        hit = None
                    _view_cache[(t, today)] = hit
                if hit:
                    views[t] = hit
        for old in [k for k in _view_cache if k[1] < today - timedelta(days=2)]:
            del _view_cache[old]
    except Exception as e:  # noqa: BLE001 — outside data is a bonus, never a failure
        logger.warning("Shelf news unavailable: %s", e)
    return {"infos": {k: {**asdict(v), "pub_date": v.pub_date and v.pub_date.isoformat(),
                          "author_born": v.author_born and v.author_born.isoformat(),
                          "author_died": v.author_died and v.author_died.isoformat()} for k, v in infos.items()},
            "views": {t: list(v) for t, v in views.items()}}


def infos_from_json(raw: dict) -> dict[str, BookInfo]:
    out = {}
    for k, v in (raw or {}).items():
        v = dict(v)
        for f in ("pub_date", "author_born", "author_died"):
            v[f] = date.fromisoformat(v[f]) if v.get(f) else None
        out[k] = BookInfo(**v)
    return out
