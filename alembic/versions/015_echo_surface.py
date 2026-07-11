"""Echo public surface: handle, echoes, replies, reactions, blocks, mutes, reports (Phase 3)

Revision ID: 015_echo_surface
Revises: 014_books_unique_title_author
Create Date: 2026-07-11 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "015_echo_surface"
down_revision: Union[str, None] = "014_books_unique_title_author"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Pseudonymous handle (defaults to username) ──
    op.add_column("users", sa.Column("handle", sa.String(50), nullable=True))
    op.add_column("users", sa.Column("handle_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET handle = username WHERE handle IS NULL")
    op.alter_column("users", "handle", nullable=False)
    op.create_unique_constraint("uq_users_handle", "users", ["handle"])
    op.create_index("ix_users_handle", "users", ["handle"])

    op.create_table(
        "handle_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("old_handle", sa.String(50), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_handle_history_old", "handle_history", ["old_handle"])

    # ── Echoes ──
    op.create_table(
        "echoes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_key", sa.String(400), nullable=True),
        sa.Column("book_title", sa.String(300), nullable=True),
        sa.Column("book_author", sa.String(200), nullable=True),
        sa.Column("primary_emotion", sa.String(30), nullable=True),
        sa.Column("secondary_emotion", sa.String(30), nullable=True),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False, server_default="community"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_echoes_created", "echoes", ["created_at"])
    op.create_index("ix_echoes_emotion_created", "echoes", ["primary_emotion", "created_at"])
    op.create_index("ix_echoes_book", "echoes", ["book_key"])
    op.create_index("ix_echoes_author", "echoes", ["author_id"])

    op.create_table(
        "echo_replies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("echo_id", UUID(as_uuid=True), sa.ForeignKey("echoes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_echo_replies_echo_created", "echo_replies", ["echo_id", "created_at"])

    op.create_table(
        "echo_reactions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("echo_id", UUID(as_uuid=True), sa.ForeignKey("echoes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("echo_id", "user_id", "kind", name="uq_echo_reaction"),
    )

    # ── Safety ──
    op.create_table(
        "blocks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("blocker_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("blocked_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("blocker_id", "blocked_id", name="uq_block"),
    )
    op.create_index("ix_blocks_blocked", "blocks", ["blocked_id"])

    op.create_table(
        "mutes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("muter_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("muted_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("muter_id", "muted_id", name="uq_mute"),
    )

    op.create_table(
        "reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("reporter_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("resolution", sa.Text, nullable=True),
        sa.UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_report_once"),
    )
    op.create_index("ix_reports_target", "reports", ["target_type", "target_id"])
    op.create_index("ix_reports_status", "reports", ["status"])


def downgrade() -> None:
    op.drop_table("reports")
    op.drop_table("mutes")
    op.drop_table("blocks")
    op.drop_table("echo_reactions")
    op.drop_table("echo_replies")
    op.drop_table("echoes")
    op.drop_index("ix_handle_history_old", table_name="handle_history")
    op.drop_table("handle_history")
    op.drop_index("ix_users_handle", table_name="users")
    op.drop_constraint("uq_users_handle", "users", type_="unique")
    op.drop_column("users", "handle_changed_at")
    op.drop_column("users", "handle")
