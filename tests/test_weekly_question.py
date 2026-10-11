"""Digest phase 2, section 6: the weekly question."""

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services import digest_copy as copy
from app.services.digest_service import question_for, previous_period

NOW = datetime(2026, 10, 11, 9, 5, tzinfo=timezone.utc)       # Sunday: week 2026-41
LAST_WEEK = NOW - timedelta(days=7)                            # week 2026-40


def test_one_question_a_week_for_everyone_cycling_through_all():
    seen = [question_for(f"2026-{w:02d}")["id"] for w in range(1, len(copy.QUESTIONS) + 1)]
    assert len(set(seen)) == len(copy.QUESTIONS)
    assert previous_period("2026-41") == "2026-40" and previous_period("2026-01") == "2025-52"


def test_the_question_rides_along_and_results_need_three():
    from app.services.digest_service import build_digest
    for shared, has_results in ((2, False), (3, True)):
        d = build_digest(name="A", books=[], v2={"enough": False, "needed": 5}, history=[],
                         period="2026-41", today=NOW.date(), question_answers=shared)
        q = next(s for s in d["sections"] if s["kind"] == "weekly_question")
        assert q["data"]["question"] == question_for("2026-41")["text"]
        assert ("results_period" in q["data"]) is has_results
        assert ("Last week, three readers answered" in q["body"]) is has_results
        assert not re.search(r"\b0\b|\bzero\b", q["body"])


# ── through the API ───────────────────────────────────────────────────────────

async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": f"wq_{name}", "password": "hunter2pass"})
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _admin(client, db, name="boss"):
    from sqlalchemy import update
    from app.models.user import User
    await _user(client, name)
    await db.execute(update(User).where(User.username == f"wq_{name}").values(is_admin=True))
    await db.commit()
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_answering_privacy_and_results(client, db):
    from app.services.digest_service import run_due_digests

    readers = [await _user(client, n) for n in ("a", "b", "c", "d")]
    await run_due_digests(LAST_WEEK)          # everyone gets week 40's digest and its question

    # Answering needs a digest with a question; an empty answer is refused.
    assert (await client.post("/api/digest/1999-01/answer", headers=readers[0], json={"book_title": "X"})).status_code == 404
    assert (await client.post("/api/digest/2026-40/answer", headers=readers[0], json={})).status_code == 422

    # A book from your own shelf fills itself in; someone else's can't be used.
    r = await client.post("/api/entries", headers=readers[0], json={"title": "Piranesi", "author": "Susanna Clarke", "intensity": 7})
    entry = r.json()["id"]
    r = await client.post("/api/digest/2026-40/answer", headers=readers[0],
                          json={"entry_id": entry, "note": "the statues, the tides", "share": True})
    assert r.json() == {"book_title": "Piranesi", "book_author": "Susanna Clarke",
                        "note": "the statues, the tides", "share": True, "status": "pending"}
    assert (await client.post("/api/digest/2026-40/answer", headers=readers[1], json={"entry_id": entry})).status_code == 422

    # Sharing is off unless ticked.
    r = await client.post("/api/digest/2026-40/answer", headers=readers[1], json={"book_title": "Piranesi"})
    assert r.json()["share"] is False
    await client.post("/api/digest/2026-40/answer", headers=readers[2], json={"book_title": "piranesi ", "share": True})

    # Two shared so far: next week shows nothing yet.
    await run_due_digests(NOW)
    page = (await client.get("/api/digest/2026-41", headers=readers[0])).json()
    q = next(s for s in page["sections"] if s["kind"] == "weekly_question")
    assert q["reveal"]["results"] is None and "results_period" not in q["data"]


async def test_results_show_books_and_only_approved_notes_anonymously(client, db):
    from app.services.digest_service import run_due_digests

    readers = [await _user(client, n) for n in ("p", "q", "r", "s")]
    admin = await _admin(client, db)
    await run_due_digests(LAST_WEEK)
    answers = [("Piranesi", "a house of endless halls", True), ("Piranesi", "secret: it's me", True),
               ("Circe", None, True), ("Gilead", "private thoughts", False)]
    for h, (title, note, share) in zip(readers, answers):
        await client.post("/api/digest/2026-40/answer", headers=h, json={"book_title": title, "note": note, "share": share})

    pending = (await client.get("/api/admin/answers/pending", headers=admin)).json()
    assert {p["note"] for p in pending} == {"a house of endless halls", "secret: it's me"}   # not the unshared one
    assert all("user" not in str(k) for p in pending for k in p)
    first = next(p for p in pending if p["note"] == "a house of endless halls")
    assert (await client.post(f"/api/admin/answers/{first['id']}/review", headers=admin, json={"approve": True})).status_code == 200
    second = next(p for p in pending if p["note"] == "secret: it's me")
    await client.post(f"/api/admin/answers/{second['id']}/review", headers=admin, json={"approve": False})

    await run_due_digests(NOW)
    page = (await client.get("/api/digest/2026-41", headers=readers[3])).json()
    q = next(s for s in page["sections"] if s["kind"] == "weekly_question")
    res = q["reveal"]["results"]
    assert res["count"] == 3                                      # the unshared answer isn't counted
    assert res["books"][0] == {"title": "Piranesi", "author": None, "count": 2}
    assert [n["note"] for n in res["notes"]] == ["a house of endless halls"]   # rejected and unshared stay hidden
    assert "wq_" not in str(res)                                  # nobody is named

    # Editing an approved note sends it back for review.
    r = await client.post("/api/digest/2026-40/answer", headers=readers[0],
                          json={"book_title": "Piranesi", "note": "changed my mind", "share": True})
    assert r.json()["status"] == "pending"
