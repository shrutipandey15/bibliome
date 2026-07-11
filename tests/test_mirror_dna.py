"""DNA signature + Mirror surfaces (B2.5 / B2.6) — served from the fixed engine."""

import pytest

pytestmark = pytest.mark.asyncio

EMO = ["grief", "longing", "catharsis", "tenderness", "awe", "comfort"]


async def _auth(client):
    await client.post("/api/auth/register", json={
        "email": "m@example.com", "username": "mirroruser", "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": "m@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _seed(client, headers, n=6):
    for i in range(n):
        await client.post("/api/entries", json={
            "title": f"Book {i}",
            "author": f"Author {i}",
            "intensity": 6 + (i % 4),
            "emotions": [{"emotion_id": EMO[i % len(EMO)], "strength": 7}],
        }, headers=headers)


async def test_dna_and_mirror_surfaces_return(client):
    headers = await _auth(client)
    await _seed(client, headers)

    # DNA signature (fixed engine)
    r = await client.get("/api/dna/profile", headers=headers)
    assert r.status_code == 200
    prof = r.json()
    assert prof["book_count"] == 6
    assert set(prof["emotion_frequency"]).issubset({
        "grief", "desire", "rage", "dread", "comfort", "awe", "catharsis",
        "two_am", "chaos", "tenderness", "wit", "longing", "devastation",
    })

    for path in ("/api/dna/heatmap", "/api/dna/stats", "/api/dna/blind-spots",
                 "/api/dna/emotional-calendar"):
        r = await client.get(path, headers=headers)
        assert r.status_code == 200, f"{path}: {r.text}"

    # Mirror surfaces
    for path in ("/api/mirror/landscape", "/api/mirror/insight",
                 "/api/mirror/weekly-memory", "/api/mirror/right-now"):
        r = await client.get(path, headers=headers)
        assert r.status_code == 200, f"{path}: {r.text}"
