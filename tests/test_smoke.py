"""End-to-end API smoke test (B1.22): register → log books → get DNA profile.

Exercises the real request path through auth, entry creation (incl. the cover_url
validator and post-commit background recalc), and the fixed DNA engine.
"""

import pytest

pytestmark = pytest.mark.asyncio

REG = {"email": "reader@example.com", "username": "reader", "password": "hunter2pass"}

# amusement + awe + rage → the midnight_arsonist fingerprint (see test_dna_engine).
# Six books clears the 5-book DNA floor (B7.6).
BOOKS = [
    {"title": "Blood Meridian", "emotions": [{"emotion_id": "amusement"}, {"emotion_id": "rage"}]},
    {"title": "House of Leaves", "emotions": [{"emotion_id": "amusement"}, {"emotion_id": "awe"}]},
    {"title": "The Road", "emotions": [{"emotion_id": "awe"}, {"emotion_id": "rage"}]},
    {"title": "Blindsight", "emotions": [{"emotion_id": "amusement"}, {"emotion_id": "awe"}]},
    {"title": "Annihilation", "emotions": [{"emotion_id": "awe"}, {"emotion_id": "rage"}]},
    {"title": "The Fifth Season", "emotions": [{"emotion_id": "amusement"}, {"emotion_id": "rage"}]},
]


async def _auth_headers(client):
    await client.post("/api/auth/register", json=REG)
    tokens = (await client.post(
        "/api/auth/login", json={"email": REG["email"], "password": REG["password"]}
    )).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_register_log_books_get_profile(client):
    headers = await _auth_headers(client)

    for book in BOOKS:
        r = await client.post("/api/entries", json={"intensity": 8, **book}, headers=headers)
        assert r.status_code == 201, r.text

    # Books are listed back.
    r = await client.get("/api/entries", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 6

    # DNA profile computes the Phase-7 payload (demoted archetype + recency profiles).
    r = await client.get("/api/dna/profile", headers=headers)
    assert r.status_code == 200, r.text
    profile = r.json()
    assert profile["enough"] is True
    assert profile["book_count"] == 6
    assert profile["archetype"]["id"] == "midnight_arsonist"
    # Recency-weighted current profile keys are canonical.
    current = {k for k, v in profile["profiles"]["current"].items() if v > 0}
    assert current <= {"amusement", "awe", "rage"}


async def test_cover_url_ssrf_rejected_on_create(client):
    headers = await _auth_headers(client)
    r = await client.post(
        "/api/entries",
        json={"title": "Evil", "cover_url": "http://169.254.169.254/latest/meta-data/", "emotions": []},
        headers=headers,
    )
    assert r.status_code == 422  # blocked by the cover_url validator


async def test_unauthenticated_entries_rejected(client):
    r = await client.get("/api/entries")
    assert r.status_code in (401, 403)
