"""Collections — curated, orderable groupings of books (Feature 2, shared in #5).

Each collection has its own visibility, so a public profile can still hold a
private collection.

**Shared collections (#5).** A collection can have members, invited by a
capability link. That changes what an item *is*: originally an item pointed at a
`book_entries` row, which is one reader's private copy of a book. In a shared
collection that is incoherent — a member adding a book would attach their own
private entry, which no one else can read, and the same book added by two members
would be two different items. So an item now points at the canonical `books` row
and records who put it there. See `CollectionItem`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

COLLECTION_VISIBILITIES = ("private", "community", "public")

# Owner can rename, delete, invite, revoke, and remove anyone's item. Member can
# add items and remove their OWN. Deliberately only two: a third tier is a
# permissions UI nobody asked for.
COLLECTION_ROLES = ("owner", "member")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, server_default="private")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["CollectionItem"]] = relationship(
        "CollectionItem", back_populates="collection", cascade="all, delete-orphan"
    )
    members: Mapped[list["CollectionMember"]] = relationship(
        "CollectionMember", back_populates="collection", cascade="all, delete-orphan"
    )
    invites: Mapped[list["CollectionInvite"]] = relationship(
        "CollectionInvite", back_populates="collection", cascade="all, delete-orphan"
    )


class CollectionItem(Base):
    """A book in a collection, and who put it there.

    ``book_id`` is the identity — the canonical catalog row, shared by everyone.
    ``entry_id`` is a **legacy** pointer at one reader's private `book_entries`
    row, kept nullable only because collections predate the catalog: an item
    whose entry never resolved to a book (`book_entries.book_id IS NULL`, a title
    that matched nothing) has no `book_id` to migrate to, and dropping the column
    would silently empty those collections. New items always set ``book_id``.

    ``added_by`` is not decoration: in a shared collection it decides who may
    remove an item, and it survives the adder leaving (ondelete SET NULL) so a
    departure doesn't delete books other members are reading.
    """

    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint("collection_id", "entry_id", name="uq_collection_item"),
        # One row per book per collection, so two members adding the same book
        # get one item rather than a duplicate. NULL book_id (legacy rows) does
        # not collide in Postgres, which is exactly the tolerance those need.
        UniqueConstraint("collection_id", "book_id", name="uq_collection_book"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=True, index=True
    )
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("book_entries.id", ondelete="CASCADE"), nullable=True
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    collection: Mapped["Collection"] = relationship("Collection", back_populates="items")


class CollectionMember(Base):
    """Who can see and add to a shared collection.

    The owner gets a row too, rather than being implied by `collections.user_id`.
    One membership table means one query answers "can this person see it?" — the
    alternative is every read path remembering to check ownership separately,
    which is how a surface eventually forgets.
    """

    __tablename__ = "collection_members"
    __table_args__ = (
        UniqueConstraint("collection_id", "user_id", name="uq_collection_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    collection: Mapped["Collection"] = relationship("Collection", back_populates="members")
    user: Mapped["User"] = relationship("User")


class CollectionInvite(Base):
    """A revocable capability link that grants membership.

    Same shape and the same reasoning as ``ShareToken``: the raw token is shown
    once and only its SHA-256 is stored, so a DB leak yields no live invites.
    Unlike a share token it is *multi-use by design* — one link goes in a group
    chat and several people join — with ``max_uses`` for the times you want
    otherwise.
    """

    __tablename__ = "collection_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # NULL = unlimited. `uses` counts redemptions that created a membership, so
    # one person clicking the link twice does not burn two uses.
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    collection: Mapped["Collection"] = relationship("Collection", back_populates="invites")


class CollectionMessage(Base):
    """One room per COLLECTION. A message may attach a book.

    The first version gave every book its own room, on the reasoning that a
    collection is a set of books and the talk should hang off each one. In
    practice that fragments a small group: four people and ten books meant ten
    places to look, nine of them empty, and conversation went to none of them.
    Talk needs one place to happen.

    So ``book_id`` is **nullable** and is an *attachment*, not an identity — a
    message belongs to the collection and may point at one of its books ("about
    Beach Read"). The book is a filter over one room, never a room of its own.

    Still true from the first version, and still load-bearing:

    - ``book_id`` references ``books``, NOT ``collection_items``. Removing a book
      from the collection must not delete everyone else's writing; the message
      keeps its attachment and stays in the room.
    - ``sender_id`` cascades on user delete: authored text goes when the account
      does. That differs on purpose from ``CollectionItem.added_by`` (SET NULL) —
      a departing member's *books* are the collection's, their *words* are theirs.
    """

    __tablename__ = "collection_messages"
    __table_args__ = (
        # The read path is always "this book, in this collection, newest first".
        # created_at + id so keyset paging has a total order and cannot skip or
        # repeat a message when two land in the same millisecond.
        # The room read path: one collection, newest first. created_at + id so
        # keyset paging has a total order and cannot skip or repeat a message
        # when two land in the same millisecond.
        Index("ix_collection_messages_room", "collection_id", "created_at", "id"),
        # Kept for the "only messages about this book" filter.
        Index(
            "ix_collection_messages_convo",
            "collection_id", "book_id", "created_at", "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional attachment, not identity — see the class docstring.
    book_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id", ondelete="CASCADE"), nullable=True, index=True
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
