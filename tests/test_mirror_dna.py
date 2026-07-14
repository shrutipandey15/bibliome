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


CANON = {
    "grief", "desire", "rage", "dread", "comfort", "awe", "catharsis",
    "two_am", "chaos", "tenderness", "wit", "longing", "devastation",
}


async def test_stats_emotion_counts_ledger(client):
    """B5.3: the Stats ledger is populated with per-book canonical counts."""
    headers = await _auth(client)
    # 3 books: grief x3, awe x2, longing x1 (deduped per book).
    plan = [["grief"], ["grief", "awe"], ["grief", "awe", "longing"]]
    for i, emos in enumerate(plan):
        await client.post("/api/entries", json={
            "title": f"B{i}", "intensity": 6,
            "emotions": [{"emotion_id": e, "strength": 7} for e in emos],
        }, headers=headers)

    r = await client.get("/api/dna/stats", headers=headers)
    counts = r.json()["emotion_counts"]
    assert counts == {"grief": 3, "awe": 2, "longing": 1}
    assert set(counts).issubset(CANON)          # all keys canonical
    assert sum(counts.values()) == 6            # total tag-books


async def test_patterns_endpoint_bundles_stats_and_heatmap(client):
    headers = await _auth(client)
    await client.post("/api/entries", json={
        "title": "Solo", "intensity": 5, "emotions": [{"emotion_id": "awe", "strength": 6}],
    }, headers=headers)
    r = await client.get("/api/dna/patterns", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "stats" in body and "heatmap" in body
    assert body["stats"]["emotion_counts"]["awe"] == 1
