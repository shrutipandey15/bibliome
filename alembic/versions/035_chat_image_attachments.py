"""Image attachments on collection and resonance messages.

A photo of a page — the mockup that started the letters redesign joked about
sending "a photograph of a page with your pencil on it", and it turned out to
be the single most requested feature once reactions and mentions landed.

`attachment_path` is a server-local disk path (app/utils/attachments.py),
never returned to a client directly — only ever through an authenticated,
party/membership-checked endpoint. Nullable, no default: most messages have
no attachment at all.

Revision ID: 035_chat_image_attachments
Revises: 034_collection_pinned_message
Create Date: 2026-09-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "035_chat_image_attachments"
down_revision: Union[str, None] = "034_collection_pinned_message"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("collection_messages", sa.Column("attachment_path", sa.String(255), nullable=True))
    op.add_column("collection_messages", sa.Column("attachment_type", sa.String(40), nullable=True))
    op.add_column("resonance_messages", sa.Column("attachment_path", sa.String(255), nullable=True))
    op.add_column("resonance_messages", sa.Column("attachment_type", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("resonance_messages", "attachment_type")
    op.drop_column("resonance_messages", "attachment_path")
    op.drop_column("collection_messages", "attachment_type")
    op.drop_column("collection_messages", "attachment_path")
