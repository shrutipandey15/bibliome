"""The share card through the API: one reused card link, the reader's switches,
the /s/ page's preview tags, the stored preview image, and the daily counts."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio

GRIEF = ["grief", "catharsis", "haunted"]
CRAWLER = {"User-Agent": "WhatsApp/2.23.20.0"}
PERSON = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"}


async def _user(client, name, via=None):
    body = {"email": f"{name}@example.com", "username": name, "password": "hunter2pass"}
    if via:
        body["via"] = via
    r = await client.post("/api/auth/register", json=body)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _reader(client, name, books=12):
    h = await _user(client, name)
    for i in range(books):
        r = await client.post("/api/entries", json={
            "title": f"Ache {i}", "intensity": 7, "status": "finished",
            "finished_at": (date.today() - timedelta(days=60 - i)).isoformat(),
            "emotions": [{"emotion_id": e, "strength": 7} for e in GRIEF],
        }, headers=h)
        assert r.status_code in (200, 201), r.text
    # The owner's DNA read computes the cache every public surface reads.
    assert (await client.get("/api/dna/profile", headers=h)).json()["enough"] is True
    return h


async def _counts(db):
    from app.models.card import CardCount

    rows = (await db.execute(select(CardCount))).scalars().all()
    return {(r.metric, r.key): r.n for r in rows}


async def test_the_card_link_is_made_once_and_reused(client):
    h = await _reader(client, "linker")
    assert (await client.get("/api/user/share-token", headers=h)).json()["share_token"] is None
    first = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    again = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    assert first == again
    assert (await client.get("/api/user/share-token", headers=h)).json()["share_token"] == first
    assert (await client.get(f"/api/public/shared/{first}")).status_code == 200


async def test_turning_the_link_off_kills_it_and_the_next_one_is_new(client):
    h = await _reader(client, "offer")
    assert (await client.get("/api/user/share-token", headers=h)).json()["off"] is False
    old = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    assert (await client.delete("/api/user/share-token", headers=h)).status_code == 204
    # "Off" is remembered on the account, for every device the reader uses.
    assert (await client.get("/api/user/share-token", headers=h)).json() == {"share_token": None, "off": True}
    assert (await client.get(f"/api/public/shared/{old}")).status_code == 404
    new = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    assert new != old
    assert (await client.get("/api/user/share-token", headers=h)).json()["off"] is False


async def test_owner_card_rides_the_dna_profile_with_both_switches(client):
    h = await _reader(client, "owner")
    card = (await client.get("/api/dna/profile", headers=h)).json()["card"]
    assert card["archetype"]["id"] == "grief_romantic"
    assert card["archetype"]["share_line"] == "Loss isn't my enemy. Numbness is."
    assert card["red_flag"] == "I avoid neat happy endings"
    assert card["choices"] == {"season": True, "red_flag": True}
    assert card["bloom"]["top"][0]["count"] == 12
    assert card["tagged_count"] == 12


async def test_switches_apply_to_strangers_but_not_the_owners_sheet(client):
    h = await _reader(client, "switcher")
    r = await client.patch("/api/user/settings", json={"card_show_red_flag": False}, headers=h)
    assert r.status_code == 200 and r.json()["card_show_red_flag"] is False
    assert r.json()["card_show_season"] is True
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    public = (await client.get(f"/api/public/shared/{token}")).json()
    assert public["red_flag"] is None
    assert "red_flag" not in public["archetype"]
    assert "choices" not in public
    owner = (await client.get("/api/dna/profile", headers=h)).json()["card"]
    assert owner["red_flag"] == "I avoid neat happy endings"
    assert owner["choices"]["red_flag"] is False
    # A null is "no change", never a broken column.
    r = await client.patch("/api/user/settings", json={"card_show_season": None}, headers=h)
    assert r.status_code == 200 and r.json()["card_show_season"] is True


async def test_share_page_points_crawlers_at_the_cards_own_image(client, db):
    h = await _reader(client, "previewed")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    page = (await client.get(f"/s/{token}", headers=CRAWLER)).text
    assert f"/s/{token}/card.jpg?v=" in page
    assert 'property="og:image:width" content="1200"' in page
    assert 'property="og:image:height" content="630"' in page
    assert 'property="og:image:type" content="image/jpeg"' in page
    assert "Most-felt: heartbreak, catharsis, haunted." in page
    assert 'name="twitter:image:alt"' in page
    assert "@previewed reads like a Grief Romantic" in page

    url = page.split('property="og:image" content="')[1].split('"')[0]
    path = url.replace("https://bibliome.app", "")
    img = await client.get(path, headers=CRAWLER)
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/jpeg"
    assert "immutable" in img.headers["cache-control"]
    assert len(img.content) <= 250 * 1024

    counts = await _counts(db)
    assert counts.get(("preview", "whatsapp")) == 1
    assert ("visit", "human") not in counts      # a crawler is not a visit


async def test_the_preview_is_stored_when_the_link_is_made(client, db):
    from app.models.card import CardPreview
    from app.models.user import User

    h = await _reader(client, "stored")
    await client.post("/api/user/share-token", headers=h)
    user = (await db.execute(select(User).where(User.username == "stored"))).scalar_one()
    row = (await db.execute(select(CardPreview).where(CardPreview.user_id == user.id))).scalar_one()
    assert row.jpeg[:3] == b"\xff\xd8\xff"


async def test_a_switch_changes_the_image_url(client):
    h = await _reader(client, "rehash")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]

    def v(page):
        return page.split("card.jpg?v=")[1].split('"')[0]

    before = v((await client.get(f"/s/{token}", headers=CRAWLER)).text)
    await client.patch("/api/user/handle", json={"handle": "rehashed"}, headers=h)
    after = v((await client.get(f"/s/{token}", headers=CRAWLER)).text)
    assert before != after


async def test_a_turned_off_link_previews_the_generic_image(client):
    h = await _reader(client, "revoked")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    await client.delete("/api/user/share-token", headers=h)
    page = (await client.get(f"/s/{token}")).text
    assert "card.jpg" not in page
    assert "https://bibliome.app/og-image.png" in page
    img = await client.get(f"/s/{token}/card.jpg?v=whatever", headers=CRAWLER)
    assert img.status_code == 302
    assert img.headers["location"].endswith("/og-image.png")


async def test_a_reader_without_a_card_has_no_image(client):
    h = await _user(client, "fresh")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    assert "card.jpg" not in (await client.get(f"/s/{token}")).text


async def test_people_on_share_pages_are_counted_without_who(client, db):
    h = await _reader(client, "visited")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    await client.get(f"/s/{token}", headers=PERSON)
    await client.get(f"/s/{token}", headers=PERSON)
    assert (await _counts(db))[("visit", "human")] == 2


async def test_shares_are_counted_by_format(client, db):
    h = await _reader(client, "sharer")
    for fmt in ("story", "story", "square", "link", "season"):
        r = await client.post("/api/card/shares", json={"format": fmt}, headers=h)
        assert r.status_code == 204
    assert (await client.post("/api/card/shares", json={"format": "tiktok"}, headers=h)).status_code == 422
    assert (await client.post("/api/card/shares", json={"format": "story"})).status_code in (401, 403)
    c = await _counts(db)
    assert c[("share", "story")] == 2 and c[("share", "square")] == 1
    assert c[("share", "link")] == 1 and c[("share", "season")] == 1


async def test_signups_from_find_yours_are_counted_and_not_stored(client, db):
    from app.models.user import User

    await _user(client, "arrived", via="card")
    await _user(client, "direct")
    assert (await _counts(db))[("signup", "card")] == 1
    # Nothing about where they came from is kept on the account.
    user = (await db.execute(select(User).where(User.username == "arrived"))).scalar_one()
    assert not any("via" in c.name for c in User.__table__.columns)
    assert user.username == "arrived"


async def test_a_stale_preview_is_replaced_and_served_fresh(client, db):
    from app.models.card import CardPreview
    from app.models.user import User

    h = await _reader(client, "stale")
    token = (await client.post("/api/user/share-token", headers=h)).json()["share_token"]
    user = (await db.execute(select(User).where(User.username == "stale"))).scalar_one()
    row = (await db.execute(select(CardPreview).where(CardPreview.user_id == user.id))).scalar_one()
    row.hash, row.jpeg = "old", b"not-a-jpeg"
    await db.commit()
    img = await client.get(f"/s/{token}/card.jpg", headers=CRAWLER)
    assert img.status_code == 200
    assert img.content[:3] == b"\xff\xd8\xff"
