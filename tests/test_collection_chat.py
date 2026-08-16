"""Collection chat (#6): talk about one book, inside one collection.

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


async def _say(client, headers, cid, book, body):
    return await client.post(f"/api/collections/{cid}/books/{book}/messages",
                             json={"body": body}, headers=headers)


async def _read(client, headers, cid, book, **params):
    r = await client.get(f"/api/collections/{cid}/books/{book}/messages",
                         params=params, headers=headers)
    return r


# ── The ordinary path ──

async def test_members_can_talk_about_a_book(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    assert (await _say(client, owner, cid, book, "this wrecked me")).status_code == 201
    assert (await _say(client, friend, cid, book, "the ending especially")).status_code == 201

    body = (await _read(client, friend, cid, book)).json()
    assert [m["body"] for m in body["messages"]] == ["this wrecked me", "the ending especially"]
    assert [m["is_mine"] for m in body["messages"]] == [False, True]


async def test_a_stranger_cannot_read_or_post(client):
    owner = await _auth(client, "o@example.com", "owner")
    stranger = await _auth(client, "s@example.com", "stranger")
    cid, book = await _room(client, owner)
    await _say(client, owner, cid, book, "hello")

    assert (await _read(client, stranger, cid, book)).status_code == 404
    assert (await _say(client, stranger, cid, book, "hi")).status_code == 404


async def test_conversations_list_includes_books_nobody_has_spoken_about(client):
    """The list shows where a conversation COULD start, not only where one has."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    quiet = await _book(client, owner, "The Employees")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": quiet}, headers=owner)
    await _say(client, owner, cid, book, "only here")

    rows = (await client.get(f"/api/collections/{cid}/conversations", headers=owner)).json()
    by_id = {r["book_id"]: r for r in rows}
    assert by_id[book]["message_count"] == 1
    assert by_id[quiet]["message_count"] == 0
    assert by_id[quiet]["last_message_at"] is None


# ── The book is the anchor ──

async def test_cannot_talk_about_a_book_the_collection_does_not_hold(client):
    """Otherwise a member could open conversations the collection has no record of."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, _in_room = await _room(client, owner)
    elsewhere = await _book(client, owner, "Some Other Book")

    r = await _say(client, owner, cid, elsewhere, "hi")
    assert r.status_code == 400
    assert (await _read(client, owner, cid, elsewhere)).status_code == 404


async def test_removing_a_book_hides_the_room_but_keeps_the_words(client):
    """One member tidying a shelf must not delete everyone else's writing."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    await _say(client, friend, cid, book, "a thing I said")

    await client.delete(f"/api/collections/{cid}/books/{book}", headers=owner)
    assert (await _read(client, friend, cid, book)).status_code == 404
    assert (await _say(client, friend, cid, book, "still here?")).status_code == 400

    # Put it back: the conversation returns intact.
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner)
    body = (await _read(client, friend, cid, book)).json()
    assert [m["body"] for m in body["messages"]] == ["a thing I said"]


# ── Blocks: hide the people, keep the room ──

async def test_a_block_hides_both_ways_without_evicting_anyone(client):
    """In a 1:1 thread a block ends the conversation. Here it must not throw
    either party out of a collection they both belong to."""
    owner = await _auth(client, "o@example.com", "owner")
    a = await _auth(client, "a@example.com", "areader")
    b = await _auth(client, "b@example.com", "breader")
    cid, book = await _room(client, owner, a, b)

    await _say(client, owner, cid, book, "from the owner")
    await _say(client, a, cid, book, "from A")
    await _say(client, b, cid, book, "from B")

    await _block(client, a, b)

    a_sees = [m["body"] for m in (await _read(client, a, cid, book)).json()["messages"]]
    b_sees = [m["body"] for m in (await _read(client, b, cid, book)).json()["messages"]]

    assert "from B" not in a_sees      # A blocked B
    assert "from A" not in b_sees      # and it cuts both ways
    assert "from the owner" in a_sees  # everyone else is untouched
    assert "from the owner" in b_sees

    # Neither was evicted, and both can still speak.
    assert (await _say(client, a, cid, book, "A again")).status_code == 201
    assert (await _say(client, b, cid, book, "B again")).status_code == 201


async def test_a_blocked_members_post_is_not_surfaced_as_activity(client):
    """"Someone said something" must not point at a message the viewer can't read."""
    owner = await _auth(client, "o@example.com", "owner")
    b = await _auth(client, "b@example.com", "breader")
    cid, book = await _room(client, owner, b)

    await _block(client, owner, b)
    await _say(client, b, cid, book, "unreadable to the owner")

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

    r = await _say(client, owner, cid, book, "i will kill you")
    assert r.status_code == 422
    assert "can't be sent" in r.json()["detail"]

    # And it really is not in the room.
    assert (await _read(client, friend, cid, book)).json()["messages"] == []


async def test_a_message_that_sounds_like_crisis_sends_and_returns_resources(client):
    """Care, not punishment — the same stance Echo and resonance take."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)

    r = await _say(client, owner, cid, book, "i want to kill myself")
    assert r.status_code == 201
    assert r.json()["crisis"] is not None

    # It sent — and the resources went to the sender only, not into the room.
    seen = (await _read(client, friend, cid, book)).json()["messages"]
    assert len(seen) == 1
    assert seen[0]["crisis"] is None


async def test_contact_details_are_allowed_in_a_private_room(client):
    """Echo holds these because it is public. A group who chose each other
    swapping details is the room working."""
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)
    assert (await _say(client, owner, cid, book, "mail me at a@b.com")).status_code == 201


async def test_empty_and_oversized_bodies_are_rejected(client):
    owner = await _auth(client, "o@example.com", "owner")
    cid, book = await _room(client, owner)

    # Whitespace clears the schema's min_length but is empty once trimmed, so
    # the service is what has to catch it.
    assert (await _say(client, owner, cid, book, "   ")).status_code == 400
    assert (await _say(client, owner, cid, book, "x" * 2001)).status_code == 422


# ── Leaving ──

async def test_leaving_keeps_your_words_in_the_room(client):
    """Consistent with #5: a departing member's books stay, and so do their words."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid, book = await _room(client, owner, friend)
    await _say(client, friend, cid, book, "said before leaving")

    friend_id = await _me(client, friend)
    assert (await client.delete(f"/api/collections/{cid}/members/{friend_id}", headers=friend)).status_code == 204

    body = (await _read(client, owner, cid, book)).json()
    assert [m["body"] for m in body["messages"]] == ["said before leaving"]
    # And they can no longer read or post.
    assert (await _read(client, friend, cid, book)).status_code == 404


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

    await _say(client, owner, cid, book, "anyone there")

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

    await _say(client, owner, cid, book, "started a thread")

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

    mine = (await _say(client, friend, cid, book, "mine")).json()["id"]
    theirs = (await _say(client, owner, cid, book, "theirs")).json()["id"]

    # A member cannot rewrite the record of what everyone else said.
    assert (await client.delete(f"/api/collections/{cid}/messages/{theirs}", headers=friend)).status_code == 403
    # But can take back their own.
    assert (await client.delete(f"/api/collections/{cid}/messages/{mine}", headers=friend)).status_code == 204
    # And the owner can clean up the room.
    assert (await client.delete(f"/api/collections/{cid}/messages/{theirs}", headers=owner)).status_code == 204

    assert (await _read(client, owner, cid, book)).json()["messages"] == []


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
        page = (await _read(client, owner, cid, book, **params)).json()
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
    await _say(client, owner, cid, book, "ephemeral")

    await client.delete(f"/api/collections/{cid}", headers=owner)

    async with async_session() as s:
        rows = (await s.execute(select(CollectionMessage))).scalars().all()
    assert rows == []
