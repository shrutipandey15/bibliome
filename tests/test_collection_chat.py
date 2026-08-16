"""Collection chat (#6): ONE room per collection.

A group room fails differently from a 1:1 thread, so most of this file is about
the awkward cases: a block between two people who both belong here, a book pulled
out mid-conversation, a member who leaves, two messages in the same millisecond.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _auth(client, email, username):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _me(client, headers):
    return (await client.get("/api/auth/me", headers=headers)).json()["id"]


async def _handle(client, headers):
    return (await client.get("/api/me/profile", headers=headers)).json().get("handle")


async def _block(client, blocker_h, target_h):
    """Block by handle — the real endpoint, so the test exercises the real path."""
    handle = await _handle(client, target_h)
    r = await client.post("/api/social/blocks", json={"handle": handle}, headers=blocker_h)
    assert r.status_code == 204, r.text


async def _book(client, headers, title="Piranesi"):
    r = await client.post("/api/entries", json={"title": title, "intensity": 7, "emotions": []},
                          headers=headers)
    return r.json()["book_id"]


async def _room(client, owner_h, *guest_h, title="Group Read"):
    """A collection with a book in it and every guest joined."""
    cid = (await client.post("/api/collections", json={"title": title}, headers=owner_h)).json()["id"]
    book = await _book(client, owner_h)
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner_h)
    token = (await client.post(f"/api/collections/{cid}/invites", json={}, headers=owner_h)).json()["token"]
    for g in guest_h:
        await client.post(f"/api/collections/invites/{token}/join", headers=g)
    return cid, book


async def _say(client, headers, cid, body, book=None):
    payload = {"body": body}
    if book:
        payload["book_id"] = book
    return await client.post(f"/api/collections/{cid}/messages",
                             json=payload, headers=headers)


async def _read(client, headers, cid, **params):
    return await client.get(f"/api/collections/{cid}/messages",
                            params=params, headers=headers)


# ── The ordinary path ──

async def test_members_can_talk_about_a_book(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    assert (await _say(client, owner, cid, "this wrecked me")).status_code == 201
    assert (await _say(client, friend, cid, "the ending especially")).status_code == 201

    body = (await _read(client, friend, cid)).json()
    assert [m["body"] for m in body["messages"]] == ["this wrecked me", "the ending especially"]
    assert [m["is_mine"] for m in body["messages"]] == [False, True]


async def test_a_stranger_cannot_read_or_post(client):
    owner = await _auth(client, "o@example.com", "owner")
    stranger = await _auth(client, "s@example.com", "stranger")
    cid, book = await _room(client, owner)
    await _say(client, owner, cid, "hello")

    assert (await _read(client, stranger, cid)).status_code == 404
    assert (await _say(client, stranger, cid, "hi")).status_code == 404


async def test_every_book_is_offered_as_an_attachment(client):
    """The book list is now what you can ATTACH to a message, not a set of rooms."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    quiet = await _book(client, owner, "The Employees")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": quiet}, headers=owner)

    rows = (await client.get(f"/api/collections/{cid}/conversations", headers=owner)).json()
    assert {r["book_id"] for r in rows} == {book, quiet}


# ── The book is the anchor ──

async def test_you_can_talk_without_attaching_anything(client):
    """The room belongs to the collection, so a message needs no book at all.
    This is the whole point of the change — a general remark had nowhere to go
    when every message had to pick a room."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, _seeded = await _room(client, owner)

    r = await _say(client, owner, cid, "hello everyone")
    assert r.status_code == 201, r.text
    assert r.json()["book_id"] is None


async def test_cannot_attach_a_book_the_collection_does_not_hold(client):
    """Not a gate on talking — a gate on the LABEL. An attachment pointing
    outside the collection would render as something nobody can follow."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, _in_room = await _room(client, owner)
    elsewhere = await _book(client, owner, "Some Other Book")

    r = await _say(client, owner, cid, "hi", book=elsewhere)
    assert r.status_code == 400


async def test_removing_a_book_leaves_the_conversation_standing(client):
    """One member tidying a shelf must not delete everyone else's writing — and
    with one room per collection it must not close the room either."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    await _say(client, friend, cid, "a thing I said", book=book)

    await client.delete(f"/api/collections/{cid}/books/{book}", headers=owner)

    body = (await _read(client, friend, cid)).json()
    assert [m["body"] for m in body["messages"]] == ["a thing I said"]
    # And the room still takes new messages.
    assert (await _say(client, friend, cid, "still here")).status_code == 201


# ── Blocks: hide the people, keep the room ──

async def test_a_block_hides_both_ways_without_evicting_anyone(client):
    """In a 1:1 thread a block ends the conversation. Here it must not throw
    either party out of a collection they both belong to."""
    owner = await _auth(client, "o@example.com", "owner")
    a = await _auth(client, "a@example.com", "areader")
    b = await _auth(client, "b@example.com", "breader")
    cid, book = await _room(client, owner, a, b)

    await _say(client, owner, cid, "from the owner")
    await _say(client, a, cid, "from A")
    await _say(client, b, cid, "from B")

    await _block(client, a, b)

    a_sees = [m["body"] for m in (await _read(client, a, cid)).json()["messages"]]
    b_sees = [m["body"] for m in (await _read(client, b, cid)).json()["messages"]]

    assert "from B" not in a_sees      # A blocked B
    assert "from A" not in b_sees      # and it cuts both ways
    assert "from the owner" in a_sees  # everyone else is untouched
    assert "from the owner" in b_sees

    # Neither was evicted, and both can still speak.
    assert (await _say(client, a, cid, "A again")).status_code == 201
    assert (await _say(client, b, cid, "B again")).status_code == 201


async def test_a_blocked_members_post_is_not_surfaced_as_activity(client):
    """"Someone said something" must not point at a message the viewer can't read."""
    owner = await _auth(client, "o@example.com", "owner")
    b = await _auth(client, "b@example.com", "breader")
    cid, book = await _room(client, owner, b)

    await _block(client, owner, b)
    await _say(client, b, cid, "unreadable to the owner")

    rows = (await client.get(f"/api/collections/{cid}/conversations", headers=owner)).json()
    row = next(r for r in rows if r["book_id"] == book)
    assert row["message_count"] == 0
    assert row["last_message_at"] is None


# ── Moderation ──

async def test_a_threat_is_refused_not_silently_held(client):
    """A room of four is not a feed to hide it in — the sender would notice their
    message never landed, so a silent hold would be a lie."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    r = await _say(client, owner, cid, "i will kill you")
    assert r.status_code == 422
    assert "can't be sent" in r.json()["detail"]

    # And it really is not in the room.
    assert (await _read(client, friend, cid)).json()["messages"] == []


async def test_a_message_that_sounds_like_crisis_sends_and_returns_resources(client):
    """Care, not punishment — the same stance Echo and resonance take."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    r = await _say(client, owner, cid, "i want to kill myself")
    assert r.status_code == 201
    assert r.json()["crisis"] is not None

    # It sent — and the resources went to the sender only, not into the room.
    seen = (await _read(client, friend, cid)).json()["messages"]
    assert len(seen) == 1
    assert seen[0]["crisis"] is None


async def test_contact_details_are_allowed_in_a_private_room(client):
    """Echo holds these because it is public. A group who chose each other
    swapping details is the room working."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    assert (await _say(client, owner, cid, "mail me at a@b.com")).status_code == 201


async def test_empty_and_oversized_bodies_are_rejected(client):
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)

    # Whitespace clears the schema's min_length but is empty once trimmed, so
    # the service is what has to catch it.
    assert (await _say(client, owner, cid, "   ")).status_code == 400
    assert (await _say(client, owner, cid, book, "x" * 2001)).status_code == 422


# ── Leaving ──

async def test_leaving_keeps_your_words_in_the_room(client):
    """Consistent with #5: a departing member's books stay, and so do their words."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    await _say(client, friend, cid, "said before leaving")

    friend_id = await _me(client, friend)
    assert (await client.delete(f"/api/collections/{cid}/members/{friend_id}", headers=friend)).status_code == 204

    body = (await _read(client, owner, cid)).json()
    assert [m["body"] for m in body["messages"]] == ["said before leaving"]
    # And they can no longer read or post.
    assert (await _read(client, friend, cid)).status_code == 404


async def test_someone_who_left_stops_being_notified(client, db):
    """Membership is read live, so a departed member isn't nudged about a room
    they can no longer open."""
    from sqlalchemy import select
    from app.models.notification import Notification

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    friend_id = await _me(client, friend)
    await client.delete(f"/api/collections/{cid}/members/{friend_id}", headers=friend)

    await _say(client, owner, cid, "anyone there")

    from app.database import async_session
    async with async_session() as s:
        rows = (await s.execute(
            select(Notification).where(Notification.user_id == uuid.UUID(friend_id))
        )).scalars().all()
    assert [n for n in rows if n.kind == "collection_message"] == []


async def test_a_message_notifies_the_other_members_but_not_the_sender(client):
    from sqlalchemy import select
    from app.models.notification import Notification

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    owner_id = uuid.UUID(await _me(client, owner))
    friend_id = uuid.UUID(await _me(client, friend))

    await _say(client, owner, cid, "started a thread")

    from app.database import async_session
    async with async_session() as s:
        rows = (await s.execute(
            select(Notification).where(Notification.kind == "collection_message")
        )).scalars().all()
    targets = {n.user_id for n in rows}
    assert friend_id in targets
    assert owner_id not in targets


# ── Deleting ──

async def test_authors_delete_their_own_and_the_owner_deletes_any(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    mine = (await _say(client, friend, cid, "mine")).json()["id"]
    theirs = (await _say(client, owner, cid, "theirs")).json()["id"]

    # A member cannot rewrite the record of what everyone else said.
    assert (await client.delete(f"/api/collections/{cid}/messages/{theirs}", headers=friend)).status_code == 403
    # But can take back their own.
    assert (await client.delete(f"/api/collections/{cid}/messages/{mine}", headers=friend)).status_code == 204
    # And the owner can clean up the room.
    assert (await client.delete(f"/api/collections/{cid}/messages/{theirs}", headers=owner)).status_code == 204

    assert (await _read(client, owner, cid)).json()["messages"] == []


# ── Paging ──

async def test_paging_does_not_skip_or_repeat_messages_sharing_a_timestamp(client, db):
    """Two messages can land in the same millisecond. A timestamp-only cursor
    would drop one or serve it twice at the page boundary."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.collection import CollectionMessage
    from app.database import async_session

    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    owner_id = uuid.UUID(await _me(client, owner))

    # Six messages, all sharing one instant — the pathological case.
    stamp = datetime.now(timezone.utc)
    async with async_session() as s:
        for i in range(6):
            s.add(CollectionMessage(
                collection_id=uuid.UUID(cid), book_id=uuid.UUID(book),
                sender_id=owner_id, body=f"m{i}", created_at=stamp,
            ))
        await s.commit()

    seen, params = [], {"limit": 2}
    for _ in range(5):
        page = (await _read(client, owner, cid, **params)).json()
        seen.extend(m["id"] for m in page["messages"])
        if not page["next_before"]:
            break
        params = {"limit": 2, "before": page["next_before"], "before_id": page["next_before_id"]}

    assert len(seen) == 6
    assert len(set(seen)) == 6   # nothing repeated

    async with async_session() as s:
        stored = (await s.execute(
            select(CollectionMessage.id).where(CollectionMessage.collection_id == uuid.UUID(cid))
        )).scalars().all()
    assert set(seen) == {str(i) for i in stored}   # nothing skipped


# ── Lifecycle ──

async def test_deleting_the_collection_takes_the_conversation_with_it(client):
    from sqlalchemy import select
    from app.models.collection import CollectionMessage
    from app.database import async_session

    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    await _say(client, owner, cid, "ephemeral")

    await client.delete(f"/api/collections/{cid}", headers=owner)

    async with async_session() as s:
        rows = (await s.execute(select(CollectionMessage))).scalars().all()
    assert rows == []


async def test_a_book_added_through_the_editor_is_talkable(client):
    """The integration that was broken: the collections editor added items by
    `entry_id`, which leaves `book_id` NULL — so the book existed on the shelf
    card but the discussion said "add a book to this collection". Adding by book
    is what makes a conversation reachable.
    """
    owner = await _auth(client, "o@example.com", "owner")
    cid = (await client.post("/api/collections", json={"title": "Ruined me"},
                             headers=owner)).json()["id"]
    book = await _book(client, owner, "Mistborn")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner)

    rows = (await client.get(f"/api/collections/{cid}/conversations", headers=owner)).json()
    assert [r["title"] for r in rows] == ["Mistborn"]
    assert (await _say(client, owner, cid, "the ending")).status_code == 201


async def test_a_member_added_book_shows_on_the_owners_collection(client):
    """A book a member added has no entry in the OWNER's library. Building the
    collection card from `entry_id` alone made it invisible to its own owner.
    """
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, _seed = await _room(client, owner, friend)

    theirs = await _book(client, friend, "The Employees")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": theirs}, headers=friend)

    profile = (await client.get("/api/me/profile", headers=owner)).json()
    collection = next(c for c in profile["collections"] if c["id"] == cid)
    assert "The Employees" in [b["title"] for b in collection["books"]]


# ── A member needs a way back in ──

async def test_a_joined_collection_is_listed_for_the_member(client):
    """The gap that made #5 and #6 unreachable for anyone but the owner.

    The profile lists collections where `collections.user_id` is you, so a member
    who accepted an invite got a database row and nothing they could see. They
    had joined a room with no door.
    """
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, _seeded = await _room(client, owner, friend, title="Books that ruined me")

    rows = (await client.get("/api/collections/joined", headers=friend)).json()
    assert [r["title"] for r in rows] == ["Books that ruined me"]
    assert rows[0]["book_count"] == 1
    assert rows[0]["member_count"] == 2
    assert rows[0]["owner_handle"]


async def test_your_own_collections_are_not_listed_as_joined(client):
    """They already have a home on the profile; listing them twice would read as
    two different things."""
    owner = await _auth(client, "o@example.com", "owner")
    await _room(client, owner)

    assert (await client.get("/api/collections/joined", headers=owner)).json() == []


async def test_leaving_removes_it_from_your_joined_list(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, _seeded = await _room(client, owner, friend)
    friend_id = await _me(client, friend)

    await client.delete(f"/api/collections/{cid}/members/{friend_id}", headers=friend)
    assert (await client.get("/api/collections/joined", headers=friend)).json() == []


async def test_joined_route_is_not_swallowed_as_a_collection_id(client):
    """`/collections/joined` sits next to `/collections/{collection_id}`. If it
    were declared after, "joined" would parse as a uuid path param and 422."""
    headers = await _auth(client, "o@example.com", "owner")
    r = await client.get("/api/collections/joined", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_the_message_notification_carries_what_a_deep_link_needs(client):
    """The notification was rendering as dead text because the client had no
    route for this kind. The payload has to carry both ids for one to exist."""
    from sqlalchemy import select
    from app.models.notification import Notification
    from app.database import async_session

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    await _say(client, owner, cid, "come and look")

    async with async_session() as s:
        n = (await s.execute(
            select(Notification).where(Notification.kind == "collection_message")
        )).scalars().first()

    assert n is not None
    assert n.payload["collection_id"] == cid
    # book_id may be null now — a message need not attach one — so the deep link
    # is to the room, and the book only narrows it when present.
    assert "book_id" in n.payload


# ── One room, live ──

async def test_the_poll_returns_only_what_is_new(client):
    """The live view asks "anything since?" — a quiet room must cost almost
    nothing, and a busy one must not resend what is already on screen."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, _seeded = await _room(client, owner, friend)

    first = (await _say(client, owner, cid, "one")).json()
    (await _read(client, friend, cid)).json()

    # Nothing new yet.
    assert (await _read(client, friend, cid, after=first["created_at"])).json()["messages"] == []

    await _say(client, friend, cid, "two")
    fresh = (await _read(client, owner, cid, after=first["created_at"])).json()["messages"]
    assert [m["body"] for m in fresh] == ["two"]


async def test_the_poll_never_offers_a_backward_cursor(client):
    """It is reading the newest end. Handing back a `before` cursor would invite
    a client to page backward from the wrong place."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, _seeded = await _room(client, owner)
    m = (await _say(client, owner, cid, "one")).json()
    await _say(client, owner, cid, "two")

    page = (await _read(client, owner, cid, after=m["created_at"], limit=1)).json()
    assert page["next_before"] is None
    assert page["next_before_id"] is None


async def test_an_attached_book_carries_its_title(client):
    """So a message can render its label without the client holding the shelf."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)

    posted = (await _say(client, owner, cid, "this one", book=book)).json()
    assert posted["book_title"] == "Piranesi"

    read = (await _read(client, owner, cid)).json()["messages"][0]
    assert read["book_title"] == "Piranesi"


async def test_filtering_by_book_narrows_the_same_room(client):
    """A filter, not a separate room — unattached messages simply fall outside it."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    await _say(client, owner, cid, "general remark")
    await _say(client, owner, cid, "about the book", book=book)

    everything = (await _read(client, owner, cid)).json()["messages"]
    assert len(everything) == 2

    narrowed = (await _read(client, owner, cid, book_id=book)).json()["messages"]
    assert [m["body"] for m in narrowed] == ["about the book"]


# ── Sparks ──

async def test_sparks_are_facts_or_questions_and_never_invented(client):
    """The rest of this product refuses to fabricate claims about books. A chat
    widget is not where that stops being true."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, _seeded = await _room(client, owner)
    for t in ("The Employees", "Solenoid"):
        b = await _book(client, owner, t)
        await client.post(f"/api/collections/{cid}/books", json={"book_id": b}, headers=owner)

    sparks = (await client.get(f"/api/collections/{cid}/sparks", headers=owner)).json()["sparks"]
    assert sparks
    assert {s["kind"] for s in sparks} <= {"fact", "question"}

    facts = [s["text"] for s in sparks if s["kind"] == "fact"]
    # Every fact is countable against this collection: 3 books, 1 member.
    assert any("3 books" in f for f in facts)


async def test_sparks_are_members_only(client):
    owner = await _auth(client, "o@example.com", "owner")
    stranger = await _auth(client, "s@example.com", "stranger")
    cid, _seeded = await _room(client, owner)

    assert (await client.get(f"/api/collections/{cid}/sparks", headers=stranger)).status_code == 404
