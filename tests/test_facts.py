"""Daily book fact: the bank's rules, Wikipedia parsing, the choice, and delivery."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import fact_bank
from app.services.fact_service import Hook, choose, due_at, parse_onthisday

DAY = date(2026, 12, 16)
TEN_AM = datetime(2026, 12, 16, 10, 5, tzinfo=timezone.utc)


# ── The bank ──────────────────────────────────────────────────────────────────

def test_every_fact_is_sourced_short_toned_and_unique():
    ids = [f["id"] for f in fact_bank.FACTS]
    assert len(ids) == len(set(ids))
    for f in fact_bank.FACTS:
        assert re.match(r"^https://\S+$", f["source"]), f["id"]
        assert len(f["text"]) <= 200, f["id"]
        assert f["tone"] in fact_bank.TONES, f["id"]
        assert isinstance(f["ok"], bool), f["id"]


def test_every_fact_in_the_bank_is_checked():
    # Each was read against its source on 2026-10-11; a new one starts unchecked.
    assert all(f["ok"] for f in fact_bank.FACTS)


def test_every_day_has_a_dated_fact_even_with_no_book_event(monkeypatch):
    from app.services import fact_service
    monkeypatch.setattr(fact_service, "FACTS", [])
    f = choose(DAY, [], [], BIRTHS, [])
    assert f["kind"] in ("birth", "death") and "shelf_note" not in f
    assert any(name in f["text"] for name in ("Jane Austen", "William Shakespeare"))


def test_unchecked_facts_never_go_out(monkeypatch):
    monkeypatch.setattr(fact_bank, "FACTS", [
        {"id": "a", "tone": "fun", "ok": False, "text": "draft", "source": "https://x"},
        {"id": "b", "tone": "fun", "ok": True, "text": "checked", "source": "https://x"},
    ])
    from app.services import fact_service
    monkeypatch.setattr(fact_service, "FACTS", fact_bank.FACTS)
    assert choose(DAY, [], [], [], [])["fact_id"] == "b"
    assert choose(DAY, [], [{"fact_id": "b", "kind": "bank"}], [], []) is None


# ── Wikipedia parsing ─────────────────────────────────────────────────────────

def _page(title, desc):
    url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    return {"titles": {"normalized": title}, "description": desc, "content_urls": {"desktop": {"page": url}}}


FEED = {
    "births": [
        {"year": 1775, "text": "Jane Austen, English novelist (died 1817)", "pages": [_page("Jane Austen", "English novelist (1775–1817)")]},
        {"year": 1943, "text": "Steven Bochco, American television writer", "pages": [_page("Steven Bochco", "American television writer and producer (1943–2018)")]},
        {"year": 1960, "text": "A Footballer", "pages": [_page("A Footballer", "English footballer")]},
    ],
    "deaths": [
        {"year": 1616, "text": "William Shakespeare, English playwright (born 1564)", "pages": [_page("William Shakespeare", "English playwright and poet (1564–1616)")]},
        {"year": 1999, "text": "A Senator", "pages": [_page("A Senator", "American politician")]},
    ],
    "selected": [{"year": 1901, "text": "Beatrix Potter privately published 250 copies of The Tale of Peter Rabbit.", "pages": [_page("Peter Rabbit", "")]}],
    "events": [
        {"year": 1813, "text": "Jane Austen's Pride and Prejudice is first published in the United Kingdom.", "pages": [_page("Pride and Prejudice", "1813 novel by Jane Austen")]},
        {"year": 2005, "text": "The first YouTube video is published.", "pages": [_page("YouTube", "")]},
        {"year": 1904, "text": "James Joyce began a relationship with Nora Barnacle.", "pages": [_page("James Joyce", "Irish novelist and poet (1882–1941)")]},
        {"year": 2021, "text": "A bookstore in Lyon is targeted by an attack.", "pages": [_page("Bookstore", "")]},
        {"year": 1813, "text": "Pride and Prejudice is published again, a second item.", "pages": [_page("Pride and Prejudice", "1813 novel by Jane Austen")]},
    ],
    "holidays": [{"text": "World Book Day", "pages": [_page("World Book Day", "")]}, {"text": "Some Saint's Day", "pages": [_page("Saint", "")]}],
}


def test_parse_keeps_book_people_and_book_events_only():
    births, general = parse_onthisday(FEED)
    assert [b.name for b in births] == ["Jane Austen", "William Shakespeare"]   # not TV, sport or politics
    assert births[1].text == "Died on this day in 1616: William Shakespeare, English playwright and poet."
    assert births[0].text == "Born on this day in 1775: Jane Austen, English novelist."
    texts = [g.text for g in general]
    assert any("Peter Rabbit" in t for t in texts) and any("Pride and Prejudice" in t for t in texts)
    assert not any("YouTube" in t for t in texts)                      # "published" alone isn't books
    assert any("attack" in t for t in texts)                           # authentic: no tone filter
    assert any("Nora Barnacle" in t for t in texts)                    # about an author counts
    assert sum("Pride and Prejudice" in t for t in texts) == 1         # one fact per page
    assert "Today is World Book Day." in texts and not any("Saint" in t for t in texts)


# ── Choosing ──────────────────────────────────────────────────────────────────

BIRTHS, GENERAL = parse_onthisday(FEED)


def test_an_author_on_your_shelf_wins_and_says_so():
    f = choose(DAY, ["Austen, Jane", "Someone Else"], [], BIRTHS, GENERAL)
    assert f["kind"] == "birth" and f["shelf_note"]
    # Not on the shelf: not this.
    assert choose(DAY, ["Someone Else"], [], BIRTHS, GENERAL)["kind"] != "birth"


def test_dated_facts_and_the_bank_take_turns(monkeypatch):
    from app.services import fact_service
    bank = [{"id": f"b{i}", "tone": "fun", "ok": True, "text": f"t{i}", "source": "https://x"} for i in range(3)]
    monkeypatch.setattr(fact_service, "FACTS", bank)
    history, kinds = [], []
    for _ in range(4):
        f = choose(DAY, [], history, [], GENERAL)
        kinds.append(f["kind"])
        history.insert(0, {"fact_id": f["fact_id"], "kind": f["kind"]})
    assert kinds[0] == "bank" and "bank" in kinds[2:] and any(k in ("event", "holiday") for k in kinds)
    assert len({h["fact_id"] for h in history}) == 4                   # never twice


def test_due_at_ten_local():
    assert due_at(None, TEN_AM) == datetime(2026, 12, 16, 10, 0, tzinfo=timezone.utc)
    assert due_at(None, TEN_AM - timedelta(minutes=10)).date() == date(2026, 12, 15)


# ── Delivery (Postgres) ───────────────────────────────────────────────────────

async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": f"fx_{name}", "password": "hunter2pass"})
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def world(monkeypatch):
    """Wikipedia stubbed with FEED, one checked bank fact, pushes captured."""
    from app.services import fact_service, notification_service

    async def hooks(day):
        return BIRTHS, GENERAL

    pushes = []

    async def fake_push(db, user_id, kind, payload):
        pushes.append((kind, payload))
        return 1

    monkeypatch.setattr(fact_service, "hooks_for", hooks)
    monkeypatch.setattr(fact_service, "FACTS", [{"id": "b1", "tone": "fun", "ok": True, "text": "A bank fact.", "source": "https://x"}])
    monkeypatch.setattr(notification_service, "push_to_user", fake_push)
    return pushes


async def test_on_by_default_at_ten_once_a_day_and_skipped_when_late(client, world):
    from app.services.fact_service import run_due_facts

    h = await _user(client, "aa")
    assert (await client.get("/api/notifications/preferences", headers=h)).json()["fact_enabled"] is True
    assert await run_due_facts(TEN_AM - timedelta(minutes=10)) == 0    # yesterday's 10am is >12h late
    assert await run_due_facts(TEN_AM) == 1
    assert await run_due_facts(TEN_AM + timedelta(hours=1)) == 0       # once a day
    assert [k for k, _ in world] == ["daily_fact"]

    today = (await client.get("/api/facts/today", headers=h)).json()
    assert today["text"] and today["source"].startswith("https://")


async def test_switched_off_means_no_fact(client, world):
    from app.services.fact_service import run_due_facts

    h = await _user(client, "off")
    await client.patch("/api/notifications/preferences", headers=h, json={"fact_enabled": False})
    assert await run_due_facts(TEN_AM) == 0
    assert (await client.get("/api/facts/today", headers=h)).status_code == 404


async def test_turning_off_facts_leaves_the_digest_on(client, world):
    from app.services.notification_service import get_or_create_prefs, notify
    from app.database import async_session

    h = await _user(client, "both")
    await client.patch("/api/notifications/preferences", headers=h, json={"fact_enabled": False})
    me = (await client.get("/api/auth/me", headers=h)).json()
    async with async_session() as db:
        async with db.begin():
            import uuid
            uid = uuid.UUID(me["id"])
            assert await notify(db, uid, 2, "daily_fact", {"text": "x"}) is None
            assert await notify(db, uid, 2, "weekly_digest", {"period": "x"}) is not None


async def test_shelf_author_birthday_is_personal_in_app_but_not_on_the_lock_screen(client, world):
    from app.services.fact_service import run_due_facts
    from app.services.push_service import _payload

    h = await _user(client, "sh")
    await client.post("/api/entries", headers=h, json={"title": "Emma", "author": "Jane Austen", "intensity": 6})
    await run_due_facts(TEN_AM)
    kind, payload = world[0]
    assert payload["kind"] == "birth" and payload["shelf_note"]
    push = _payload(kind, payload)
    assert push["body"].startswith("Born on this day in 1775") and "shelf" not in push["body"]
    assert push["title"] == "Today in books"


async def test_wikipedia_down_falls_back_to_the_bank(client, world, monkeypatch):
    from app.services import fact_service

    async def down(day):
        return [], []

    monkeypatch.setattr(fact_service, "hooks_for", down)
    h = await _user(client, "wd")
    assert await fact_service.run_due_facts(TEN_AM) == 1
    assert (await client.get("/api/facts/today", headers=h)).json()["kind"] == "bank"


async def test_hooks_fetch_failure_never_raises(monkeypatch):
    from app.services import fact_service

    class Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): raise RuntimeError("no network")
        async def __aexit__(self, *a): return False

    fact_service._cache.clear()
    monkeypatch.setattr(fact_service.httpx, "AsyncClient", Boom)
    assert await fact_service.hooks_for(DAY) == ([], [])
