"""Shared collections (#5): membership, invite links, and book-identity items.

The theme under test: a collection stops being one reader's private list and
becomes a place several readers add to. Most of these tests are about what a
member must NOT be able to do.
"""

import pytest

pytestmark = pytest.mark.asyncio


async def _auth(client, email, username):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _book(client, headers, title="Piranesi"):
    """A canonical book id, via an entry (which find-or-creates the catalog row)."""
    r = await client.post("/api/entries", json={
        "title": title, "intensity": 7, "emotions": [],
    }, headers=headers)
    return r.json()["book_id"]


async def _collection(client, headers, title="Group Read"):
    r = await client.post("/api/collections", json={"title": title}, headers=headers)
    return r.json()["id"]


async def _invite(client, headers, cid, **body):
    r = await client.post(f"/api/collections/{cid}/invites", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ── Invites ──

async def test_invite_token_is_returned_once_and_never_stored_raw(client, db):
    """A DB leak must not yield live invite links — only the SHA-256 is stored."""
    from sqlalchemy import select
    from app.models.collection import CollectionInvite

    owner = await _auth(client, "o@example.com", "owner")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)

    rows = (await db.execute(select(CollectionInvite))).scalars().all()
    assert len(rows) == 1
    assert token not in rows[0].token_hash
    assert len(rows[0].token_hash) == 64


async def test_join_by_link_makes_a_member(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)

    r = await client.post(f"/api/collections/invites/{token}/join", headers=friend)
    assert r.status_code == 200, r.text
    assert r.json()["joined"] is True
    assert r.json()["collection_id"] == cid

    members = (await client.get(f"/api/collections/{cid}/members", headers=friend)).json()
    assert {m["role"] for m in members} == {"owner", "member"}


async def test_clicking_the_link_twice_is_not_an_error(client):
    """People re-click links. The second one must not error, duplicate the
    membership, or burn a use."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid, max_uses=1)

    first = await client.post(f"/api/collections/invites/{token}/join", headers=friend)
    second = await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    assert first.json()["joined"] is True
    assert second.status_code == 200
    assert second.json()["joined"] is False

    members = (await client.get(f"/api/collections/{cid}/members", headers=friend)).json()
    assert len(members) == 2


async def test_max_uses_counts_joins_not_clicks(client):
    """A one-use link spent by the same person clicking twice would lock out the
    person it was actually for."""
    owner = await _auth(client, "o@example.com", "owner")
    a = await _auth(client, "a@example.com", "areader")
    b = await _auth(client, "b@example.com", "breader")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid, max_uses=1)

    await client.post(f"/api/collections/invites/{token}/join", headers=a)
    await client.post(f"/api/collections/invites/{token}/join", headers=a)  # re-click
    r = await client.post(f"/api/collections/invites/{token}/join", headers=b)

    assert r.status_code == 404  # the single use went to `a`, as intended


async def test_revoked_link_stops_working_but_members_stay(client):
    """Revoking is about the door, not the people who already came through it."""
    owner = await _auth(client, "o@example.com", "owner")
    early = await _auth(client, "e@example.com", "early")
    late = await _auth(client, "l@example.com", "late")
    cid = await _collection(client, owner)

    r = await client.post(f"/api/collections/{cid}/invites", json={}, headers=owner)
    token, invite_id = r.json()["token"], r.json()["id"]
    await client.post(f"/api/collections/invites/{token}/join", headers=early)

    await client.delete(f"/api/collections/{cid}/invites/{invite_id}", headers=owner)

    assert (await client.post(f"/api/collections/invites/{token}/join", headers=late)).status_code == 404
    members = (await client.get(f"/api/collections/{cid}/members", headers=early)).json()
    assert len(members) == 2  # `early` was not evicted


async def test_expired_link_is_refused(client):
    from datetime import datetime, timedelta, timezone

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token = await _invite(client, owner, cid, expires_at=past)

    assert (await client.post(f"/api/collections/invites/{token}/join", headers=friend)).status_code == 404


async def test_only_the_owner_can_mint_an_invite(client):
    """A member must not be able to invite the world into someone's collection."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    r = await client.post(f"/api/collections/{cid}/invites", json={}, headers=friend)
    assert r.status_code == 404


async def test_peek_names_the_collection_without_joining(client):
    """Nobody should have to accept a blind invitation."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner, title="Winter Reads")
    book = await _book(client, owner)
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=owner)
    token = await _invite(client, owner, cid)

    r = await client.get(f"/api/collections/invites/{token}", headers=friend)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Winter Reads"
    assert body["book_count"] == 1
    assert body["member_count"] == 1
    assert body["already_member"] is False

    # Peeking did not join anyone.
    members = (await client.get(f"/api/collections/{cid}/members", headers=owner)).json()
    assert len(members) == 1


# ── Membership as the gate ──

async def test_a_stranger_gets_404_not_403(client):
    """403 would confirm the collection exists — a non-member must not be able to
    probe which ids are real."""
    owner = await _auth(client, "o@example.com", "owner")
    stranger = await _auth(client, "s@example.com", "stranger")
    cid = await _collection(client, owner)

    assert (await client.get(f"/api/collections/{cid}/members", headers=stranger)).status_code == 404
    r = await client.post(f"/api/collections/{cid}/books",
                          json={"book_id": await _book(client, stranger)}, headers=stranger)
    assert r.status_code == 404


async def test_owner_is_a_member_from_creation(client):
    """Membership is the single gate, so the owner has to be in the table — not
    implied by collections.user_id and checked separately everywhere."""
    owner = await _auth(client, "o@example.com", "owner")
    cid = await _collection(client, owner)

    members = (await client.get(f"/api/collections/{cid}/members", headers=owner)).json()
    assert len(members) == 1
    assert members[0]["role"] == "owner"


# ── Items belong to the collection, not to one member ──

async def test_a_member_can_add_a_book(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    book = await _book(client, friend, "Piranesi")
    r = await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=friend)
    assert r.status_code == 204, r.text


async def test_two_members_adding_the_same_book_make_one_item(client):
    """The item is the BOOK, not each member's copy of it. This is the whole
    reason items moved off entry_id — two entries, one canonical book, one item.
    """
    from sqlalchemy import select
    from app.models.collection import CollectionItem

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    # Each reader logs their OWN entry for the same title; both resolve to one book.
    owner_book = await _book(client, owner, "Piranesi")
    friend_book = await _book(client, friend, "Piranesi")
    assert owner_book == friend_book

    await client.post(f"/api/collections/{cid}/books", json={"book_id": owner_book}, headers=owner)
    await client.post(f"/api/collections/{cid}/books", json={"book_id": friend_book}, headers=friend)

    from app.database import async_session
    async with async_session() as s:
        items = (await s.execute(
            select(CollectionItem).where(CollectionItem.collection_id == cid)
        )).scalars().all()
    assert len(items) == 1


async def test_a_member_cannot_remove_someone_elses_book(client):
    """Otherwise one member can quietly gut a shared collection."""
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    owners_book = await _book(client, owner, "Piranesi")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": owners_book}, headers=owner)

    r = await client.delete(f"/api/collections/{cid}/books/{owners_book}", headers=friend)
    assert r.status_code == 403


async def test_a_member_can_remove_their_own_book(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    book = await _book(client, friend, "Piranesi")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=friend)

    assert (await client.delete(f"/api/collections/{cid}/books/{book}", headers=friend)).status_code == 204


async def test_the_owner_can_remove_anyones_book(client):
    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)

    book = await _book(client, friend, "Piranesi")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=friend)

    assert (await client.delete(f"/api/collections/{cid}/books/{book}", headers=owner)).status_code == 204


# ── Leaving ──

async def test_a_member_can_leave_and_their_books_stay(client):
    """A departure must not delete books the rest of the collection is reading."""
    from sqlalchemy import select
    from app.models.collection import CollectionItem

    owner = await _auth(client, "o@example.com", "owner")
    friend = await _auth(client, "f@example.com", "friend")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=friend)
    book = await _book(client, friend, "Piranesi")
    await client.post(f"/api/collections/{cid}/books", json={"book_id": book}, headers=friend)

    friend_id = (await client.get("/api/collections/" + cid + "/members", headers=friend)).json()
    friend_uid = next(m["user_id"] for m in friend_id if m["role"] == "member")
    r = await client.delete(f"/api/collections/{cid}/members/{friend_uid}", headers=friend)
    assert r.status_code == 204

    from app.database import async_session
    async with async_session() as s:
        items = (await s.execute(
            select(CollectionItem).where(CollectionItem.collection_id == cid)
        )).scalars().all()
    assert len(items) == 1  # the book they added is still there


async def test_a_member_cannot_remove_another_member(client):
    owner = await _auth(client, "o@example.com", "owner")
    a = await _auth(client, "a@example.com", "areader")
    b = await _auth(client, "b@example.com", "breader")
    cid = await _collection(client, owner)
    token = await _invite(client, owner, cid)
    await client.post(f"/api/collections/invites/{token}/join", headers=a)
    await client.post(f"/api/collections/invites/{token}/join", headers=b)

    members = (await client.get(f"/api/collections/{cid}/members", headers=a)).json()
    b_uid = [m["user_id"] for m in members if m["role"] == "member"][-1]

    r = await client.delete(f"/api/collections/{cid}/members/{b_uid}", headers=a)
    assert r.status_code == 403


async def test_the_owner_cannot_leave_their_own_collection(client):
    """It would leave a collection with members and no one who can administer it."""
    owner = await _auth(client, "o@example.com", "owner")
    cid = await _collection(client, owner)
    members = (await client.get(f"/api/collections/{cid}/members", headers=owner)).json()
    owner_uid = members[0]["user_id"]

    r = await client.delete(f"/api/collections/{cid}/members/{owner_uid}", headers=owner)
    assert r.status_code == 403
