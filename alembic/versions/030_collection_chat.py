"""Collection chat — talk about one book inside one collection (#6).

One table. A conversation is the pair ``(collection_id, book_id)``; there is no
thread row, because a thread row would have to be created lazily, raced on the
first message, and reconciled every time a book joins or leaves the collection.
The pair cannot drift out of sync with itself.

``book_id`` references ``books``, NOT ``collection_items``, and that is the load-
bearing choice: removing a book from the collection must not delete everyone
else's writing. The conversation simply becomes unreachable while the book is
out, and returns intact if it is added back.

Revision ID: 030_collection_chat
Revises: 029_shared_collections
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "030_collection_chat"
down_revision: Union[str, None] = "029_shared_collections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False,
        ),
        # books, not collection_items — see the module docstring.
        sa.Column(
            "book_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False,
        ),
        # CASCADE, unlike collection_items.added_by (SET NULL): a departing
        # member's books belong to the collection, their words belong to them.
        sa.Column(
            "sender_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("ix_collection_messages_collection_id", "collection_messages", ["collection_id"])
    op.create_index("ix_collection_messages_book_id", "collection_messages", ["book_id"])
    op.create_index("ix_collection_messages_sender_id", "collection_messages", ["sender_id"])
    # The read path, and the total order keyset paging needs so two messages in
    # the same millisecond can't be skipped or repeated at a page boundary.
    op.create_index(
        "ix_collection_messages_convo",
        "collection_messages",
        ["collection_id", "book_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("collection_messages")
