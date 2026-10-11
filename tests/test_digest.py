"""Weekly digest: the spec's acceptance criteria, one test per rule where possible.

The first half is pure (build_digest, no database). The second half drives the
real delivery path (in-app notification + push) against Postgres.
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import digest_copy as copy
from app.services.digest_service import Book, build_digest, due_at, period_of

TODAY = date(2026, 10, 11)            # a Sunday
NOW = datetime(2026, 10, 11, 9, 5, tzinfo=timezone.utc)
NOTE = "the lighthouse chapter undid me completely and I still think about the keeper"


def _book(i, *, days_ago=40, status="finished", emotions=None, note=None, finished=True):
    when = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc)
    return Book(id=str(uuid.UUID(int=i + 1)), title=f"Book {i + 1}", status=status, created_at=when,
                finished_at=when.date() if finished else None, note=note,
                emotions=emotions if emotions is not None else {"grief": 7, "longing": 5, "awe": 4})


ENOUGH = {"enough": True, "archetype": {"id": "grief_romantic", "name": "The Grief Romantic"},
          "profiles": {"current": {"grief": 0.4, "longing": 0.35, "awe": 0.25},
                       "enduring": {"grief": 0.45, "longing": 0.25, "awe": 0.3}},
          "leaning": {"id": "quiet_witness"}}
NOT_ENOUGH = {"enough": False, "needed": 5}


def _build(books, v2, history=(), today=TODAY):
    return build_digest(name="Ada", books=books, v2=v2, history=list(history),
                        period=period_of(datetime.combine(today, datetime.min.time())), today=today)


def _personas():
    return {
        "new": ([], NOT_ENOUGH),
        "one_book": ([_book(0, days_ago=3)], NOT_ENOUGH),
        "twenty_quiet": ([_book(i, days_ago=20 + i * 9) for i in range(20)], ENOUGH),
        "active": ([_book(i, days_ago=1 + i * 6, note=NOTE if i % 3 == 0 else None) for i in range(40)], ENOUGH),
    }


def _all_text(d):
    """Everything a reader can see: notification, page, action."""
    parts = [d["subject"], d.get("intro") or "", d["action"]["label"]]
    for s in d["sections"]:
        parts += [s["title"], s["body"], s["tease"], s.get("button", "")]
    return "\n".join(parts)


PERSONAS = ["new", "one_book", "twenty_quiet", "active"]


# ── Every reader gets a good digest ───────────────────────────────────────────

@pytest.mark.parametrize("persona", PERSONAS)
def test_every_persona_gets_a_full_digest(persona):
    books, v2 = _personas()[persona]
    d = _build(books, v2)
    assert d["subject"] and d["lead"]
    assert len(d["sections"]) >= 2          # a lead plus at least one smaller
    assert d["action"]["link"].startswith("/")


@pytest.mark.parametrize("persona", ["new", "one_book"])
def test_new_readers_lead_with_dna_waiting_to_add_book(persona):
    books, v2 = _personas()[persona]
    d = _build(books, v2)
    assert d["lead"] == "dna_waiting"
    assert d["sections"][0]["link"] == "/?add=1"


def test_revealed_is_sent_exactly_once_after_the_archetype_arrives():
    books, _ = _personas()["twenty_quiet"]
    waited = [{"lead": "dna_waiting"}]
    assert _build(books, ENOUGH, waited)["lead"] == "revealed"
    assert _build(books, ENOUGH, [{"lead": "revealed"}, *waited])["lead"] != "revealed"
    # Had an archetype before the digest ever ran: nothing to reveal.
    assert _build(books, ENOUGH, [])["lead"] != "revealed"


def test_a_quiet_week_is_still_warm_and_full():
    books, v2 = _personas()["twenty_quiet"]
    d = _build(books, v2)
    opening = d.get("intro") or d["sections"][0]["body"]
    assert any(opening.startswith(o) for o in copy.FALLOW_OPENERS)


# ── Tone ──────────────────────────────────────────────────────────────────────

BANNED = re.compile(r"\b0\b|\bzero\b|0 books|didn.t read|miss you", re.I)


@pytest.mark.parametrize("persona", PERSONAS)
def test_no_digest_shames_inactivity(persona):
    books, v2 = _personas()[persona]
    for weeks_at_zero in range(8):
        d = _build(books, v2, [{"zero_books": True, "lead": "dna_waiting"}] * weeks_at_zero)
        if not d.get("skipped"):
            assert not BANNED.search(_all_text(d)), BANNED.search(_all_text(d))


@pytest.mark.parametrize("persona", PERSONAS)
def test_one_main_button_and_one_held_back_reveal(persona):
    books, v2 = _personas()[persona]
    d = _build(books, v2)
    lead, *smaller = d["sections"]
    assert lead.get("button") and not any(s.get("button") for s in smaller)
    # The notification shows the lead's tease; the page reveals the rest.
    assert lead["tease"] != lead["body"] or lead["kind"] in ("past_self", "drift")


# ── Privacy ───────────────────────────────────────────────────────────────────

def test_note_text_is_never_copied_into_the_digest():
    books, v2 = _personas()["active"]
    d = _build(books, v2)
    assert any(s["kind"] == "past_self" for s in d["sections"])
    # Not in the stored payload either: the page reads the note live, for its owner.
    assert "lighthouse" not in repr(d)


# ── Variety ───────────────────────────────────────────────────────────────────

def test_an_archetype_letter_is_never_sent_twice():
    books, v2 = _personas()["twenty_quiet"]
    seen, history = [], []
    for _ in range(6):
        d = _build(books, v2, history)
        if d["letter_id"]:
            assert d["letter_id"] not in seen
            seen.append(d["letter_id"])
        history.insert(0, {"lead": d["lead"], "subject_id": d["subject_id"], "letter_id": d["letter_id"]})
    assert seen


def test_the_same_lead_does_not_repeat_when_another_is_available():
    books, v2 = _personas()["active"]
    first = _build(books, v2)
    second = _build(books, v2, [{"lead": first["lead"], "subject_id": first["subject_id"]}])
    assert second["lead"] != first["lead"]


def test_subjects_do_not_repeat_within_eight_weeks():
    books, v2 = _personas()["new"]           # always leads with dna_waiting
    history = []
    for _ in range(len(copy.SUBJECTS["dna_waiting"])):
        d = _build(books, v2, history)
        assert d["subject_id"] not in {h["subject_id"] for h in history[:8]}
        history.insert(0, {"lead": d["lead"], "subject_id": d["subject_id"], "zero_books": False})


def test_after_six_weeks_at_zero_it_goes_every_other_week():
    sent, history = [], []
    for _ in range(12):
        d = _build([], NOT_ENOUGH, history)
        sent.append(not d.get("skipped"))
        history.insert(0, {"lead": d.get("lead"), "zero_books": True, "subject_id": d.get("subject_id")})
    assert sent[:7] == [True] * 7
    assert sent[7:] == [False, True, False, True, False]


def test_each_week_at_zero_reveals_a_new_clue_in_the_app():
    bodies = [_build([], NOT_ENOUGH, [{"zero_books": True, "lead": "dna_waiting"}] * w)["sections"][0]["body"]
              for w in range(3)]
    assert len(set(bodies)) == 3
    assert copy.WAITING_CLUES[0] not in _build([], NOT_ENOUGH)["sections"][0]["tease"]


# ── Copy completeness ─────────────────────────────────────────────────────────

def test_copy_check_names_every_unwritten_feeling_and_archetype(monkeypatch):
    from app.services.digest_service import ARCHETYPES, EMOTION_SLUGS, missing_copy

    monkeypatch.setitem(copy.EMOTION_ESSAYS, "awe", "TODO(shruti): awe")
    monkeypatch.delitem(copy.ARCHETYPE_LETTERS, "world_diver")
    missing = missing_copy()
    assert "EMOTION_ESSAYS[awe]" in missing
    assert any(m.startswith("ARCHETYPE_LETTERS[world_diver]") for m in missing)
    # It checks the live vocabulary, not a copy of it.
    assert len(EMOTION_SLUGS) == 21 and len(ARCHETYPES) == 12


async def test_the_scheduler_refuses_to_start_with_unwritten_copy(monkeypatch):
    import asyncio
    from app.services import background, digest_service

    monkeypatch.setattr(digest_service, "missing_copy", lambda: ["EMOTION_ESSAYS[awe]"])
    await asyncio.wait_for(background.run_weekly_digest_forever(interval=0), 1)   # returns at once


def test_due_at_defaults_to_sunday_nine():
    assert due_at(None, NOW) == datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    assert due_at(None, NOW - timedelta(minutes=10)) == datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)


# ── Delivery (Postgres) ───────────────────────────────────────────────────────

async def _user(client, name):
    await client.post("/api/auth/register", json={
        "email": f"{name}@example.com", "username": f"dg_{name}", "password": "hunter2pass"})
    r = await client.post("/api/auth/login", json={"email": f"{name}@example.com", "password": "hunter2pass"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _digest_notices(client, headers):
    notes = (await client.get("/api/notifications", headers=headers)).json()["notifications"]
    return [n for n in notes if n["kind"] == "weekly_digest"]


@pytest.fixture
def pushes(monkeypatch):
    """Every push the digest asks for, as (kind, payload)."""
    from app.services import notification_service

    sent = []

    async def fake_push(db, user_id, kind, payload):
        sent.append((kind, payload))
        return 1

    monkeypatch.setattr(notification_service, "push_to_user", fake_push)
    return sent


async def test_delivered_sunday_9am_local_once_and_skipped_when_36h_late(client, pushes):
    from app.services.digest_service import run_due_digests

    h = await _user(client, "aa")
    assert await run_due_digests(NOW - timedelta(minutes=10)) == 0     # not yet due this week
    assert await run_due_digests(NOW) == 1
    assert await run_due_digests(NOW + timedelta(hours=1)) == 0        # same week: once
    [notice] = await _digest_notices(client, h)
    assert notice["payload"]["subject"] and notice["payload"]["tease"]
    assert [kind for kind, _ in pushes] == ["weekly_digest"]

    await _user(client, "bb")
    assert await run_due_digests(NOW + timedelta(hours=37)) == 0       # bb's week is too late now


async def test_push_lands_on_that_weeks_digest_and_says_nothing_private():
    from app.services.push_service import _payload

    p = _payload("weekly_digest", {"period": "2026-41", "subject": "Something you wrote about Piranesi"})
    assert p["url"] == "/digest/2026-41"
    assert "Piranesi" not in p["body"] and "Piranesi" not in p["title"]


async def test_chosen_day_hour_and_timezone_are_honoured(client, pushes):
    from app.services.digest_service import run_due_digests

    h = await _user(client, "tz")
    r = await client.patch("/api/notifications/preferences", headers=h,
                           json={"timezone": "Asia/Kolkata", "digest_day": 2, "digest_hour": 7})
    assert r.status_code == 200 and r.json()["digest_day"] == 2
    wed_7am_ist = datetime(2026, 10, 14, 1, 30, tzinfo=timezone.utc)
    assert await run_due_digests(wed_7am_ist - timedelta(minutes=5)) == 0
    assert await run_due_digests(wed_7am_ist + timedelta(minutes=5)) == 1


async def test_switched_off_means_no_digest(client, pushes):
    from app.services.digest_service import run_due_digests

    h = await _user(client, "off")
    await client.patch("/api/notifications/preferences", headers=h, json={"digest_enabled": False})
    assert await run_due_digests(NOW) == 0
    assert await _digest_notices(client, h) == []


async def test_a_failed_build_keeps_nothing_and_is_retried_next_tick(client, pushes, monkeypatch):
    from app.services import digest_service

    h = await _user(client, "rt")
    real = digest_service.build_digest
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("DNA cache exploded")
        return real(**kw)

    monkeypatch.setattr(digest_service, "build_digest", flaky)
    assert await digest_service.run_due_digests(NOW) == 0              # failed, nothing claimed
    assert await _digest_notices(client, h) == []
    assert await digest_service.run_due_digests(NOW + timedelta(minutes=15)) == 1   # next tick
    assert len(await _digest_notices(client, h)) == 1


async def test_a_failed_push_is_retried_by_the_sweeper(client, monkeypatch):
    from app.services import notification_service
    from app.services.digest_service import run_due_digests
    from app.database import async_session

    await _user(client, "ps")
    attempts = []

    async def flaky_push(db, user_id, kind, payload):
        attempts.append(kind)
        if len(attempts) == 1:
            raise RuntimeError("push service down")
        return 1

    monkeypatch.setattr(notification_service, "push_to_user", flaky_push)
    assert await run_due_digests(datetime.now(timezone.utc), force=True) == 1
    async with async_session() as db:
        async with db.begin():
            assert await notification_service.sweep_deferred_pushes(db) == 1
    assert attempts == ["weekly_digest", "weekly_digest"]


async def test_digest_page_reveals_and_clicks_are_counted_once(client, pushes, db):
    from sqlalchemy import select
    from app.models.notification import DigestClick
    from app.services.digest_service import run_due_digests

    h = await _user(client, "pg")
    await run_due_digests(NOW)
    period = period_of(due_at(None, NOW))
    page = (await client.get(f"/api/digest/{period}", headers=h)).json()
    assert page["subject"] and all("tease" not in s for s in page["sections"])

    for _ in range(2):
        r = await client.post(f"/api/digest/{period}/click", headers=h, json={"section": "open"})
        assert r.status_code == 204
    await client.post(f"/api/digest/{period}/click", headers=h, json={"section": page["sections"][0]["kind"]})
    clicks = (await db.execute(select(DigestClick.section))).scalars().all()
    assert sorted(clicks) == sorted(["open", page["sections"][0]["kind"]])

    other = await _user(client, "pg2")
    assert (await client.get(f"/api/digest/{period}", headers=other)).status_code == 404
    assert (await client.post(f"/api/digest/{period}/click", headers=other, json={"section": "open"})).status_code == 404


async def test_admin_can_deliver_now_once_and_preview_without_writing(client, pushes, db):
    from sqlalchemy import func, select, update
    from app.models.notification import NotificationDigest
    from app.models.user import User

    await _user(client, "ad")
    await db.execute(update(User).where(User.username == "dg_ad").values(is_admin=True))
    await db.commit()
    tok = (await client.post("/api/auth/login", json={"email": "ad@example.com", "password": "hunter2pass"})).json()["access_token"]
    admin = {"Authorization": f"Bearer {tok}"}

    preview = (await client.get("/api/admin/digest/preview", headers=admin)).json()
    assert preview["readers"][0]["digest"]["subject"] and preview["missing_copy"] == []
    assert (await db.execute(select(func.count(NotificationDigest.id)))).scalar() == 0

    assert (await client.post("/api/admin/jobs/weekly-digest", headers=admin)).json()["digests_sent"] == 1
    assert (await client.post("/api/admin/jobs/weekly-digest", headers=admin)).json()["digests_sent"] == 0


async def test_admin_clicks_report_the_rate_per_section(client, pushes, db):
    from sqlalchemy import update
    from app.models.user import User
    from app.services.digest_service import run_due_digests

    a = await _user(client, "ca")
    await _user(client, "cb")
    await _user(client, "cadmin")
    await db.execute(update(User).where(User.username == "dg_cadmin").values(is_admin=True))
    await db.commit()
    tok = (await client.post("/api/auth/login", json={"email": "cadmin@example.com", "password": "hunter2pass"})).json()["access_token"]
    admin = {"Authorization": f"Bearer {tok}"}
    await run_due_digests(datetime.now(timezone.utc), force=True)
    period = period_of(datetime.now(timezone.utc))
    page = (await client.get(f"/api/digest/{period}", headers=a)).json()
    lead = page["sections"][0]["kind"]
    for section in ("open", lead):
        await client.post(f"/api/digest/{period}/click", headers=a, json={"section": section})

    r = (await client.get("/api/admin/digest/clicks", headers=admin)).json()
    assert r["digests"] == 3 and r["opened"] == 1
    row = next(s for s in r["sections"] if s["kind"] == lead)
    assert row["shown"] == 3 and row["clicked"] == 1 and row["led"] == 3 and row["led_clicked"] == 1
    assert "dg_" not in str(r)                     # aggregates only
