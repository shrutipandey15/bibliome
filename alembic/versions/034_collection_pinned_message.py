"""Pin one message per collection room.

A room accumulates a live thread and, sometimes, one thing worth keeping in
view above it — the passage everyone's actually discussing, or the reading
schedule for the week. `pinned_message_id` lives on `collections` rather than
a separate one-row-per-collection table because there is at most one: pinning
a second message replaces the first rather than stacking.

`ON DELETE SET NULL`: deleting the pinned message (author's own, or the
owner's cleanup right — see `collection_chat_service.delete_message`) unpins
it silently rather than leaving a dangling reference the read path has to
guard against.

Revision ID: 034_collection_pinned_message
Revises: 033_chat_reply_reactions
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "034_collection_pinned_message"
down_revision: Union[str, None] = "033_chat_reply_reactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collections",
        sa.Column(
            "pinned_message_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collection_messages.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("collections", "pinned_message_id")
