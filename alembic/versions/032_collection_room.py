"""One chat per COLLECTION, not per book (#6 revision).

The original shape gave every book its own room. In practice that fragments a
small group across a dozen empty rooms: a four-person collection with ten books
has ten places to look and nine of them say "start it". Conversation needs one
place to happen.

``book_id`` becomes NULLABLE and demotes from *identity* to *attachment*: a
message belongs to the collection, and may optionally point at one of its books
("about Beach Read"). Existing per-book messages keep their attachment, so no
history is lost or re-parented — they simply become messages in the collection's
room that happen to reference a book.

Revision ID: 032_collection_room
Revises: 031_push_subscriptions
Create Date: 2026-08-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "032_collection_room"
down_revision: Union[str, None] = "031_push_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("collection_messages", "book_id", nullable=True)
    # The read path is now "this collection, newest first"; the book is a filter
    # at most. The old (collection, book, created_at, id) index no longer leads.
    op.create_index(
        "ix_collection_messages_room",
        "collection_messages",
        ["collection_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_collection_messages_room", table_name="collection_messages")
    # Messages with no book cannot exist in the per-book shape.
    op.execute("DELETE FROM collection_messages WHERE book_id IS NULL")
    op.alter_column("collection_messages", "book_id", nullable=False)
