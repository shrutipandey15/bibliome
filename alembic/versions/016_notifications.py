"""notifications, prefs, digests (Phase 4)

Revision ID: 016_notifications
Revises: 015_echo_surface
Create Date: 2026-07-11 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "016_notifications"
down_revision: Union[str, None] = "015_echo_surface"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("batch_key", sa.String(120), nullable=True),
        sa.Column("deliver_after", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "read_at"])
    op.create_index("ix_notifications_batch", "notifications", ["user_id", "batch_key"])

    op.create_table(
        "notification_prefs",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("reply_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("digest_enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("quiet_hours_start", sa.Integer, nullable=True),
        sa.Column("quiet_hours_end", sa.Integer, nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "notification_digests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period", sa.String(12), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "period", name="uq_digest_period"),
    )


def downgrade() -> None:
    op.drop_table("notification_digests")
    op.drop_table("notification_prefs")
    op.drop_table("notifications")
