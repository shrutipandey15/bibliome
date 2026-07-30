"""Resonance matching + connection tests.

The GATE properties, in order of how much damage their failure does:
  1. Identity never leaves the API before both sides accept.
  2. A sealed note is unreadable to the person it was sent to until they answer.
  3. Nothing anywhere is counted in public.
  4. Matching is batch-computed, not derived on read.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio

PASSWORD = "hunter2pass"


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": PASSWORD,
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _log_book(client, headers, title="Piranesi", emotions=None, status="finished"):
    """Log an engaged entry with emotions — the raw material resonance runs on."""
    body = {
        "title": title,
        "author": "Susanna Clarke",
        "status": status,
        "intensity": 8,
        "emotions": emotions or [{"emotion_id": "awe", "strength": 8}],
    }
    r = await client.post("/api/entries", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _refresh(user_id):
    """Run the batch job the way production does — out of band, own session."""
    from app.database import async_session
    from app.services.resonance_service import refresh_matches_for_user

    async with async_session() as db:
        async with db.begin():
            return await refresh_matches_for_user(db, uuid.UUID(user_id))


async def _me(client, headers):
    r = await client.get("/api/auth/me", headers=headers)
    return r.json()


async def _pair(client, a_emotions=None, b_emotions=None, title="Piranesi", names=("ann", "bea")):
    """Two readers on the same book with overlapping emotions, matched."""
    ha = await _user(client, names[0])
    hb = await _user(client, names[1])
    await _log_book(client, ha, title, a_emotions or [{"emotion_id": "awe", "strength": 8}])
    await _log_book(client, hb, title, b_emotions or [{"emotion_id": "awe", "strength": 7}])
    me_a = await _me(client, ha)
    await _refresh(me_a["id"])
    return ha, hb


# ── Matching ──

async def test_shared_emotion_on_same_book_produces_a_match(client):
    ha, hb = await _pair(client)

    r = await client.get("/api/resonance/matches", headers=ha)
    assert r.status_code == 200, r.text
    matches = r.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["book_title"] == "Piranesi"
    assert [s["emotion_id"] for s in matches[0]["shared_emotions"]] == ["awe"]

    # The pair row serves both readers — B sees the same match, not a second one.
    r = await client.get("/api/resonance/matches", headers=hb)
    assert len(r.json()["matches"]) == 1


async def test_same_book_no_shared_emotion_is_not_a_match(client):
    ha = await _user(client, "cara")
    hb = await _user(client, "dee")
    await _log_book(client, ha, "Piranesi", [{"emotion_id": "awe", "strength": 8}])
    await _log_book(client, hb, "Piranesi", [{"emotion_id": "boredom", "strength": 8}])
    await _refresh((await _me(client, ha))["id"])

    r = await client.get("/api/resonance/matches", headers=ha)
    assert r.json()["matches"] == []


async def test_shared_emotion_different_book_is_not_a_match(client):
    ha = await _user(client, "eve")
    hb = await _user(client, "fay")
    await _log_book(client, ha, "Piranesi", [{"emotion_id": "awe", "strength": 8}])
    await _log_book(client, hb, "Beloved", [{"emotion_id": "awe", "strength": 8}])
    await _refresh((await _me(client, ha))["id"])

    r = await client.get("/api/resonance/matches", headers=ha)
    assert r.json()["matches"] == []


async def test_similar_intensity_is_strong_far_intensity_is_light(client):
    ha, _ = await _pair(
        client,
        a_emotions=[{"emotion_id": "awe", "strength": 9}],
        b_emotions=[{"emotion_id": "awe", "strength": 8}],
        names=("gia", "hal"),
    )
    r = await client.get("/api/resonance/matches", headers=ha)
    match = r.json()["matches"][0]
    assert match["strength"] == "strong"
    assert match["shared_emotions"][0]["close"] is True

    ha2, _ = await _pair(
        client,
        a_emotions=[{"emotion_id": "grief", "strength": 10}],
        b_emotions=[{"emotion_id": "grief", "strength": 2}],
        title="Beloved",
        names=("iris", "jodi"),
    )
    r = await client.get("/api/resonance/matches", headers=ha2)
    match = r.json()["matches"][0]
    assert match["strength"] == "light"
    assert match["shared_emotions"][0]["close"] is False


async def test_at_most_three_matches_are_surfaced(client):
    """Calm, not a flood: five overlapping readers still yield three suggestions."""
    ha = await _user(client, "kay")
    titles = ["Piranesi", "Beloved", "Dune", "Stoner", "Ubik"]
    for t in titles:
        await _log_book(client, ha, t, [{"emotion_id": "awe", "strength": 8}])
    for i, t in enumerate(titles):
        h = await _user(client, f"peer{i}")
        await _log_book(client, h, t, [{"emotion_id": "awe", "strength": 8}])

    await _refresh((await _me(client, ha))["id"])
    r = await client.get("/api/resonance/matches", headers=ha)
    assert len(r.json()["matches"]) == 3


async def test_surfaced_set_is_stable_across_loads(client):
    """The three suggestions a reader saw yesterday are the three they see today
    — the set doesn't reshuffle under them as new candidates arrive."""
    ha = await _user(client, "lena")
    titles = ["Piranesi", "Beloved", "Dune", "Stoner"]
    for t in titles:
        await _log_book(client, ha, t, [{"emotion_id": "awe", "strength": 8}])
    for i, t in enumerate(titles):
        h = await _user(client, f"stable{i}")
        await _log_book(client, h, t, [{"emotion_id": "awe", "strength": 8}])
    await _refresh((await _me(client, ha))["id"])

    first = {m["match_id"] for m in (await client.get("/api/resonance/matches", headers=ha)).json()["matches"]}
    second = {m["match_id"] for m in (await client.get("/api/resonance/matches", headers=ha)).json()["matches"]}
    assert first == second


async def test_blocked_users_are_never_matched(client):
    ha = await _user(client, "mia")
    hb = await _user(client, "ned")
    await _log_book(client, ha, "Piranesi", [{"emotion_id": "awe", "strength": 8}])
    await _log_book(client, hb, "Piranesi", [{"emotion_id": "awe", "strength": 8}])

    ned = await _me(client, hb)
    await client.post("/api/social/blocks", json={"handle": ned.get("handle") or ned["username"]}, headers=ha)

    await _refresh((await _me(client, ha))["id"])
    assert (await client.get("/api/resonance/matches", headers=ha)).json()["matches"] == []


async def test_refresh_is_idempotent(client):
    ha, _ = await _pair(client, names=("oda", "pia"))
    me = await _me(client, ha)
    assert await _refresh(me["id"]) == 0  # already banked by _pair; nothing new
    assert len((await client.get("/api/resonance/matches", headers=ha)).json()["matches"]) == 1


# ── Privacy ──

async def test_suggestion_carries_no_identity(client):
    ha, _ = await _pair(client, names=("quin", "rae"))
    match = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]

    assert match["handle"] is None
    for key in match:
        assert not any(bad in key.lower() for bad in ("user_id", "email", "name", "username")) or key in (
            "book_title", "book_author",
        )
    # And nothing in the serialized payload resolves to the other reader.
    assert "rae" not in str(match).lower()


async def test_a_sealed_note_is_unreadable_until_the_recipient_answers(client):
    ha, hb = await _pair(client, names=("sam", "tia"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]

    r = await client.post(
        f"/api/resonance/{match_id}/reach",
        json={"note": "The house. I have not stopped thinking about the house."},
        headers=ha,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert r.json()["direction"] == "you_reached"

    # The recipient sees that someone reached — and not one word of what they said.
    theirs = (await client.get("/api/resonance/matches", headers=hb)).json()["matches"][0]
    assert theirs["status"] == "pending"
    assert theirs["direction"] == "they_reached"
    assert theirs["their_note"] is None
    assert theirs["handle"] is None
    assert "house" not in str(theirs).lower()


async def test_identity_and_note_appear_only_after_both_accept(client):
    ha, hb = await _pair(client, names=("uma", "vic"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]
    await client.post(f"/api/resonance/{match_id}/reach", json={"note": "the house"}, headers=ha)

    r = await client.post(f"/api/resonance/{match_id}/respond", json={"accept": True}, headers=hb)
    assert r.status_code == 200, r.text
    connected = r.json()
    assert connected["status"] == "connected"
    assert connected["handle"] == "uma"          # now, and not before
    assert connected["their_note"] == "the house"
    assert connected["thread_id"] is not None

    mine = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]
    assert mine["handle"] == "vic"


async def test_a_non_party_cannot_touch_a_match(client):
    ha, _ = await _pair(client, names=("wren", "xen"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]
    outsider = await _user(client, "snoop")

    # 404, not 403: a 403 would confirm the match exists.
    r = await client.post(f"/api/resonance/{match_id}/reach", json={"note": "hi"}, headers=outsider)
    assert r.status_code == 404
    r = await client.post(f"/api/resonance/{match_id}/respond", json={"accept": True}, headers=outsider)
    assert r.status_code == 404


async def test_no_public_counts_anywhere(client):
    ha, _ = await _pair(client, names=("yuri", "zoe"))
    payload = (await client.get("/api/resonance/matches", headers=ha)).json()

    # The only number is the reader's own remaining reach budget.
    assert set(payload.keys()) == {"matches", "reaches_left_today"}
    for key in payload["matches"][0]:
        assert not any(bad in key.lower() for bad in ("count", "total", "followers", "popular"))


# ── Declines ──

async def test_decline_closes_the_match_permanently(client):
    ha, hb = await _pair(client, names=("abe", "bre"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]
    await client.post(f"/api/resonance/{match_id}/reach", json={"note": "hello"}, headers=ha)

    r = await client.post(f"/api/resonance/{match_id}/respond", json={"accept": False}, headers=hb)
    assert r.json()["status"] == "declined"

    # Gone for both sides, and never re-suggested by a later batch run.
    assert (await client.get("/api/resonance/matches", headers=ha)).json()["matches"] == []
    assert (await client.get("/api/resonance/matches", headers=hb)).json()["matches"] == []
    await _refresh((await _me(client, ha))["id"])
    assert (await client.get("/api/resonance/matches", headers=ha)).json()["matches"] == []


async def test_declined_pair_is_not_rematched_on_another_book(client):
    """A no is about the person, not the title."""
    ha = await _user(client, "cyd")
    hb = await _user(client, "dov")
    for t in ("Piranesi", "Beloved"):
        await _log_book(client, ha, t, [{"emotion_id": "awe", "strength": 8}])
        await _log_book(client, hb, t, [{"emotion_id": "awe", "strength": 8}])
    await _refresh((await _me(client, ha))["id"])

    matches = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"]
    for m in matches:
        await client.post(f"/api/resonance/{m['match_id']}/respond", json={"accept": False}, headers=ha)

    await _refresh((await _me(client, ha))["id"])
    assert (await client.get("/api/resonance/matches", headers=ha)).json()["matches"] == []


async def test_reach_is_rate_limited_per_day(client):
    """Reaching out is the harvesting vector, so the cap is per account per day."""
    from app.services.resonance_service import REACH_DAILY_LIMIT

    ha = await _user(client, "eli")
    titles = ["Piranesi", "Beloved", "Dune", "Stoner", "Ubik", "Solaris", "Ada"]
    for t in titles:
        await _log_book(client, ha, t, [{"emotion_id": "awe", "strength": 8}])
    for i, t in enumerate(titles):
        h = await _user(client, f"target{i}")
        await _log_book(client, h, t, [{"emotion_id": "awe", "strength": 8}])
    await _refresh((await _me(client, ha))["id"])

    sent = 0
    for _ in range(REACH_DAILY_LIMIT + 2):
        matches = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"]
        pending = [m for m in matches if m["status"] == "suggested"]
        if not pending:
            break
        r = await client.post(
            f"/api/resonance/{pending[0]['match_id']}/reach", json={"note": "hi"}, headers=ha
        )
        if r.status_code == 429:
            break
        assert r.status_code == 200, r.text
        sent += 1

    assert sent == REACH_DAILY_LIMIT


# ── Threads ──

async def _connected_thread(client, names=("fin", "gus")):
    ha, hb = await _pair(client, names=names)
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]
    await client.post(f"/api/resonance/{match_id}/reach", json={"note": "opening note"}, headers=ha)
    r = await client.post(f"/api/resonance/{match_id}/respond", json={"accept": True}, headers=hb)
    return ha, hb, r.json()["thread_id"]


async def test_thread_opens_with_the_notes_already_in_it(client):
    ha, hb, thread_id = await _connected_thread(client)
    r = await client.get(f"/api/threads/{thread_id}/messages", headers=hb)
    assert r.status_code == 200
    bodies = [m["body"] for m in r.json()["messages"]]
    assert bodies == ["opening note"]


async def test_free_text_messaging_both_ways(client):
    ha, hb, thread_id = await _connected_thread(client, names=("hana", "ivo"))

    r = await client.post(
        f"/api/threads/{thread_id}/messages",
        json={"body": "Completely unrelated to books: what's your coffee order?"},
        headers=hb,
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_mine"] is True

    r = await client.get(f"/api/threads/{thread_id}/messages", headers=ha)
    messages = r.json()["messages"]
    assert len(messages) == 2
    assert messages[-1]["handle"] == "ivo"
    assert messages[-1]["is_mine"] is False


async def test_outsiders_cannot_read_or_write_a_thread(client):
    _, _, thread_id = await _connected_thread(client, names=("jem", "kit"))
    outsider = await _user(client, "lurker")

    assert (await client.get(f"/api/threads/{thread_id}/messages", headers=outsider)).status_code == 404
    r = await client.post(f"/api/threads/{thread_id}/messages", json={"body": "hi"}, headers=outsider)
    assert r.status_code == 404


async def test_no_thread_before_both_sides_accept(client):
    ha, hb = await _pair(client, names=("moss", "nel"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]
    r = await client.post(f"/api/resonance/{match_id}/reach", json={"note": "hi"}, headers=ha)
    assert r.json()["thread_id"] is None
    assert (await client.get("/api/threads", headers=ha)).json() == []


async def test_block_on_thread_closes_it_and_blocks_everywhere(client):
    ha, hb, thread_id = await _connected_thread(client, names=("ora", "pax"))

    assert (await client.post(f"/api/threads/{thread_id}/block", headers=hb)).status_code == 204

    # The conversation stops for both sides...
    r = await client.post(f"/api/threads/{thread_id}/messages", json={"body": "still there?"}, headers=ha)
    assert r.status_code == 400
    assert (await client.get("/api/threads", headers=hb)).json() == []

    # ...and the block is the ordinary cross-surface one, so they can't be
    # rematched either.
    await _refresh((await _me(client, ha))["id"])
    assert (await client.get("/api/resonance/matches", headers=ha)).json()["matches"] == []


async def test_report_on_thread_is_filed_and_blocks_by_default(client):
    from sqlalchemy import select

    from app.database import async_session
    from app.models.social import Report

    ha, hb, thread_id = await _connected_thread(client, names=("quil", "rex"))
    r = await client.post(
        f"/api/threads/{thread_id}/report", json={"category": "harassment"}, headers=hb
    )
    assert r.status_code == 202

    async with async_session() as db:
        reports = (await db.execute(select(Report).where(Report.target_type == "thread"))).scalars().all()
    assert len(reports) == 1
    assert reports[0].category == "harassment"

    r = await client.post(f"/api/threads/{thread_id}/messages", json={"body": "hello?"}, headers=ha)
    assert r.status_code == 400


async def test_mutual_reach_connects_without_either_reading_the_other(client):
    ha, hb = await _pair(client, names=("sol", "tam"))
    match_id = (await client.get("/api/resonance/matches", headers=ha)).json()["matches"][0]["match_id"]

    await client.post(f"/api/resonance/{match_id}/reach", json={"note": "from sol"}, headers=ha)
    r = await client.post(f"/api/resonance/{match_id}/reach", json={"note": "from tam"}, headers=hb)

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "connected"
    thread_id = r.json()["thread_id"]

    bodies = [
        m["body"]
        for m in (await client.get(f"/api/threads/{thread_id}/messages", headers=ha)).json()["messages"]
    ]
    assert sorted(bodies) == ["from sol", "from tam"]
