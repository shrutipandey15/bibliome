"""Collection chat (#6) — ONE room per collection.

The first version gave every book its own room. That fragmented a small group
across a dozen mostly-empty rooms, so conversation happened in none of them. A
collection now has one room, and a message may *attach* a book — the book is a
label and a filter, never a separate place.

**Where this differs from resonance threads, and why.** A resonance thread is two
people who both said yes. A collection conversation is a small group who joined a
shelf. That changes four things, and each is handled explicitly below:

1. **Blocks can't close the room.** In a 1:1 thread a block ends the
   conversation. Here it must not evict either party from a collection they both
   belong to, so a block hides the two from each other and leaves the room
   standing — the same treatment Echo gives a blocked author.
2. **A threat is still refused, not held.** Echo can hold a threat invisibly
   because it has a feed to hide it in. A collection of four readers is not a
   feed: the sender will notice their message never landed, and a silent hold
   would tell them it did. Refusing is honest here for the same reason it is in a
   thread.
3. **Leaving is not deleting.** A member who leaves keeps their words in the
   room, exactly as the books they added stay on the shelf.
4. **An attached book must be one the collection holds.** Not a gate on talking
   — anyone can say anything in the room — but an attachment pointing at a book
   the collection does not have would render as a label nobody can follow.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.collection import (
    Collection, CollectionItem, CollectionMember, CollectionMessage, CollectionMessageReaction,
)
from app.models.reaction_kinds import CHAT_REACTION_KINDS
from app.models.user import User
from app.services.moderation import VERDICT_CRISIS, VERDICT_HOLD, classify_text
from app.services.social_service import hidden_author_ids
from app.utils.attachments import delete_chat_attachment

MAX_MESSAGE_CHARS = 2000
MESSAGE_PAGE_DEFAULT = 50
MESSAGE_PAGE_MAX = 100


class ChatError(ValueError):
    """Bad chat input (router maps to 400)."""


class ChatRefused(ValueError):
    """The message was refused outright (router maps to 422 with the reason)."""


async def book_is_in_collection(
    db: AsyncSession, collection_id: uuid.UUID, book_id: uuid.UUID
) -> bool:
    """You can only talk about a book the collection actually holds.

    Checked on every post rather than only on the first: a book can be removed
    mid-conversation, and after that the room is closed even though its history
    survives.
    """
    return (await db.execute(
        select(CollectionItem.id).where(
            CollectionItem.collection_id == collection_id,
            CollectionItem.book_id == book_id,
        ).limit(1)
    )).scalar_one_or_none() is not None


def _visible(stmt: Select, hidden: set[uuid.UUID]) -> Select:
    """Drop messages from people the viewer has blocked or muted (and from people
    who blocked the viewer).

    Omitted, not tombstoned. A "message hidden" marker would still tell the
    viewer that the person they blocked is here and talking, which is the thing
    blocking is supposed to stop. Echo makes the same call.
    """
    if hidden:
        stmt = stmt.where(CollectionMessage.sender_id.notin_(hidden))
    return stmt


async def list_messages(
    db: AsyncSession,
    collection_id: uuid.UUID,
    viewer_id: uuid.UUID,
    *,
    book_id: uuid.UUID | None = None,
    limit: int = MESSAGE_PAGE_DEFAULT,
    before: datetime | None = None,
    before_id: uuid.UUID | None = None,
    after: datetime | None = None,
) -> list[CollectionMessage]:
    """A page of the collection's room, oldest-first within the page.

    ``book_id`` is an optional FILTER over the one room, not a room of its own.
    ``after`` fetches only what arrived since a timestamp — the poll the live
    view uses, so a quiet room costs one tiny query rather than a full page.

    Pages backward with (``before``, ``before_id``). Both halves matter: two
    messages posted in the same millisecond tie on ``created_at``, and paging on
    the timestamp alone would either repeat or skip them at a page boundary.
    """
    hidden = await hidden_author_ids(db, viewer_id)

    stmt = select(CollectionMessage).where(
        CollectionMessage.collection_id == collection_id,
    )
    if book_id is not None:
        stmt = stmt.where(CollectionMessage.book_id == book_id)

    if after is not None:
        # Forward poll: oldest-first, newest N. Deliberately separate from the
        # backward paging below — mixing the two directions in one branch is how
        # a cursor ends up applied against the wrong end of the range.
        stmt = _visible(stmt.where(CollectionMessage.created_at > after), hidden)
        stmt = stmt.order_by(
            CollectionMessage.created_at.asc(), CollectionMessage.id.asc()
        ).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    if before is not None:
        if before_id is None:
            # No tie-breaker supplied (an older client, or a hand-made request):
            # fall back to a strict timestamp cut. This can skip a message that
            # ties exactly with the boundary, which is why the cursor carries an
            # id at all — but skipping is the safe failure, not repeating.
            stmt = stmt.where(CollectionMessage.created_at < before)
        else:
            # Strict keyset over the full order (created_at DESC, id DESC): take
            # everything that sorts strictly after the cursor.
            stmt = stmt.where(
                or_(
                    CollectionMessage.created_at < before,
                    and_(
                        CollectionMessage.created_at == before,
                        CollectionMessage.id < before_id,
                    ),
                )
            )
    stmt = _visible(stmt, hidden)
    stmt = stmt.order_by(
        CollectionMessage.created_at.desc(), CollectionMessage.id.desc()
    ).limit(limit)

    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def post_message(
    db: AsyncSession,
    collection: Collection,
    sender_id: uuid.UUID,
    body: str,
    book_id: uuid.UUID | None = None,
    reply_to_id: uuid.UUID | None = None,
    attachment_path: str | None = None,
    attachment_type: str | None = None,
) -> tuple[CollectionMessage, str]:
    """Say something about a book in this collection. Returns (message, verdict).

    The caller has already established membership. What is checked here:

    - a non-empty body within the length bound;
    - IF a book is attached, that it is still in the collection — an attachment
      pointing at a book the collection does not hold would render as a label
      nobody else can click;
    - IF quoting an earlier message, that it belongs to THIS collection — don't
      let someone quote a message from a room they merely know the id of;
    - moderation, with a group's stance on each verdict:
        * **crisis** — sends, and the sender gets the resources back. Care, not
          punishment; the same stance Echo and resonance take.
        * **threat** — refused. See the module docstring: a small room is not a
          feed to hide it in.
        * **pii** — allowed. A private group swapping details is the room working.
    """
    body = (body or "").strip()
    if not body:
        raise ChatError("Message body is required")
    if len(body) > MAX_MESSAGE_CHARS:
        raise ChatError(f"Message must be {MAX_MESSAGE_CHARS} characters or fewer")
    if book_id is not None and not await book_is_in_collection(db, collection.id, book_id):
        raise ChatError("That book isn't in this collection")
    if reply_to_id is not None:
        quoted = (await db.execute(
            select(CollectionMessage.id).where(
                CollectionMessage.id == reply_to_id,
                CollectionMessage.collection_id == collection.id,
            )
        )).scalar_one_or_none()
        if quoted is None:
            raise ChatError("That message isn't in this room")

    # The VERDICT is not enough to decide here: `classify_text` returns HOLD for
    # BOTH a threat and contact details, and this surface treats those opposite
    # ways. The reason is what separates them.
    verdict, reason = classify_text(body)
    if verdict == VERDICT_HOLD and reason == "threat":
        raise ChatRefused(
            "That message can't be sent here. If someone is making you feel "
            "unsafe, you can report the conversation."
        )

    message = CollectionMessage(
        collection_id=collection.id,
        book_id=book_id,
        sender_id=sender_id,
        body=body,
        reply_to_id=reply_to_id,
        attachment_path=attachment_path,
        attachment_type=attachment_type,
    )
    db.add(message)
    await db.flush()
    return message, verdict


async def delete_message(
    db: AsyncSession,
    collection: Collection,
    message_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> bool:
    """Delete a message. Authors may delete their own; the owner, any.

    The same shape as removing a book, and for the same reason: a shared space
    needs someone who can clean it up, but not a member who can rewrite the
    record of what everyone else said.
    """
    message = (await db.execute(
        select(CollectionMessage).where(
            CollectionMessage.id == message_id,
            CollectionMessage.collection_id == collection.id,
        )
    )).scalar_one_or_none()
    if message is None:
        return False
    if actor_id != collection.user_id and message.sender_id != actor_id:
        raise PermissionError("You can only delete your own messages")
    delete_chat_attachment(message.attachment_path)
    await db.delete(message)
    await db.flush()
    return True


async def set_reaction(
    db: AsyncSession, message_id: uuid.UUID, user_id: uuid.UUID, kind: str, on: bool
) -> None:
    """Toggle one reaction. Public within the room — see the model docstring on
    `CollectionMessageReaction` for why this differs from Echo's author-only
    tally. Same upsert/delete-if-present shape as `echo_service.set_reaction`."""
    if kind not in CHAT_REACTION_KINDS:
        raise ChatError(f"Invalid reaction: {kind}")
    if on:
        stmt = pg_insert(CollectionMessageReaction).values(
            message_id=message_id, user_id=user_id, kind=kind,
        ).on_conflict_do_nothing(constraint="uq_collection_msg_reaction")
        await db.execute(stmt)
    else:
        row = (await db.execute(
            select(CollectionMessageReaction).where(
                CollectionMessageReaction.message_id == message_id,
                CollectionMessageReaction.user_id == user_id,
                CollectionMessageReaction.kind == kind,
            )
        )).scalar_one_or_none()
        if row:
            await db.delete(row)
    await db.flush()


async def annotate_messages(
    db: AsyncSession, messages: list[CollectionMessage], viewer_id: uuid.UUID,
) -> tuple[dict[uuid.UUID, dict[str, int]], dict[uuid.UUID, list[str]], dict[uuid.UUID, dict]]:
    """Batch-load reaction counts + the viewer's own reactions + reply-to previews
    for a page of messages — one query each, regardless of page size (same
    N+1-avoidance as `echo_service.annotate_feed`). Reactions are public here, so
    unlike Echo there's no separate author-gated count query.

    Returns (counts_by_message_id, my_kinds_by_message_id, reply_preview_by_message_id).
    """
    ids = [m.id for m in messages]
    if not ids:
        return {}, {}, {}

    counts: dict[uuid.UUID, dict[str, int]] = {}
    mine: dict[uuid.UUID, list[str]] = {}
    rows = (await db.execute(
        select(CollectionMessageReaction.message_id, CollectionMessageReaction.kind, CollectionMessageReaction.user_id)
        .where(CollectionMessageReaction.message_id.in_(ids))
    )).all()
    for mid, kind, uid in rows:
        counts.setdefault(mid, {}).setdefault(kind, 0)
        counts[mid][kind] += 1
        if uid == viewer_id:
            mine.setdefault(mid, []).append(kind)

    reply_ids = [m.reply_to_id for m in messages if m.reply_to_id is not None]
    previews: dict[uuid.UUID, dict] = {}
    if reply_ids:
        quoted = (await db.execute(
            select(CollectionMessage.id, CollectionMessage.body, User.handle)
            .join(User, User.id == CollectionMessage.sender_id)
            .where(CollectionMessage.id.in_(reply_ids))
        )).all()
        by_id = {qid: {"id": qid, "handle": handle, "body": body} for qid, body, handle in quoted}
        for m in messages:
            if m.reply_to_id is not None and m.reply_to_id in by_id:
                previews[m.id] = by_id[m.reply_to_id]

    return counts, mine, previews


_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,32})")


def parse_mentions(body: str, members: list[CollectionMember]) -> set[uuid.UUID]:
    """`@handle` tokens in a message, resolved to member user ids.

    Matched only against people who are actually IN this room — a mention of
    someone who isn't a member resolves to nobody rather than a notification
    with no valid recipient. Case-insensitive, since a reader typing a
    handle from memory shouldn't have to get the casing right for it to land.
    """
    if not body:
        return set()
    by_handle = {m.user.handle.lower(): m.user_id for m in members if getattr(m.user, "handle", None)}
    if not by_handle:
        return set()
    found = {h.lower() for h in _MENTION_RE.findall(body)}
    return {by_handle[h] for h in found if h in by_handle}


async def set_pinned_message(
    db: AsyncSession, collection: Collection, message_id: uuid.UUID | None, actor_id: uuid.UUID,
) -> None:
    """Pin one message (replacing whatever was pinned), or clear it with
    `message_id=None`. Any member may — the same "anyone can add, only the
    owner or the author can remove" line drawn elsewhere in this room would
    make pinning a passage someone else's call, which is backwards for a
    reading-together utility with no moderation weight of its own."""
    if message_id is not None:
        exists = (await db.execute(
            select(CollectionMessage.id).where(
                CollectionMessage.id == message_id,
                CollectionMessage.collection_id == collection.id,
            )
        )).scalar_one_or_none()
        if exists is None:
            raise ChatError("That message isn't in this room")
    collection.pinned_message_id = message_id
    await db.flush()


async def get_pinned_message(
    db: AsyncSession, collection: Collection,
) -> dict | None:
    if collection.pinned_message_id is None:
        return None
    row = (await db.execute(
        select(CollectionMessage.id, CollectionMessage.body, User.handle)
        .join(User, User.id == CollectionMessage.sender_id)
        .where(CollectionMessage.id == collection.pinned_message_id)
    )).first()
    if row is None:
        return None
    return {"id": row.id, "handle": row.handle, "body": row.body}


async def notify_targets(
    db: AsyncSession, collection_id: uuid.UUID, sender_id: uuid.UUID
) -> list[uuid.UUID]:
    """Who hears about a new message: current members, minus the sender, minus
    anyone blocked either way.

    Read live rather than captured when the conversation started — someone who
    left must not keep getting notified about a room they can no longer open.
    """
    member_ids = set((await db.execute(
        select(CollectionMember.user_id).where(CollectionMember.collection_id == collection_id)
    )).scalars().all())
    member_ids.discard(sender_id)
    if not member_ids:
        return []
    hidden = await hidden_author_ids(db, sender_id)
    return [uid for uid in member_ids if uid not in hidden]


# Conversation starters. Every one is either a QUESTION (obviously an invitation,
# true of nobody in particular) or a FACT computed from this collection's own
# data. Nothing in between — a fabricated "fun fact" about a book would be the
# one place in this product that invents something about your reading.
_STARTERS = [
    "What's the last book here that kept you up too late?",
    "Which of these would you press into a stranger's hands?",
    "Anyone else bounce off one of these? No wrong answers.",
    "What were you reading right before this collection existed?",
    "Which one has the best first line?",
    "If you could un-read one of these to read it fresh — which?",
    "What's the book you keep meaning to start here?",
]


async def conversation_sparks(
    db: AsyncSession, collection: Collection, viewer_id: uuid.UUID
) -> list[dict]:
    """A few things to say when nobody knows how to start.

    Returns `{kind, text}` where kind is "fact" or "question". Facts are counted
    from this collection and are checkable by anyone in it; questions are plainly
    questions. There is deliberately no third category of invented trivia — the
    rest of this product refuses to fabricate claims about books, and a chat
    widget is not the place to make an exception.
    """
    import random

    sparks: list[dict] = []

    items = (await db.execute(
        select(Book.title, Book.author, CollectionItem.created_at)
        .join(CollectionItem, CollectionItem.book_id == Book.id)
        .where(CollectionItem.collection_id == collection.id)
        .order_by(CollectionItem.created_at.desc())
    )).all()
    member_count = (await db.execute(
        select(func.count(CollectionMember.id))
        .where(CollectionMember.collection_id == collection.id)
    )).scalar() or 0

    if items:
        newest = items[0]
        sparks.append({
            "kind": "fact",
            "text": f"The newest arrival here is “{newest.title}”"
                    + (f" by {newest.author}." if newest.author else "."),
        })
    if len(items) >= 3:
        sparks.append({
            "kind": "fact",
            "text": f"There are {len(items)} books on this shelf and "
                    f"{member_count} of you. That's a lot of opinions.",
        })

    # Questions are shuffled so the same two do not greet the same room forever.
    sparks += [{"kind": "question", "text": t} for t in random.sample(_STARTERS, 3)]
    return sparks


async def list_conversations(
    db: AsyncSession, collection: Collection, viewer_id: uuid.UUID
) -> list[dict]:
    """Every book in the collection, with when it was last spoken about.

    Books with no messages are included — the point is to show where a
    conversation *could* start, not only where one already did. `last_at` is
    computed over messages this viewer can see, so a blocked member's post does
    not surface as "someone said something" to a person who cannot read it.
    """
    hidden = await hidden_author_ids(db, viewer_id)

    books = (await db.execute(
        select(Book.id, Book.title, Book.author, Book.cover_url, CollectionItem.position)
        .join(CollectionItem, CollectionItem.book_id == Book.id)
        .where(CollectionItem.collection_id == collection.id)
        .order_by(CollectionItem.position)
    )).all()

    stmt = select(
        CollectionMessage.book_id,
        func.max(CollectionMessage.created_at),
        func.count(CollectionMessage.id),
    ).where(CollectionMessage.collection_id == collection.id)
    stmt = _visible(stmt, hidden).group_by(CollectionMessage.book_id)
    stats = {bid: (last, n) for bid, last, n in (await db.execute(stmt)).all()}

    return [
        {
            "book_id": b.id,
            "title": b.title,
            "author": b.author,
            "cover_url": b.cover_url,
            "last_message_at": stats.get(b.id, (None, 0))[0],
            "message_count": stats.get(b.id, (None, 0))[1],
        }
        for b in books
    ]
