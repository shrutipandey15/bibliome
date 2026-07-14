"""Echo + safety tests (B3.14) — the Phase 3 GATE properties."""

import pytest

pytestmark = pytest.mark.asyncio


async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": name, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _post_echo(client, headers, **over):
    body = {"body": "This book undid me.", "book_title": "Piranesi", "primary_emotion": "awe", **over}
    return await client.post("/api/echoes", json=body, headers=headers)


# ── Echo core ──

async def test_post_book_anchored_echo_appears_in_feed(client):
    h = await _user(client, "alice")
    r = await _post_echo(client, h)
    assert r.status_code == 201, r.text
    assert r.json()["held_for_review"] is False

    r = await client.get("/api/echoes/feed", headers=h)
    assert r.status_code == 200
    feed = r.json()
    assert len(feed["echoes"]) == 1
    assert feed["caught_up"] is True  # feed ends


async def test_echo_requires_an_anchor(client):
    h = await _user(client, "bob")
    r = await client.post("/api/echoes", json={"body": "no anchor here"}, headers=h)
    assert r.status_code == 400


async def test_feed_carries_no_public_count(client):
    """The card may carry render state (my_reactions, reply previews), but never a
    *public* count. The only count field, reaction_counts, is author-private and is
    the author's own echo here — so a peer viewer must see it as None."""
    h = await _user(client, "carol")
    peer = await _user(client, "carol_peer")
    await _post_echo(client, h)

    # A peer viewer never receives reaction_counts, and never any reply/like count.
    r = await client.get("/api/echoes/feed", headers=peer)
    card = r.json()["echoes"][0]
    assert card["reaction_counts"] is None
    assert "has_more_replies" in card and isinstance(card["has_more_replies"], bool)
    for key in card:
        assert not any(bad in key.lower() for bad in ("like", "score", "karma"))
        # No field exposes a *reply* count in any form.
        assert "reply_count" not in key.lower() and "replies_count" not in key.lower()


async def test_reply_shows_in_thread(client):
    a = await _user(client, "dave")
    b = await _user(client, "erin")
    echo_id = (await _post_echo(client, a)).json()["echo"]["id"]
    r = await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "me too"}, headers=b)
    assert r.status_code == 201
    r = await client.get(f"/api/echoes/{echo_id}", headers=a)
    assert len(r.json()["replies"]) == 1
    assert r.json()["replies"][0]["handle"] == "erin"


# ── Block / mute enforcement ──

async def test_block_hides_content_bidirectionally(client):
    a = await _user(client, "fay")
    b = await _user(client, "gus")
    await _post_echo(client, b, body="gus was here")

    # a blocks gus
    r = await client.post("/api/social/blocks", json={"handle": "gus"}, headers=a)
    assert r.status_code == 204

    # gus's echo is gone from a's feed
    r = await client.get("/api/echoes/feed", headers=a)
    assert all(e["handle"] != "gus" for e in r.json()["echoes"])

    # and a's echoes are gone from gus's feed (bidirectional)
    await _post_echo(client, a, body="fay was here")
    r = await client.get("/api/echoes/feed", headers=b)
    assert all(e["handle"] != "fay" for e in r.json()["echoes"])


async def test_mute_is_one_way(client):
    a = await _user(client, "hana")
    b = await _user(client, "ivan")
    await _post_echo(client, b, body="ivan speaks")
    await client.post("/api/social/mutes", json={"handle": "ivan"}, headers=a)

    r = await client.get("/api/echoes/feed", headers=a)
    assert all(e["handle"] != "ivan" for e in r.json()["echoes"])

    # ivan is unaffected — hana's echo still visible to ivan
    await _post_echo(client, a, body="hana speaks")
    r = await client.get("/api/echoes/feed", headers=b)
    assert any(e["handle"] == "hana" for e in r.json()["echoes"])


# ── Report auto-throttle ──

async def test_report_threshold_holds_echo(client):
    author = await _user(client, "jill")
    echo_id = (await _post_echo(client, author, body="something reported")).json()["echo"]["id"]

    for name in ("rep1", "rep2", "rep3"):
        reporter = await _user(client, name)
        r = await client.post(f"/api/echoes/{echo_id}/report", json={"category": "spam"}, headers=reporter)
        assert r.status_code == 202

    # After 3 weighted reports the echo is held → gone from the feed.
    viewer = await _user(client, "viewer")
    r = await client.get("/api/echoes/feed", headers=viewer)
    assert all(e["id"] != echo_id for e in r.json()["echoes"])


# ── Crisis path ──

async def test_self_harm_routes_to_support_not_punishment(client):
    h = await _user(client, "kate")
    r = await client.post("/api/echoes", json={
        "body": "I want to kill myself after reading this",
        "primary_emotion": "devastation",
    }, headers=h)
    assert r.status_code == 201
    body = r.json()
    assert body["held_for_review"] is True
    assert body["crisis"] is not None
    assert "988" in str(body["crisis"]["resources"])


# ── Private reactions ──

async def test_reactions_private_to_author(client):
    a = await _user(client, "liam")
    b = await _user(client, "mira")
    echo_id = (await _post_echo(client, a)).json()["echo"]["id"]

    r = await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "felt_this", "on": True}, headers=b)
    assert r.status_code == 200
    # The reactor gets their own state back, but NOT the private counts.
    assert r.json()["my_reactions"] == ["felt_this"]
    assert r.json()["reaction_counts"] is None

    # author can read the aggregate
    r = await client.get(f"/api/echoes/{echo_id}/reactions", headers=a)
    assert r.status_code == 200
    assert r.json().get("felt_this") == 1

    # a non-author cannot
    r = await client.get(f"/api/echoes/{echo_id}/reactions", headers=b)
    assert r.status_code == 403


# ── Handle change ──

async def test_moderation_queue_and_dismiss_restores(client, db):
    from sqlalchemy import update
    from app.models.user import User

    author = await _user(client, "opal")
    echo_id = (await _post_echo(client, author, body="held then restored")).json()["echo"]["id"]
    for name in ("mrep1", "mrep2", "mrep3"):
        reporter = await _user(client, name)
        await client.post(f"/api/echoes/{echo_id}/report", json={"category": "spam"}, headers=reporter)

    # promote an admin
    await _user(client, "modadmin")
    await db.execute(update(User).where(User.username == "modadmin").values(is_admin=True))
    await db.commit()
    admin = {"Authorization": (await client.post("/api/auth/login", json={
        "email": "modadmin@example.com", "password": "hunter2pass"})).json()["access_token"]}
    admin = {"Authorization": f"Bearer {admin['Authorization']}"}

    r = await client.get("/api/admin/moderation/queue", headers=admin)
    assert r.status_code == 200
    assert any(item["target_id"] == echo_id for item in r.json())

    r = await client.post("/api/admin/moderation/resolve",
                          json={"target_type": "echo", "target_id": echo_id, "action": "dismiss"},
                          headers=admin)
    assert r.status_code == 200

    # dismissed → echo restored to the feed
    r = await client.get("/api/echoes/feed", headers=author)
    assert any(e["id"] == echo_id for e in r.json()["echoes"])


# ── Phase 6: Echo UX ──

async def test_feed_returns_reply_previews_and_my_reaction_state(client):
    author = await _user(client, "p6author")
    reader = await _user(client, "p6reader")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]

    # Three replies: only the first two preview inline, and has_more flips on.
    for body in ("first", "second", "third"):
        await client.post(f"/api/echoes/{echo_id}/replies", json={"body": body}, headers=reader)
    # The reader has set a reaction.
    await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "felt_this", "on": True}, headers=reader)

    card = (await client.get("/api/echoes/feed", headers=reader)).json()["echoes"][0]
    assert [r["body"] for r in card["replies_preview"]] == ["first", "second"]
    assert card["has_more_replies"] is True
    assert card["my_reactions"] == ["felt_this"]


async def test_author_sees_private_counts_in_feed_but_peer_does_not(client):
    author = await _user(client, "p6owner")
    peer = await _user(client, "p6peer")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]
    await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "felt_this", "on": True}, headers=peer)

    author_card = (await client.get("/api/echoes/feed", headers=author)).json()["echoes"][0]
    assert author_card["reaction_counts"] == {"felt_this": 1}

    peer_card = (await client.get("/api/echoes/feed", headers=peer)).json()["echoes"][0]
    assert peer_card["reaction_counts"] is None


async def test_feed_query_count_is_constant_in_page_size(client, db):
    """The feed is the one fan-out surface; annotations must not be N+1 (B6.1)."""
    from sqlalchemy import event
    from app.database import engine

    author = await _user(client, "p6load")
    reader = await _user(client, "p6loadr")
    # Log a few books so the new-account echo cool-down doesn't cap this author at 3.
    for i in range(3):
        await client.post("/api/entries", json={"title": f"Warmup {i}", "status": "finished"}, headers=author)
    # A handful of echoes, each with replies + a reaction, so per-item work would show.
    for i in range(6):
        eid = (await _post_echo(client, author, body=f"echo {i}", book_title=f"Book {i}")).json()["echo"]["id"]
        await client.post(f"/api/echoes/{eid}/replies", json={"body": "a"}, headers=reader)
        await client.post(f"/api/echoes/{eid}/replies", json={"body": "b"}, headers=reader)
        await client.post(f"/api/echoes/{eid}/react", json={"kind": "felt_this", "on": True}, headers=reader)

    async def _queries_for(limit):
        counter = {"n": 0}
        def _cb(*a):
            counter["n"] += 1
        event.listen(engine.sync_engine, "before_cursor_execute", _cb)
        try:
            r = await client.get(f"/api/echoes/feed?limit={limit}", headers=reader)
            assert r.status_code == 200
            return counter["n"], len(r.json()["echoes"])
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _cb)

    small_q, small_n = await _queries_for(2)
    large_q, large_n = await _queries_for(20)
    assert large_n > small_n  # the larger page really did return more echoes
    assert small_q == large_q  # …but cost the same number of queries


async def test_to_my_shelf_creates_want_to_read_idempotently(client):
    author = await _user(client, "p6shelfa")
    reader = await _user(client, "p6shelfr")
    echo_id = (await _post_echo(client, author, book_title="Stoner", book_author="John Williams")).json()["echo"]["id"]

    r = await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "adding_to_list", "on": True}, headers=reader)
    assert r.status_code == 200
    assert r.json()["added_to_shelf"] is True

    def _stoner(entries):
        return [e for e in entries if e["title"] == "Stoner"]

    entries = (await client.get("/api/entries", headers=reader)).json()
    rows = entries["entries"] if isinstance(entries, dict) else entries
    stoner = _stoner(rows)
    assert len(stoner) == 1
    assert stoner[0]["status"] == "want_to_read"

    # React again → still one entry, and no new shelf claim.
    r = await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "adding_to_list", "on": True}, headers=reader)
    assert r.json()["added_to_shelf"] is False

    # Un-react → the reaction clears but the shelf entry SURVIVES (deliberate asymmetry).
    await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "adding_to_list", "on": False}, headers=reader)
    entries = (await client.get("/api/entries", headers=reader)).json()
    rows = entries["entries"] if isinstance(entries, dict) else entries
    assert len(_stoner(rows)) == 1


async def test_emotion_only_echo_adds_nothing_to_shelf(client):
    author = await _user(client, "p6emoauthor")
    reader = await _user(client, "p6emoreader")
    # No book anchor — emotion only.
    echo_id = (await client.post("/api/echoes", json={
        "body": "pure feeling", "primary_emotion": "awe",
    }, headers=author)).json()["echo"]["id"]

    r = await client.post(f"/api/echoes/{echo_id}/react", json={"kind": "adding_to_list", "on": True}, headers=reader)
    assert r.status_code == 200
    assert r.json()["added_to_shelf"] is False


async def test_blocked_users_replies_absent_from_previews(client):
    author = await _user(client, "p6ba")
    troll = await _user(client, "p6btroll")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]
    await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "unwelcome"}, headers=troll)

    # Author blocks the troll; the troll's reply must vanish from the preview path.
    await client.post("/api/social/blocks", json={"handle": "p6btroll"}, headers=author)
    card = (await client.get("/api/echoes/feed", headers=author)).json()["echoes"][0]
    assert all(r["handle"] != "p6btroll" for r in card["replies_preview"])


async def test_posted_reply_returns_full_row(client):
    author = await _user(client, "p6ra")
    reader = await _user(client, "p6rr")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]
    r = await client.post(f"/api/echoes/{echo_id}/replies", json={"body": "me too"}, headers=reader)
    assert r.status_code == 201
    row = r.json()
    assert row["handle"] == "p6rr" and row["body"] == "me too" and row["created_at"]
    assert not any("count" in k.lower() for k in row)


# ── Phase 6: the weekly Prompt (B6.5) ──

async def _seed_prompt(db, question="A book that made you feel longing this month?"):
    from datetime import datetime, timedelta, timezone
    from app.models.prompt import Prompt
    now = datetime.now(timezone.utc)
    p = Prompt(question=question, starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=6))
    db.add(p)
    await db.commit()
    return p


async def test_prompts_today_returns_live_prompt(client, db):
    await _seed_prompt(db)
    h = await _user(client, "p6prompt")
    r = await client.get("/api/prompts/today", headers=h)
    assert r.status_code == 200
    assert r.json()["question"] == "A book that made you feel longing this month?"


async def test_prompts_today_null_when_none_live(client, db):
    from datetime import datetime, timedelta, timezone
    from app.models.prompt import Prompt
    now = datetime.now(timezone.utc)
    # A prompt whose window is entirely in the past → nothing is live.
    db.add(Prompt(question="old one", starts_at=now - timedelta(days=30), ends_at=now - timedelta(days=23)))
    await db.commit()
    h = await _user(client, "p6noprompt")
    r = await client.get("/api/prompts/today", headers=h)
    assert r.status_code == 200
    assert r.json() is None


async def test_echo_can_answer_prompt_and_feed_groups_by_it(client, db):
    prompt = await _seed_prompt(db)
    pid = str(prompt.id)
    author = await _user(client, "p6answerer")
    other = await _user(client, "p6other")

    # One echo answers the prompt; one doesn't.
    r = await client.post("/api/echoes", json={
        "body": "Stoner left me aching", "book_title": "Stoner", "prompt_id": pid,
    }, headers=author)
    assert r.status_code == 201
    assert r.json()["echo"]["prompt_id"] == pid
    await _post_echo(client, other)  # unrelated echo, no prompt

    # The campfire feed shows only the prompt's answers.
    r = await client.get(f"/api/echoes/feed?prompt_id={pid}", headers=author)
    cards = r.json()["echoes"]
    assert len(cards) == 1
    assert cards[0]["prompt_id"] == pid


async def test_unknown_prompt_id_is_rejected(client):
    import uuid as _uuid
    h = await _user(client, "p6badprompt")
    r = await client.post("/api/echoes", json={
        "body": "answering nothing", "book_title": "Ghost", "prompt_id": str(_uuid.uuid4()),
    }, headers=h)
    assert r.status_code == 400


async def test_handle_change_and_cooldown(client):
    h = await _user(client, "nora")
    r = await client.patch("/api/user/handle", json={"handle": "nova"}, headers=h)
    assert r.status_code == 200
    assert r.json()["handle"] == "nova"
    # Second change is rate-limited.
    r = await client.patch("/api/user/handle", json={"handle": "nyx"}, headers=h)
    assert r.status_code == 400
