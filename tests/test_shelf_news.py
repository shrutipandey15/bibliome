"""Digest phase 2, section 4: outside news about the reader's shelf."""

from datetime import date

import pytest

from app.services.shelf_news import BookInfo, book_key, find_news, parse_time, pick_book

TODAY = date(2026, 1, 28)


def _label(v):
    return {"en": {"value": v}}


def _claim(prop, value):
    return {prop: [{"mainsnak": {"datavalue": {"value": value}}}]}


def _time(iso, precision=11):
    return {"time": f"+{iso}T00:00:00Z", "precision": precision}


AUTHORS = {"Q36322": {"labels": _label("Jane Austen"), "claims": _claim("P569", _time("1775-12-16"))
                      | _claim("P570", _time("1817-07-18"))}}
BOOKS = {
    # an edition: same title and author, no Wikipedia article, year only
    "Q900": {"labels": _label("Pride and Prejudice"), "claims": _claim("P50", {"id": "Q36322"})
             | _claim("P577", _time("1990-01-01", 9))},
    # the work itself
    "Q170583": {"labels": _label("Pride and Prejudice"), "sitelinks": {"enwiki": {"title": "Pride and Prejudice"}},
                "claims": _claim("P50", {"id": "Q36322"}) | _claim("P577", _time("1813-01-28"))},
    # same title, different author
    "Q1": {"labels": _label("Pride and Prejudice"), "claims": _claim("P50", {"id": "Q999"})},
}


def test_parse_time_keeps_precision_and_drops_bce():
    assert parse_time(_time("1813-01-28")) == (date(1813, 1, 28), 11)
    assert parse_time(_time("2023-00-00", 9)) == (date(2023, 1, 1), 9)
    assert parse_time({"time": "-0019-00-00T00:00:00Z", "precision": 9}) == (None, None)


def test_the_work_wins_over_its_editions_and_strangers():
    key = book_key("Pride and Prejudice", "Jane Austen")
    info = pick_book(["Q900", "Q1", "Q170583"], BOOKS, AUTHORS, "Pride and Prejudice", "Austen, Jane", key)
    assert info.qid == "Q170583" and info.wiki_title == "Pride and Prejudice"
    assert info.pub_date == date(1813, 1, 28) and info.author_died == date(1817, 7, 18)


def test_no_match_without_a_matching_author():
    key = book_key("Pride and Prejudice", "Someone Else")
    assert not pick_book(list(BOOKS), BOOKS, AUTHORS, "Pride and Prejudice", "Someone Else", key).found
    assert not pick_book(list(BOOKS), BOOKS, AUTHORS, "Pride and Prejudice", None, key).found


def _info(**kw):
    return BookInfo(key=book_key("Pride and Prejudice", "Jane Austen"), found=True, qid="Q170583",
                    wiki_title="Pride and Prejudice", author_name="Jane Austen", **kw)


SHELF = [{"entry_id": "e1", "title": "Pride and Prejudice", "author": "Jane Austen"}]


def test_round_publication_anniversary_this_week():
    infos = {SHELF[0]["title"] and book_key("Pride and Prejudice", "Jane Austen"):
             _info(pub_date=date(1813, 1, 28), pub_precision=11)}
    [n] = find_news(SHELF, infos, {}, date(2013, 1, 26))     # 200 years, 2 days out
    assert n["type"] == "published" and n["years"] == 200
    assert find_news(SHELF, infos, {}, date(2014, 1, 28)) == []          # 201: not round
    assert find_news(SHELF, infos, {}, date(2013, 3, 1)) == []           # not this week


def test_year_only_dates_never_claim_an_anniversary():
    infos = {book_key("Pride and Prejudice", "Jane Austen"): _info(pub_date=date(1813, 1, 1), pub_precision=9)}
    assert find_news(SHELF, infos, {}, date(2013, 1, 1)) == []


def test_author_birthday_once_per_author():
    infos = {book_key("Pride and Prejudice", "Jane Austen"): _info(author_born=date(1775, 12, 16),
                                                                  author_died=date(1817, 7, 18)),
             book_key("Emma", "Jane Austen"): _info(author_born=date(1775, 12, 16), author_died=date(1817, 7, 18))}
    shelf = SHELF + [{"entry_id": "e2", "title": "Emma", "author": "Jane Austen"}]
    news = find_news(shelf, infos, {}, date(2025, 12, 16))
    assert [n["type"] for n in news] == ["author_born"] and news[0]["alive"] is False and news[0]["years"] == 250


def test_a_spike_needs_real_numbers():
    infos = {book_key("Pride and Prejudice", "Jane Austen"): _info()}
    [n] = find_news(SHELF, infos, {"Pride and Prejudice": (40_000, 40_000)}, TODAY)   # 4x the usual week
    assert n["type"] == "spike" and n["x"] == 4
    assert find_news(SHELF, infos, {"Pride and Prejudice": (900, 100)}, TODAY) == []  # tiny page
    assert find_news(SHELF, infos, {"Pride and Prejudice": (12_000, 40_000)}, TODAY) == []  # usual


def test_digest_uses_news_and_never_repeats_an_item():
    from datetime import datetime, timezone
    from app.services.digest_service import Book, build_digest
    from dataclasses import asdict

    book = Book(id="e1", title="Pride and Prejudice", status="finished", author="Jane Austen",
                created_at=datetime(2012, 1, 1, tzinfo=timezone.utc), emotions={"joy": 6})
    info = _info(pub_date=date(1813, 1, 28), pub_precision=11)
    outside = {"infos": {info.key: {**asdict(info), "pub_date": "1813-01-28", "author_born": None, "author_died": None}},
               "views": {}}
    v2 = {"enough": True, "archetype": {"id": "grief_romantic", "name": "The Grief Romantic"},
          "profiles": {"current": {"joy": 1.0}, "enduring": {"joy": 1.0}}}
    d = build_digest(name="A", books=[book], v2=v2, history=[], period="2013-04", today=date(2013, 1, 27),
                     outside=outside)
    news = next(s for s in d["sections"] if s["kind"] == "while_away")
    assert "turns 200 this week" in news["body"] and d["news_id"]
    again = build_digest(name="A", books=[book], v2=v2, history=[{"news_id": d["news_id"], "lead": d["lead"]}],
                         period="2013-05", today=date(2013, 1, 28), outside=outside)
    assert all(s["data"].get("news_id") != d["news_id"] for s in again["sections"])


async def test_gather_never_raises_and_caches_lookups(db_ready, monkeypatch):
    from app.services import shelf_news

    calls = []

    async def lookup(client, title, author, key):
        calls.append(title)
        if title == "Broken":
            raise RuntimeError("Wikidata down")
        return BookInfo(key=key, found=True, qid="Q1", wiki_title=None, pub_date=date(1813, 1, 28),
                        pub_precision=11, author_name=author)

    monkeypatch.setattr(shelf_news, "_lookup", lookup)
    books = [{"entry_id": "e1", "title": "Pride and Prejudice", "author": "Jane Austen"},
             {"entry_id": "e2", "title": "Broken", "author": "X"}]
    out = await shelf_news.gather(books, TODAY)
    assert book_key("Pride and Prejudice", "Jane Austen") in out["infos"]
    await shelf_news.gather(books, TODAY)
    assert calls.count("Pride and Prejudice") == 1     # second time from the cache
    assert calls.count("Broken") == 2                  # failures aren't cached; tried again


async def test_preview_mode_never_calls_outside(db_ready, monkeypatch):
    """The admin preview has a request timeout to beat; it uses known data only."""
    from app.services import shelf_news

    async def boom(*a, **k):
        raise AssertionError("no network in preview mode")

    monkeypatch.setattr(shelf_news, "_lookup", boom)
    monkeypatch.setattr(shelf_news, "_views", boom)
    out = await shelf_news.gather([{"entry_id": "e1", "title": "Circe", "author": "Madeline Miller"}],
                                  TODAY, network=False)
    assert out == {"infos": {}, "views": {}}


async def test_admin_preview_builds_without_lookups(client, db, monkeypatch):
    from sqlalchemy import update
    from app.models.user import User
    from app.services import shelf_news

    calls = []

    async def lookup(*a, **k):
        calls.append(a)
        return shelf_news.BookInfo(key="x", found=False)

    monkeypatch.setattr(shelf_news, "_lookup", lookup)
    await client.post("/api/auth/register", json={"email": "pv@example.com", "username": "sn_pv", "password": "hunter2pass"})
    await db.execute(update(User).where(User.username == "sn_pv").values(is_admin=True))
    await db.commit()
    tok = (await client.post("/api/auth/login", json={"email": "pv@example.com", "password": "hunter2pass"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    await client.post("/api/entries", headers=h, json={"title": "Circe", "author": "Madeline Miller", "intensity": 7})
    r = await client.get("/api/admin/digest/preview", headers=h)
    assert r.status_code == 200 and calls == []
