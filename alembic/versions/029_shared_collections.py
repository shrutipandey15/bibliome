"""Shared collections — membership, invite links, and book-identity items (#5).

Three changes, one theme: a collection stops being a private list owned by one
reader and becomes a place several readers can add to.

- ``collection_members`` — who can see and add. The owner gets a row too rather
  than being implied by ``collections.user_id``, so one query answers "can this
  person see it?" everywhere.
- ``collection_invites`` — revocable capability links, stored as SHA-256 like
  ``share_tokens``. Multi-use by design (one link, a group chat, several joins).
- ``collection_items`` gains ``book_id`` + ``added_by``.

The item change is the substantive one. An item used to point at a
``book_entries`` row — one reader's *private copy* of a book. That cannot work
once a collection is shared: a member adding a book would attach an entry nobody
else can read, and the same book added twice would be two unrelated items. Items
now point at the canonical ``books`` row.

``entry_id`` is kept, nullable, rather than dropped: collections predate the
catalog, so an item whose entry never resolved to a book
(``book_entries.book_id IS NULL`` — a title that matched nothing) has no
``book_id`` to migrate onto. Dropping the column would silently empty those
collections. Those rows keep working off ``entry_id``; everything new sets
``book_id``.

Revision ID: 029_shared_collections
Revises: 028_entry_progress
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "029_shared_collections"
down_revision: Union[str, None] = "028_entry_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── collection_items: book identity + provenance ──
    op.add_column(
        "collection_items",
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "collection_items",
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_collection_items_book", "collection_items", "books",
        ["book_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        # SET NULL, not CASCADE: a member leaving must not delete books the rest
        # of the collection is reading.
        "fk_collection_items_added_by", "collection_items", "users",
        ["added_by"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_collection_items_book_id", "collection_items", ["book_id"])
    op.create_index("ix_collection_items_added_by", "collection_items", ["added_by"])

    # Backfill: the entry's canonical book, and the collection's owner as adder.
    op.execute(
        """
        UPDATE collection_items ci
           SET book_id = be.book_id
          FROM book_entries be
         WHERE be.id = ci.entry_id
           AND be.book_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE collection_items ci
           SET added_by = c.user_id
          FROM collections c
         WHERE c.id = ci.collection_id
        """
    )

    # Two rows in one collection can point at the same book only if the owner
    # somehow held two entries for it. Collapse to the earliest before the
    # unique constraint goes on, or the migration fails on real data.
    op.execute(
        """
        DELETE FROM collection_items a
         USING collection_items b
         WHERE a.collection_id = b.collection_id
           AND a.book_id = b.book_id
           AND a.book_id IS NOT NULL
           AND (a.created_at, a.id) > (b.created_at, b.id)
        """
    )
    op.create_unique_constraint(
        "uq_collection_book", "collection_items", ["collection_id", "book_id"]
    )

    # entry_id becomes optional — new items are identified by book_id.
    op.alter_column("collection_items", "entry_id", nullable=True)

    # ── collection_members ──
    op.create_table(
        "collection_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("collection_id", "user_id", name="uq_collection_member"),
        sa.CheckConstraint("role IN ('owner','member')", name="check_collection_role"),
    )
    op.create_index("ix_collection_members_collection_id", "collection_members", ["collection_id"])
    op.create_index("ix_collection_members_user_id", "collection_members", ["user_id"])

    # Every existing collection gets its owner as a member, so the membership
    # table is authoritative from the first read after this migration.
    op.execute(
        """
        INSERT INTO collection_members (id, collection_id, user_id, role, joined_at)
        SELECT gen_random_uuid(), c.id, c.user_id, 'owner', c.created_at
          FROM collections c
        """
    )

    # ── collection_invites ──
    op.create_table(
        "collection_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_collection_invites_collection_id", "collection_invites", ["collection_id"])
    op.create_index("ix_collection_invites_token_hash", "collection_invites", ["token_hash"])


def downgrade() -> None:
    op.drop_table("collection_invites")
    op.drop_table("collection_members")

    # Items added by a member have no entry in the owner's library, so there is
    # no entry_id to restore for them — they cannot survive the downgrade.
    op.execute("DELETE FROM collection_items WHERE entry_id IS NULL")
    op.drop_constraint("uq_collection_book", "collection_items", type_="unique")
    op.alter_column("collection_items", "entry_id", nullable=False)
    op.drop_index("ix_collection_items_added_by", table_name="collection_items")
    op.drop_index("ix_collection_items_book_id", table_name="collection_items")
    op.drop_constraint("fk_collection_items_added_by", "collection_items", type_="foreignkey")
    op.drop_constraint("fk_collection_items_book", "collection_items", type_="foreignkey")
    op.drop_column("collection_items", "added_by")
    op.drop_column("collection_items", "book_id")
