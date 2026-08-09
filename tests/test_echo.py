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
    # `reply_count` exists on the schema but is author-private, exactly like
    # reaction_counts: the gate is that no *count* reaches a peer, not that the
    # key is absent. Its value, not its name, is what has to stay private.
    assert card["reply_count"] is None
    for key in card:
        assert not any(bad in key.lower() for bad in ("like", "score", "karma"))
    # Nothing numeric about other people's engagement reaches a peer at all.
    for key, value in card.items():
        if isinstance(value, int) and not isinstance(value, bool):
            raise AssertionError(f"peer received a bare count in {key!r}: {value}")


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
    item = next(i for i in r.json() if i["target_id"] == echo_id)

    # The queue has to carry enough to adjudicate on. Held content is filtered
    # out of the public feed, so if the preview isn't here there is nowhere else
    # an admin could go look before choosing remove-vs-dismiss.
    assert item["preview"] == "held then restored"
    assert item["author_handle"] == "opal"
    assert item["status"] == "held"
    assert item["target_exists"] is True
    assert item["report_count"] == 3
    assert item["categories"] == ["spam"]

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


# ── Frontend-blocking contract: mine / reply_count / is_mine ──

async def test_mine_filter_returns_only_the_viewers_echoes(client):
    """The "your echoes" tab. Shipped as a real server filter because a server
    that ignored an unknown param would return the everyone-feed, and the tab
    would silently lie about whose echoes those are."""
    a = await _user(client, "minea")
    b = await _user(client, "mineb")
    await _post_echo(client, a, body="Mine, and it wrecked me.", book_title="Piranesi")
    await _post_echo(client, b, body="Theirs, and it held me.", book_title="Stoner")

    everyone = (await client.get("/api/echoes/feed", headers=a)).json()["echoes"]
    assert len(everyone) == 2

    r = await client.get("/api/echoes/feed?mine=true", headers=a)
    assert r.status_code == 200, r.text
    mine = r.json()["echoes"]
    assert len(mine) == 1
    assert mine[0]["book_title"] == "Piranesi"
    assert all(e["is_mine"] for e in mine)


async def test_mine_composes_with_the_emotion_anchor(client):
    """`mine` must narrow the other anchors, not replace them — otherwise "my
    echoes tagged grief" becomes a client-side filter over someone else's page."""
    a = await _user(client, "minec")
    b = await _user(client, "mined")
    await _post_echo(client, a, body="Grief one.", book_title="A", primary_emotion="grief")
    await _post_echo(client, a, body="Awe one.", book_title="B", primary_emotion="awe")
    await _post_echo(client, b, body="Their grief.", book_title="C", primary_emotion="grief")

    r = await client.get("/api/echoes/feed?mine=true&emotion=grief", headers=a)
    echoes = r.json()["echoes"]
    assert len(echoes) == 1
    assert echoes[0]["book_title"] == "A"


async def test_reply_count_is_author_only_and_not_capped_by_the_preview(client):
    """The tally must count *all* replies. Deriving it from `replies_preview`
    would cap at 2 and under-report any echo with a real conversation."""
    author = await _user(client, "rcauthor")
    peer = await _user(client, "rcpeer")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]

    for i in range(4):
        r = await client.post(f"/api/echoes/{echo_id}/replies",
                              json={"body": f"reply {i}"}, headers=peer)
        assert r.status_code == 201, r.text

    mine = (await client.get("/api/echoes/feed", headers=author)).json()["echoes"][0]
    assert mine["reply_count"] == 4          # not 2, the preview cap
    assert len(mine["replies_preview"]) == 2
    assert mine["has_more_replies"] is True

    theirs = (await client.get("/api/echoes/feed", headers=peer)).json()["echoes"][0]
    assert theirs["reply_count"] is None     # never leaks to non-authors


async def test_reply_count_is_zero_not_null_for_an_authors_quiet_echo(client):
    """For the author, absent must not be confusable with none — the whole point
    of the field is telling "no replies yet" apart from "not yours"."""
    author = await _user(client, "rcquiet")
    await _post_echo(client, author)
    mine = (await client.get("/api/echoes/feed", headers=author)).json()["echoes"][0]
    assert mine["reply_count"] == 0
    assert mine["is_mine"] is True


async def test_is_mine_does_not_depend_on_reaction_counts(client):
    """Ownership is stated, not inferred. The old inference ("reaction_counts is
    not None") happened to hold, but it coupled the "yours" pill and
    self-reaction suppression to the nullability of an unrelated field."""
    author = await _user(client, "ismineA")
    peer = await _user(client, "ismineB")
    echo_id = (await _post_echo(client, author)).json()["echo"]["id"]

    mine = (await client.get("/api/echoes/feed", headers=author)).json()["echoes"][0]
    assert mine["is_mine"] is True
    assert mine["reaction_counts"] == {}   # no reactions yet — still owned

    theirs = (await client.get("/api/echoes/feed", headers=peer)).json()["echoes"][0]
    assert theirs["is_mine"] is False
    assert theirs["reaction_counts"] is None

    # And on the single-echo thread route, which builds cards without annotations.
    thread = (await client.get(f"/api/echoes/{echo_id}", headers=author)).json()
    assert thread["echo"]["is_mine"] is True
    thread = (await client.get(f"/api/echoes/{echo_id}", headers=peer)).json()
    assert thread["echo"]["is_mine"] is False
