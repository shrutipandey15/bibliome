"""add the weekly Prompt (campfire) + echoes.prompt_id (Echo UX B6.5)

A curated, seeded question that changes weekly. It manufactures a populated feed
on day one without any follower graph, and can't become a popularity contest —
the only thing to "win" is answering. Echoes may optionally point at a prompt so
the feed can group answers to the same question.

Revision ID: 020_add_prompts
Revises: 019_migrate_public_echo
Create Date: 2026-07-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "020_add_prompts"
down_revision: Union[str, None] = "019_migrate_public_echo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_prompts_window", "prompts", ["starts_at", "ends_at"])

    op.add_column(
        "echoes",
        sa.Column("prompt_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_echoes_prompt", "echoes", "prompts",
        ["prompt_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_echoes_prompt", "echoes", ["prompt_id"])

    # Seed the first campfire, live for a week starting now. Curated by us; future
    # prompts are inserted the same way (there is deliberately no UGC path).
    op.execute(
        """
        INSERT INTO prompts (id, question, starts_at, ends_at)
        VALUES (gen_random_uuid(),
                'A book that made you feel longing this month?',
                now(), now() + interval '7 days')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_echoes_prompt", table_name="echoes")
    op.drop_constraint("fk_echoes_prompt", "echoes", type_="foreignkey")
    op.drop_column("echoes", "prompt_id")
    op.drop_index("ix_prompts_window", table_name="prompts")
    op.drop_table("prompts")
