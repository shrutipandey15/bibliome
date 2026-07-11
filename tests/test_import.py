"""Library import tests (B2.7)."""

import pytest

pytestmark = pytest.mark.asyncio

GOODREADS_CSV = (
    "Book Id,Title,Author,ISBN,ISBN13,Exclusive Shelf,Date Read\n"
    '1,Beloved,Toni Morrison,="0","=""9781400033416""",read,2026/01/15\n'
    "2,Wanting Book,Some Author,,,to-read,\n"
    "3,Reading Now,Another Author,,,currently-reading,\n"
)

STORYGRAPH_CSV = (
    "Title,Authors,ISBN/UID,Read Status,Last Date Read\n"
    "The Overstory,Richard Powers,9780393635225,read,2026/02/01\n"
)


async def _auth(client, email="imp@example.com", username="importer"):
    await client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "hunter2pass",
    })
    r = await client.post("/api/auth/login", json={"email": email, "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _upload(client, headers, csv_text):
    return await client.post(
        "/api/entries/import",
        files={"file": ("export.csv", csv_text, "text/csv")},
        headers=headers,
    )


async def test_goodreads_import_maps_status_and_dates(client):
    headers = await _auth(client)
    r = await _upload(client, headers, GOODREADS_CSV)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parsed"] == 3
    assert body["imported"] == 3

    r = await client.get("/api/entries", headers=headers)
    entries = {e["title"]: e for e in r.json()["entries"]}
    assert entries["Beloved"]["status"] == "finished"
    assert entries["Beloved"]["finished_at"] == "2026-01-15"
    assert entries["Beloved"]["isbn"] == "9781400033416"   # unwrapped from ="..."
    assert entries["Wanting Book"]["status"] == "want_to_read"
    assert entries["Reading Now"]["status"] == "reading"


async def test_import_dedupes_against_existing_and_within_file(client):
    headers = await _auth(client)
    await _upload(client, headers, GOODREADS_CSV)
    # Re-uploading the same file imports nothing.
    r = await _upload(client, headers, GOODREADS_CSV)
    assert r.json()["imported"] == 0
    assert r.json()["skipped"] == 3


async def test_storygraph_import(client):
    headers = await _auth(client, "sg@example.com", "sguser")
    r = await _upload(client, headers, STORYGRAPH_CSV)
    assert r.status_code == 200
    assert r.json()["imported"] == 1
    r = await client.get("/api/entries?q=overstory", headers=headers)
    assert r.json()["total"] == 1
