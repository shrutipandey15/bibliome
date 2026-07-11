"""visibility spine: profile_visibility + share_tokens table (B2.1 / §2.3)

Revision ID: 013_visibility_spine
Revises: 012_add_shelf_position
Create Date: 2026-07-11 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "013_visibility_spine"
down_revision: Union[str, None] = "012_add_shelf_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── profile_visibility replaces is_public ──
    op.add_column(
        "users",
        sa.Column("profile_visibility", sa.String(20), nullable=False, server_default="private"),
    )
    # Backfill: public accounts stay public, everyone else is private.
    op.execute("UPDATE users SET profile_visibility = 'public' WHERE is_public = true")

    # ── share_tokens table (revocable, optionally-expiring capability links) ──
    op.create_table(
        "share_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Old plaintext share tokens are intentionally NOT migrated — they were
    # unrevocable/never-expiring (audit P1-8). Users regenerate a fresh link once.

    op.drop_column("users", "is_public")
    op.drop_column("users", "share_token")


def downgrade() -> None:
    op.add_column("users", sa.Column("is_public", sa.Boolean, server_default="false"))
    op.add_column("users", sa.Column("share_token", sa.String(50), nullable=True))
    op.execute("UPDATE users SET is_public = (profile_visibility = 'public')")
    op.drop_table("share_tokens")
    op.drop_column("users", "profile_visibility")
