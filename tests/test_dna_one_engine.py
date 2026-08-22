"""P0-1: one engine, every surface (DB-backed).

The share card used to recompute a second, older engine live. On simulated readers
the two named a different archetype 42.7% of the time, and the legacy one gated at
3 books where the real one gates at 5 — so a reader could be told "not enough yet"
in-app while their share link confidently labelled them. These are the guards.
"""

import pytest

from app.services.dna_signals import HEDGE_ARCHETYPE_GAP

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _add_book(client, headers, title, emotions, intensity=8):
    return await client.post("/api/entries", json={
        "title": title, "intensity": intensity,
        "emotions": [{"emotion_id": e, "strength": 7} for e in emotions],
    }, headers=headers)


async def _share_token(client, headers):
    r = await client.post("/api/user/share-token", headers=headers)
    assert r.status_code == 200
    return r.json()["share_token"]


async def test_public_card_matches_in_app_archetype(client):
    """The 42.7% regression guard: both surfaces, one answer."""
    h = await _user(client, "onengine")
    # A shelf with a real winner, and enough of it that the label is not a coin flip.
    for i in range(8):
        await _add_book(client, h, f"Grief {i}", ["grief", "devastation", "catharsis"])
    for i in range(3):
        await _add_book(client, h, f"Soft {i}", ["comfort"])

    in_app = (await client.get("/api/dna/profile", headers=h)).json()
    assert in_app["enough"] is True and in_app["archetype"]

    token = await _share_token(client, h)
    card = (await client.get(f"/api/public/shared/{token}")).json()

    assert card["archetype"]["id"] == in_app["archetype"]["id"]
    assert card["archetype"]["name"] == in_app["archetype"]["name"]
    assert card["archetype_scores"] == in_app["archetype_scores"]
    assert card["margin"] == in_app["margin"]
    assert card["book_count"] == in_app["book_count"]
    # Same reader, same confidence. Dropping runner_up from the card let a public
    # surface assert the noun flatly for a reader the engine chose to hedge.
    assert card["runner_up"] == in_app["runner_up"]
    assert card["handle"] and card["share_token"] == token
    # The legacy shape is gone: `stats` was always {} because the old engine had
    # no such key, and `personality` is now `archetype`.
    assert "stats" not in card and "personality" not in card


async def test_public_card_hedges_when_the_in_app_mirror_hedges(client):
    """A hedged reader must be hedged on every surface that names them.

    The engine decides a close call is too close to assert; the card is a render
    of that decision, not a second opinion with more confidence than the first.
    """
    h = await _user(client, "hedged")
    # control_intellectual and midnight_arsonist held to a gap of ~0.0002, well
    # inside HEDGE_ARCHETYPE_GAP, so build_dna fills in a runner-up. Asserted
    # rather than skipped-if-absent: a guard that quietly opts out when its own
    # fixture drifts is not a guard.
    #
    # Re-picked when quiet_witness was re-anchored onto recognition: the old shelf
    # (grief_romantic vs control_intellectual) opened to a 0.023 gap and stopped
    # exercising the hedge at all. The failure message below is what caught it.
    for i in range(7):
        await _add_book(client, h, f"Control {i}", ["recognition", "dread", "awe"])
    for i in range(6):
        await _add_book(client, h, f"Arson {i}", ["amusement", "awe", "rage"])

    in_app = (await client.get("/api/dna/profile", headers=h)).json()
    assert in_app["runner_up"], (
        f"fixture no longer lands in the hedge band (margin {in_app['margin']}, "
        f"threshold {HEDGE_ARCHETYPE_GAP}) — re-pick the shelf, don't drop the test"
    )

    token = await _share_token(client, h)
    card = (await client.get(f"/api/public/shared/{token}")).json()
    assert card["runner_up"] == in_app["runner_up"]
    assert card["archetype"]["id"] == in_app["archetype"]["id"]


async def test_card_and_profile_signature_agree(client):
    """The profile's signature block is the same card, from the same cache."""
    h = await _user(client, "onesig")
    for i in range(6):
        await _add_book(client, h, f"B{i}", ["awe", "rage"])
    await client.patch("/api/user/settings", json={"profile_visibility": "public"}, headers=h)
    await client.get("/api/dna/profile", headers=h)

    token = await _share_token(client, h)
    card = (await client.get(f"/api/public/shared/{token}")).json()
    sig = (await client.get("/api/me/profile", headers=h)).json()["signature"]

    assert sig is not None
    assert sig["archetype"]["id"] == card["archetype"]["id"]
    assert sig["top_emotions"] == card["top_emotions"]


async def test_three_book_reader_is_not_enough_on_either_surface(client):
    """The legacy engine labelled at 3 books. The card must not outrun the app."""
    h = await _user(client, "onethree")
    for i in range(3):
        await _add_book(client, h, f"B{i}", ["grief"])

    in_app = (await client.get("/api/dna/profile", headers=h)).json()
    assert in_app["enough"] is False and "archetype" not in in_app

    token = await _share_token(client, h)
    r = await client.get(f"/api/public/shared/{token}")
    assert r.status_code == 404
    assert "isn't ready" in r.json()["detail"]


async def test_untagged_shelf_shares_nothing(client):
    """Books with no feelings logged produce no card on any surface."""
    h = await _user(client, "oneuntagged")
    for i in range(7):
        await _add_book(client, h, f"B{i}", [])

    in_app = (await client.get("/api/dna/profile", headers=h)).json()
    assert in_app["enough"] is False and in_app["tagged_count"] == 0

    token = await _share_token(client, h)
    assert (await client.get(f"/api/public/shared/{token}")).status_code == 404


async def test_generated_snapshot_matches_the_dna_tab(client):
    """A snapshot is a permanent record of what the reader was told. /dna/generate
    used to write the legacy engine's answer into it, so the evolution timeline
    could name an archetype the DNA tab never showed."""
    h = await _user(client, "gensnap")
    for i in range(8):
        await _add_book(client, h, f"B{i}", ["grief", "devastation", "catharsis"])

    in_app = (await client.get("/api/dna/profile", headers=h)).json()
    r = await client.post("/api/dna/generate", headers=h)
    assert r.status_code in (200, 201), r.text
    body = r.json()

    assert body["personality"]["id"] == in_app["archetype"]["id"]
    assert body["snapshot"]["personality_type"] == in_app["archetype"]["name"]
    assert body["snapshot"]["book_count"] == in_app["book_count"]
    data = body["snapshot"]["emotion_data"]
    assert data["archetype_id"] == in_app["archetype"]["id"]
    assert data["archetype_scores"] == in_app["archetype_scores"]
    # Same emotion_data shape the automatic path writes, so the timeline is
    # homogeneous no matter which path created a point on it.
    assert data["current_vector"] and data["enduring_vector"]

    evo = (await client.get("/api/dna/evolution", headers=h)).json()
    assert [p["archetype"] for p in evo] == [in_app["archetype"]["name"]]
    assert evo[0]["trigger"] == "manual"


async def test_generate_refuses_below_the_tagged_gate(client):
    """The legacy gate was 3 books. It now matches the DNA tab's 5 tagged."""
    h = await _user(client, "genlow")
    for i in range(4):
        await _add_book(client, h, f"B{i}", ["grief"])

    assert (await client.get("/api/dna/profile", headers=h)).json()["enough"] is False
    r = await client.post("/api/dna/generate", headers=h)
    assert r.status_code == 400
    assert "5 books with a feeling" in r.json()["detail"]
    # And nothing was persisted.
    assert (await client.get("/api/dna/evolution", headers=h)).json() == []


async def test_generate_refuses_an_untagged_shelf(client):
    """Ten books, no feelings logged: past the old book gate, still nothing to say."""
    h = await _user(client, "genuntagged")
    for i in range(10):
        await _add_book(client, h, f"B{i}", [])

    r = await client.post("/api/dna/generate", headers=h)
    assert r.status_code == 400
    assert (await client.get("/api/dna/evolution", headers=h)).json() == []


async def test_share_card_never_carries_journal_emotions(client):
    """The private mirror spans reading and life; the card is books only. A
    stranger must not read a private journal's emotions out of a share link."""
    from datetime import date

    h = await _user(client, "onejournal")
    for i in range(6):
        await _add_book(client, h, f"B{i}", ["comfort"])
    for i in range(6):
        await client.post("/api/journal", json={
            "entry_date": str(date(2026, 7, i + 1)),
            "ciphertext": "x" * 40, "nonce": "y" * 16,
            "emotions": ["rage"],
        }, headers=h)
    await client.get("/api/dna/profile", headers=h)

    token = await _share_token(client, h)
    card = (await client.get(f"/api/public/shared/{token}")).json()
    assert {e["emotion_id"] for e in card["top_emotions"]} == {"comfort"}
