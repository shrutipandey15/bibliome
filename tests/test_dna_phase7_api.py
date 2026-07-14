"""Phase 7 DNA — DB-backed endpoint + snapshot/notification tests."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _add_book(client, headers, title, emotions, intensity=6, status="finished"):
    return await client.post("/api/entries", json={
        "title": title, "intensity": intensity, "status": status,
        "emotions": [{"emotion_id": e, "strength": 7} for e in emotions],
    }, headers=headers)


# ── /dna/profile honesty ──

async def test_profile_not_enough_under_five_books(client):
    h = await _user(client, "dnalow")
    for i in range(3):
        await _add_book(client, h, f"B{i}", ["comfort"])
    r = await client.get("/api/dna/profile", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["enough"] is False
    assert body["book_count"] == 3 and body["needed"] == 5
    assert "archetype" not in body


async def test_profile_has_insights_and_locked_at_ten_books(client):
    h = await _user(client, "dnaten")
    for i in range(10):
        await _add_book(client, h, f"B{i}", ["comfort"])  # 12 emotions never tagged → blind spot
    r = await client.get("/api/dna/profile", headers=h)
    body = r.json()
    assert body["enough"] is True
    assert body["archetype"]["id"]                                    # demoted headline present
    assert any(i["category"] == "blind_spot" for i in body["insights"])
    assert all("n" in i for i in body["insights"])                   # every insight carries its n
    assert any(l["category"] == "seasonality" for l in body["locked"])  # honest curiosity gap


# ── stated preference (B7.1) ──

async def test_reads_for_persists_and_validates(client):
    h = await _user(client, "dnastated")
    r = await client.patch("/api/user/settings", json={"reads_for": ["comfort", "tenderness"]}, headers=h)
    assert r.status_code == 200
    assert r.json()["reads_for"] == ["comfort", "tenderness"]
    # Round-trips through GET.
    r = await client.get("/api/user/settings", headers=h)
    assert r.json()["reads_for"] == ["comfort", "tenderness"]


async def test_reads_for_rejects_non_canonical_slug(client):
    h = await _user(client, "dnabadslug")
    r = await client.patch("/api/user/settings", json={"reads_for": ["not_an_emotion"]}, headers=h)
    assert r.status_code == 400


async def test_contradiction_insight_appears_once_stated_pref_set(client):
    h = await _user(client, "dnacontra")
    # Shelf is all devastation, intensity high; they'll claim they read for comfort.
    for i in range(10):
        await _add_book(client, h, f"B{i}", ["devastation"], intensity=9)
    r = await client.get("/api/dna/profile", headers=h)
    assert not any(i["category"] == "contradiction" for i in r.json()["insights"])

    await client.patch("/api/user/settings", json={"reads_for": ["comfort"]}, headers=h)
    r = await client.get("/api/dna/profile", headers=h)
    assert any(i["category"] == "contradiction" for i in r.json()["insights"])


# ── evolution timeline + snapshot/shift (B7.4), service level ──

async def test_evolution_empty_before_any_snapshot(client):
    h = await _user(client, "dnaevo")
    r = await client.get("/api/dna/evolution", headers=h)
    assert r.status_code == 200 and r.json() == []


async def test_snapshot_on_drift_and_shift_notification(client, db):
    from app.models.user import User
    from app.models.book_entry import BookEntry, EntryEmotion
    from app.models.dna_snapshot import DNASnapshot
    from app.models.notification import Notification
    from app.services.dna_service import compute_and_cache, maybe_snapshot_and_notify

    await _user(client, "dnashift")
    user = (await db.execute(select(User).where(User.username == "dnashift"))).scalar_one()

    def add(emotions, days_ago, intensity=6):
        e = BookEntry(user_id=user.id, title=f"bk-{days_ago}-{emotions[0]}",
                      intensity=intensity, status="finished",
                      finished_at=date.today() - timedelta(days=days_ago))
        db.add(e)
        return e

    # 15 comfort books, ~a year old.
    comfort = [add(["comfort", "tenderness"], 330 + i * 10) for i in range(15)]
    await db.flush()
    for e in comfort:
        for slug in ("comfort", "tenderness"):
            db.add(EntryEmotion(entry_id=e.id, emotion_id=slug, strength=7))
    await db.commit()

    await compute_and_cache(db, user)
    await maybe_snapshot_and_notify(db, user)
    await db.commit()
    n_snaps = (await db.execute(select(func.count(DNASnapshot.id)).where(DNASnapshot.user_id == user.id))).scalar()
    assert n_snaps == 1  # first snapshot captured

    # Four recent devastating books → the profile moves.
    fresh = [add(["devastation", "grief"], i, intensity=9) for i in range(4)]
    await db.flush()
    for e in fresh:
        for slug in ("devastation", "grief"):
            db.add(EntryEmotion(entry_id=e.id, emotion_id=slug, strength=9))
    await db.commit()

    await compute_and_cache(db, user)
    snap = await maybe_snapshot_and_notify(db, user)
    await db.commit()

    assert snap is not None and snap.trigger == "drift"
    shift = (await db.execute(
        select(Notification).where(Notification.user_id == user.id, Notification.kind == "dna_shifted")
    )).scalars().all()
    assert len(shift) == 1
    assert shift[0].payload["new"] != shift[0].payload["old"]
