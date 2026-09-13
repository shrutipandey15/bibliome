"""Reply-to and reactions on collection and resonance messages.

Two features borrowed from a design pass on the chat surfaces: quoting an
earlier message when you reply, and a small set of non-emoji reaction marks
(``resonated`` / ``noted`` / ``reconsidered`` / ``warm`` — see
``app/models/reaction_kinds.py``). Unlike Echo's reactions (private, author-only
tally — a public-feed design choice), these are public within the room/thread:
everyone in a collection or a connected resonance thread already sees everyone
else's name on every message, so hiding who reacted would read as broken, not
private.

``reply_to_id`` carries no denormalized snapshot of the quoted body — it's
resolved via a join at read time, and ``ON DELETE SET NULL`` means a reply to a
later-deleted message (only possible in Collection) just loses its quote rather
than needing a "this quoted a deleted message" fallback.

Revision ID: 033_chat_reply_reactions
Revises: 032_collection_room
Create Date: 2026-09-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "033_chat_reply_reactions"
down_revision: Union[str, None] = "032_collection_room"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collection_messages",
        sa.Column("reply_to_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("collection_messages.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(
        "ix_collection_messages_reply_to_id", "collection_messages", ["reply_to_id"],
    )

    op.add_column(
        "resonance_messages",
        sa.Column("reply_to_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("resonance_messages.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(
        "ix_resonance_messages_reply_to_id", "resonance_messages", ["reply_to_id"],
    )

    op.create_table(
        "collection_message_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("collection_messages.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "user_id", "kind", name="uq_collection_msg_reaction"),
    )

    op.create_table(
        "resonance_message_reactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("resonance_messages.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", "user_id", "kind", name="uq_resonance_msg_reaction"),
    )


def downgrade() -> None:
    op.drop_table("resonance_message_reactions")
    op.drop_table("collection_message_reactions")

    op.drop_index("ix_resonance_messages_reply_to_id", table_name="resonance_messages")
    op.drop_column("resonance_messages", "reply_to_id")
    op.drop_index("ix_collection_messages_reply_to_id", table_name="collection_messages")
    op.drop_column("collection_messages", "reply_to_id")
