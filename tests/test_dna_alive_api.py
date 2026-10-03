"""The aliveness layer through the API: the echo after a save, the shift card
shown once, the launch guard and the import exemption for "your DNA shifted",
and the recap reading shifts and seasons from the same replayed DNA."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

GRIEF = ["grief", "catharsis", "haunted"]
PULSE = ["thrill", "dread", "shock"]


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _add(client, h, title, emotions, days_ago, intensity=7):
    r = await client.post("/api/entries", json={
        "title": title, "intensity": intensity, "status": "finished",
        "finished_at": (date.today() - timedelta(days=days_ago)).isoformat(),
        "emotions": [{"emotion_id": e, "strength": 7} for e in emotions],
    }, headers=h)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_profile_carries_the_echo_for_the_book_just_saved(client):
    h = await _user(client, "echoer")
    for i in range(6):
        await _add(client, h, f"Ache {i}", GRIEF, 40 - i)
    saved = await _add(client, h, "Night Train", PULSE, 1)
    body = (await client.get("/api/dna/profile", headers=h)).json()
    assert body["echo"]["entry_id"] == saved["id"]
    assert body["echo"]["book_type"]["id"] == "adrenaline_seeker"
    assert body["echo"]["relation"] == "pulled"
    for key in ("season", "seasons", "eras", "moments", "leaning"):
        assert key in body


async def test_below_the_gate_the_echo_only_names_the_book(client):
    h = await _user(client, "echolow")
    saved = await _add(client, h, "First", PULSE, 1)
    body = (await client.get("/api/dna/profile", headers=h)).json()
    assert body["enough"] is False
    assert body["echo"]["entry_id"] == saved["id"]
    assert body["echo"]["relation"] == "reads_like"


async def test_shift_card_shows_once_after_a_real_change(client, db):
    from app.models.user import User

    h = await _user(client, "shifter")
    for i in range(6):
        await _add(client, h, f"Ache {i}", GRIEF, 60 - i)
    first = (await client.get("/api/dna/profile", headers=h)).json()
    assert first["archetype"]["id"] == "grief_romantic"
    assert first["shift_unseen"] is False          # a first label is never a "shift"

    for i in range(14):
        await _add(client, h, f"Pulse {i}", PULSE, 40 - i)
    moved = (await client.get("/api/dna/profile", headers=h)).json()
    assert moved["archetype"]["id"] == "adrenaline_seeker"
    assert [e["id"] for e in moved["eras"][:2]] == ["adrenaline_seeker", "grief_romantic"]
    assert moved["eras"][0]["books_that_moved"]
    assert moved["shift_unseen"] is True

    r = await client.post("/api/dna/shift-seen", headers=h)
    assert r.status_code == 204
    again = (await client.get("/api/dna/profile", headers=h)).json()
    assert again["shift_unseen"] is False
    user = (await db.execute(select(User).where(User.username == "shifter"))).scalar_one()
    await db.refresh(user)
    assert user.dna_seen_archetype == "adrenaline_seeker"


async def _seed(db, username, runs):
    """Books straight into the DB, oldest first: runs = [(feelings, n), ...]."""
    from app.models.book_entry import BookEntry, EntryEmotion
    from app.models.user import User

    user = (await db.execute(select(User).where(User.username == username))).scalar_one()
    total = sum(n for _, n in runs)
    k = 0
    for feelings, n in runs:
        for _ in range(n):
            e = BookEntry(user_id=user.id, title=f"bk{k}", intensity=7, status="finished",
                          finished_at=date.today() - timedelta(days=total - k))
            db.add(e)
            await db.flush()
            for slug in feelings:
                db.add(EntryEmotion(entry_id=e.id, emotion_id=slug, strength=7))
            k += 1
    await db.commit()
    return user


async def _shifts(db, user):
    from app.models.notification import Notification
    return (await db.execute(select(Notification).where(
        Notification.user_id == user.id, Notification.kind == "dna_shifted"))).scalars().all()


async def test_a_change_in_our_rules_is_not_announced_as_a_shift(client, db):
    """Snapshots written before the climate rule existed carry the bare table rev;
    a label that differs from theirs is our arithmetic, not the reader's reading."""
    from app.models.dna_snapshot import DNASnapshot
    from app.services.dna_engine import ARCHETYPE_TABLE_REV
    from app.services.dna_service import compute_and_cache, maybe_snapshot_and_notify

    await _user(client, "ruleguard")
    user = await _seed(db, "ruleguard", [(GRIEF, 16)])
    db.add(DNASnapshot(user_id=user.id, personality_type="The Comfort Architect",
                       emotion_data={"current_vector": {}, "archetype_table_rev": ARCHETYPE_TABLE_REV},
                       book_count=16, year=2026, trigger="drift"))
    await db.commit()
    await compute_and_cache(db, user)
    await maybe_snapshot_and_notify(db, user)
    await db.commit()
    assert await _shifts(db, user) == []


async def test_an_import_never_sends_a_shift(client, db):
    from app.services.dna_service import compute_and_cache, maybe_snapshot_and_notify

    await _user(client, "importer")
    user = await _seed(db, "importer", [(GRIEF, 16)])
    await compute_and_cache(db, user)
    await maybe_snapshot_and_notify(db, user)
    await db.commit()
    await _seed(db, "importer", [(PULSE, 30)])
    await compute_and_cache(db, user)
    snap = await maybe_snapshot_and_notify(db, user, notify_shift=False)
    await db.commit()
    assert snap is not None and snap.personality_type == "The Adrenaline Seeker"
    assert await _shifts(db, user) == []


async def test_recap_reads_shifts_and_seasons_from_the_replayed_dna(client):
    h = await _user(client, "recapper")
    for i in range(10):
        await _add(client, h, f"Ache {i}", GRIEF, 90 - i)
    for i in range(14):
        await _add(client, h, f"Pulse {i}", PULSE, 14 - i)
    body = (await client.get("/api/dna/profile", headers=h)).json()
    shift_month = body["eras"][0]["from"][:7]
    recap = (await client.get(f"/api/dna/recap?month={shift_month}", headers=h)).json()
    assert recap["personality_shift"] == {
        "previous_type": "The Grief Romantic", "current_type": "The Adrenaline Seeker", "shifted": True,
    }
    season_month = body["seasons"][0]["from"][:7]
    recap = (await client.get(f"/api/dna/recap?month={season_month}", headers=h)).json()
    assert body["seasons"][0]["id"] in {s["id"] for s in recap["seasons"]}


async def test_a_feelings_only_edit_gets_the_echo(client):
    """Changing only an entry's feelings touches no column on the entry; the
    echo still has to find it."""
    h = await _user(client, "reeditor")
    first = await _add(client, h, "Old one", GRIEF, 50)
    for i in range(6):
        await _add(client, h, f"Ache {i}", GRIEF, 40 - i)
    r = await client.put(f"/api/entries/{first['id']}", json={
        "emotions": [{"emotion_id": e, "strength": 7} for e in PULSE],
    }, headers=h)
    assert r.status_code == 200, r.text
    body = (await client.get("/api/dna/profile", headers=h)).json()
    assert body["echo"]["entry_id"] == first["id"]
    assert body["echo"]["book_type"]["id"] == "adrenaline_seeker"
