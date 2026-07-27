"""Book identity + emotional aggregate tests (B8.1–B8.6)."""

import uuid

from app.services.aggregate_service import build_profile
from app.services.book_search import normalize

# asyncio_mode=auto (pytest.ini) collects the async tests here; this file mixes
# pure-unit and DB-backed tests, so no module-level asyncio mark.


async def _auth(client, email, username):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _entry(client, headers, **kw):
    payload = {"title": "A Little Life", "author": "Hanya Yanagihara", "status": "finished"}
    payload.update(kw)
    r = await client.post("/api/entries", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ── B8.1: identity ────────────────────────────────────────────────────────────

def test_normalize_folds_unicode_forms():
    """NFC and NFD spellings of the same name must produce one key (P8-1).

    This is the bug that fractured Wuthering Heights into two catalog rows: one
    API returned a precomposed "ë", another returned "e" + a combining diaeresis.
    """
    nfc = "Emily Brontë"        # ë as one codepoint
    nfd = "Emily Brontë"       # e + combining diaeresis
    assert nfc != nfd
    assert normalize(nfc) == normalize(nfd) == "emily bronte"
    assert normalize("Gabriel García Márquez") == "gabriel garcia marquez"


def test_normalize_still_collapses_case_and_whitespace():
    assert normalize("  A Little   LIFE ") == "a little life"


async def test_same_book_differently_cased_resolves_to_one_book_id(client):
    """The doc's acceptance test: differing case/spacing must collapse."""
    a = await _auth(client, "id1@example.com", "identity1")
    b = await _auth(client, "id2@example.com", "identity2")

    first = await _entry(client, a, title="A Little Life", author="Hanya Yanagihara")
    second = await _entry(client, b, title="a  little life", author="hanya yanagihara")

    assert first["book_id"] is not None
    assert first["book_id"] == second["book_id"]


async def test_same_title_different_author_stays_distinct(client):
    """"Powerless" is three real books. Identity must never merge on title alone."""
    headers = await _auth(client, "id3@example.com", "identity3")
    one = await _entry(client, headers, title="Powerless", author="Lauren Roberts")
    two = await _entry(client, headers, title="Powerless", author="Matthew Cody")
    assert one["book_id"] != two["book_id"]


# ── B8.2/B8.4: aggregation ────────────────────────────────────────────────────

def test_build_profile_counts_readers_not_entries():
    """One reader logging a re-read is one confirmation, not two."""
    reader = uuid.uuid4()
    rows = [
        (reader, "finished", "yes", "grief", 8),
        (reader, "reread", "yes", "grief", 6),
    ]
    profile = build_profile(rows)
    assert profile["reader_count"] == 1
    assert profile["emotion_profile"]["grief"]["count"] == 1
    # mean of that reader's own mean, not of the raw rows
    assert profile["emotion_profile"]["grief"]["mean_strength"] == 7.0
    assert profile["emotion_profile"]["grief"]["tagged_by_fraction"] == 1.0


def test_build_profile_fractions_and_dnf():
    r1, r2, r3, r4 = (uuid.uuid4() for _ in range(4))
    rows = [
        (r1, "finished", "yes", "devastation", 9),
        (r2, "finished", "yes", "devastation", 7),
        (r3, "finished", "no", "comfort", 5),
        (r4, "abandoned", "no", None, None),
    ]
    profile = build_profile(rows)
    assert profile["reader_count"] == 4
    assert profile["emotion_profile"]["devastation"]["count"] == 2
    assert profile["emotion_profile"]["devastation"]["tagged_by_fraction"] == 0.5
    assert profile["emotion_profile"]["devastation"]["mean_strength"] == 8.0
    assert profile["verdict_profile"]["yes"] == 0.5
    assert profile["dnf_rate"] == 0.25
    # An entry with no emotions still counts as a reader.
    assert "comfort" in profile["emotion_profile"]


def test_build_profile_canonicalizes_legacy_slugs():
    reader = uuid.uuid4()
    # "chaos" was retired to "confusion"; "made_up" has no target and is dropped.
    rows = [
        (reader, "finished", None, "chaos", 6),
        (reader, "finished", None, "made_up", 6),
    ]
    profile = build_profile(rows)
    assert "confusion" in profile["emotion_profile"]
    assert "chaos" not in profile["emotion_profile"]
    assert "made_up" not in profile["emotion_profile"]


def test_build_profile_empty():
    assert build_profile([])["reader_count"] == 0


async def test_aggregate_builds_and_is_visible_to_its_own_reader(client):
    headers = await _auth(client, "agg1@example.com", "aggregate1")
    entry = await _entry(
        client, headers, title="The Vegetarian", author="Han Kang",
        emotions=[{"emotion_id": "devastation", "strength": 9}],
    )
    r = await client.get(f"/api/books/{entry['book_id']}/profile", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Only one reader, but it's this reader's own book — always visible to them.
    assert body["reader_count"] == 1
    assert body["confidence"] == "emerging"
    assert body["emotion_profile"]["devastation"]["mean_strength"] == 9.0


# ── B8.6: privacy floor ───────────────────────────────────────────────────────

async def test_thin_aggregate_is_withheld_from_other_readers(client):
    """A one-reader "aggregate" is that reader's private tagging — never served."""
    owner = await _auth(client, "agg2@example.com", "aggregate2")
    stranger = await _auth(client, "agg3@example.com", "aggregate3")

    entry = await _entry(
        client, owner, title="Convenience Store Woman", author="Sayaka Murata",
        emotions=[{"emotion_id": "recognition", "strength": 8}],
    )
    r = await client.get(f"/api/books/{entry['book_id']}/profile", headers=stranger)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert "emotion_profile" not in body
    assert body["readers_needed"] >= 1


async def test_profile_404s_for_unknown_book(client):
    headers = await _auth(client, "agg4@example.com", "aggregate4")
    r = await client.get(f"/api/books/{uuid.uuid4()}/profile", headers=headers)
    assert r.status_code == 404


async def test_want_to_read_contributes_nothing(client):
    """A book nobody has engaged with has no emotional data to aggregate."""
    headers = await _auth(client, "agg5@example.com", "aggregate5")
    entry = await _entry(client, headers, title="Unread Thing", author="Nobody",
                         status="want_to_read")
    r = await client.get(f"/api/books/{entry['book_id']}/profile", headers=headers)
    assert r.status_code == 200
    assert r.json()["available"] is False



def test_thresholds_are_separate_questions():
    """Privacy and deviation-trust are different bars and must stay unlinked.

    3 readers is enough that a profile isn't one person's private tagging; it is
    nowhere near enough for a reader to be a small enough share of the population
    to deviate from meaningfully.
    """
    from app.config import get_settings
    s = get_settings()
    assert s.AGGREGATE_PUBLIC_MIN_READERS == 3
    assert s.DEVIATION_MIN_READERS == s.AGGREGATE_CONFIRMED_MIN_READERS
    assert s.DEVIATION_MIN_READERS > s.AGGREGATE_PUBLIC_MIN_READERS
