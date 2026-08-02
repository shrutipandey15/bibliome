"""Data export — the DPDP right of access, as one JSON document.

Two rules shape what goes in.

**Everything the user authored, in a form they can actually use.** Not a
screenshot of the UI: the shelf with its emotion tags, the check-ins, the
collections, the DNA snapshots, the echoes they posted.

**The journal comes out as ciphertext, with its key bundle.** We cannot decrypt
it — that is the whole point of ``journalCryptoContract.md`` — so shipping the
wrapped DEK alongside the blobs is the only export that is actually complete.
With their password or recovery code the user can decrypt the file offline; we
never could, and the export doesn't pretend otherwise.

What is deliberately left out: anything that would export a *second* person's
data under the first person's right of access. Private thread transcripts are
the clear case — a DM has two authors, and one party cannot unilaterally
publish it. The export lists that those conversations exist and says why the
bodies aren't included.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.book_entry import BookEntry
from app.models.collection import Collection
from app.models.dna_snapshot import DNASnapshot
from app.models.echo import Echo, EchoReply
from app.models.journal import JournalEntry, JournalKeyBundle
from app.models.resonance import ResonanceMatch, ResonanceThread
from app.models.social import Block, Mute
from app.models.user import User

EXPORT_FORMAT_VERSION = 1


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


async def build_export(db: AsyncSession, user: User) -> dict[str, Any]:
    """Assemble the full export document for one user."""
    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "account": _account(user),
        "entries": await _entries(db, user.id),
        "collections": await _collections(db, user.id),
        "dna_snapshots": await _snapshots(db, user.id),
        "echoes": await _echoes(db, user.id),
        "journal": await _journal(db, user.id),
        "social": await _social(db, user.id),
    }


def _account(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "handle": user.handle,
        "display_name": user.display_name,
        "bio": user.bio,
        "profile_visibility": user.profile_visibility,
        "personality_type": user.personality_type,
        "reads_for": user.reads_for,
        "created_at": _iso(user.created_at),
    }


async def _entries(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(BookEntry)
        .options(selectinload(BookEntry.emotions), selectinload(BookEntry.checkins))
        .where(BookEntry.user_id == user_id)
        .order_by(BookEntry.created_at.asc())
    )).scalars().all()

    return [
        {
            "id": str(e.id),
            "title": e.title,
            "author": e.author,
            "isbn": e.isbn,
            "cover_url": e.cover_url,
            "status": e.status,
            "verdict": e.verdict,
            "dnf_reason": e.dnf_reason,
            "intensity": e.intensity,
            "quote": e.quote,
            "notes": e.notes,
            "finish_thought": e.finish_thought,
            "arc": {
                "start": e.arc_start_emotion_id,
                "middle": e.arc_middle_emotion_id,
                "end": e.arc_end_emotion_id,
            },
            "started_at": _iso(e.started_at),
            "finished_at": _iso(e.finished_at),
            "created_at": _iso(e.created_at),
            "emotions": [
                {"emotion_id": em.emotion_id, "strength": em.strength} for em in e.emotions
            ],
            "checkins": [
                {
                    "emotion_id": c.emotion_id,
                    "note": c.note,
                    "created_at": _iso(c.created_at),
                }
                for c in sorted(e.checkins, key=lambda c: c.created_at)
            ],
        }
        for e in rows
    ]


async def _collections(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(Collection)
        .options(selectinload(Collection.items))
        .where(Collection.user_id == user_id)
        .order_by(Collection.position.asc())
    )).scalars().all()

    return [
        {
            "id": str(c.id),
            "title": c.title,
            "description": c.description,
            "visibility": c.visibility,
            "created_at": _iso(c.created_at),
            "entry_ids": [
                str(i.entry_id) for i in sorted(c.items, key=lambda i: i.position)
            ],
        }
        for c in rows
    ]


async def _snapshots(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(DNASnapshot)
        .where(DNASnapshot.user_id == user_id)
        .order_by(DNASnapshot.generated_at.asc())
    )).scalars().all()

    return [
        {
            "personality_type": s.personality_type,
            "dna_type_slug": s.dna_type_slug,
            "book_count": s.book_count,
            "year": s.year,
            "trigger": s.trigger,
            "emotion_data": s.emotion_data,
            "generated_at": _iso(s.generated_at),
        }
        for s in rows
    ]


async def _echoes(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    posts = (await db.execute(
        select(Echo).where(Echo.author_id == user_id).order_by(Echo.created_at.asc())
    )).scalars().all()
    replies = (await db.execute(
        select(EchoReply)
        .where(EchoReply.author_id == user_id)
        .order_by(EchoReply.created_at.asc())
    )).scalars().all()

    return {
        "posts": [
            {
                "id": str(e.id),
                "body": e.body,
                "book_title": e.book_title,
                "book_author": e.book_author,
                "primary_emotion": e.primary_emotion,
                "secondary_emotion": e.secondary_emotion,
                "visibility": e.visibility,
                "status": e.status,
                "created_at": _iso(e.created_at),
            }
            for e in posts
        ],
        "replies": [
            {
                "id": str(r.id),
                "echo_id": str(r.echo_id),
                "body": r.body,
                "status": r.status,
                "created_at": _iso(r.created_at),
            }
            for r in replies
        ],
    }


async def _journal(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Ciphertext plus the wrapped key material needed to open it offline.

    Exporting the bundle is not a leak: every field in it is already inert
    without the password or recovery code, which the server has never held. It
    is what makes the export usable at all — ciphertext without the wrapped DEK
    would be a file the user can never read either.
    """
    bundle = (await db.execute(
        select(JournalKeyBundle).where(JournalKeyBundle.user_id == user_id)
    )).scalar_one_or_none()

    entries = (await db.execute(
        select(JournalEntry)
        .options(selectinload(JournalEntry.emotions))
        .where(JournalEntry.user_id == user_id)
        .order_by(JournalEntry.entry_date.asc())
    )).scalars().all()

    if bundle is None and not entries:
        return {"present": False}

    return {
        "present": True,
        "note": (
            "Entry prose is encrypted. The server cannot decrypt it and never could. "
            "Use your account password (unless password_wrap_stale is true) or your "
            "recovery code with key_bundle below to decrypt these blobs offline."
        ),
        "key_bundle": None if bundle is None else {
            "cipher": bundle.cipher,
            "kdf": bundle.kdf,
            "kdf_params": bundle.kdf_params,
            "password_salt": bundle.password_salt,
            "wrapped_dek": bundle.wrapped_dek,
            "wrapped_dek_nonce": bundle.wrapped_dek_nonce,
            "recovery_salt": bundle.recovery_salt,
            "wrapped_dek_recovery": bundle.wrapped_dek_recovery,
            "wrapped_dek_recovery_nonce": bundle.wrapped_dek_recovery_nonce,
            "key_version": bundle.key_version,
            "password_wrap_stale": bundle.password_wrap_stale,
        },
        "entries": [
            {
                "id": str(j.id),
                "entry_date": _iso(j.entry_date),
                "ciphertext": j.ciphertext,
                "nonce": j.nonce,
                "key_version": j.key_version,
                "created_at": _iso(j.created_at),
                "emotions": [
                    {"emotion_id": em.emotion_id, "strength": em.strength}
                    for em in j.emotions
                ],
            }
            for j in entries
        ],
    }


async def _social(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    blocks = (await db.execute(
        select(Block.created_at).where(Block.blocker_id == user_id)
    )).scalars().all()
    mutes = (await db.execute(
        select(Mute.created_at).where(Mute.muter_id == user_id)
    )).scalars().all()

    mine = (await db.execute(
        select(ResonanceThread)
        .join(ResonanceMatch, ResonanceMatch.id == ResonanceThread.match_id)
        .where(or_(ResonanceMatch.user_a == user_id, ResonanceMatch.user_b == user_id))
        .order_by(ResonanceThread.created_at.asc())
    )).scalars().all()

    return {
        # Counts only: who you blocked is a statement about another person, and
        # their handle is not yours to export.
        "blocks_count": len(blocks),
        "mutes_count": len(mutes),
        "resonance_threads": {
            "count": len(mine),
            "note": (
                "Message bodies are omitted. A thread has two authors and one "
                "party cannot unilaterally export the other's words. Read them "
                "in the app while the thread is open."
            ),
            "threads": [
                {"id": str(t.id), "status": t.status, "created_at": _iso(t.created_at)}
                for t in mine
            ],
        },
    }
