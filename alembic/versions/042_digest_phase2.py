"""Weekly digest phase 2: outside book data, and the weekly question.

- book_wikidata: one row per (title, author) we tried to find on Wikidata, so
  each book is looked up once, not every week. found=False rows are retried
  after 90 days.
- question_answers: a reader's answer to the week's question. One per reader
  per week. Shown to others only with share=True, always anonymously; a written
  note only once approved (status 'approved').

Revision ID: 042_digest_phase2
Revises: 041_drop_reply_enabled
Create Date: 2026-10-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "042_digest_phase2"
down_revision: Union[str, None] = "041_drop_reply_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "book_wikidata",
        sa.Column("key", sa.String(520), primary_key=True),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("qid", sa.String(20)),
        sa.Column("wiki_title", sa.String(300)),
        sa.Column("pub_date", sa.Date()),
        sa.Column("pub_precision", sa.SmallInteger()),
        sa.Column("author_name", sa.String(200)),
        sa.Column("author_born", sa.Date()),
        sa.Column("author_died", sa.Date()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "question_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period", sa.String(12), nullable=False, index=True),
        sa.Column("question_id", sa.String(40), nullable=False),
        sa.Column("book_title", sa.String(300)),
        sa.Column("book_author", sa.String(200)),
        sa.Column("note", sa.String(280)),
        sa.Column("share", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(10), nullable=False, server_default="none"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "period", name="uq_answer_week"),
        sa.CheckConstraint("status IN ('none', 'pending', 'approved', 'rejected')", name="ck_answer_status"),
        sa.CheckConstraint("book_title IS NOT NULL OR note IS NOT NULL", name="ck_answer_not_empty"),
    )


def downgrade() -> None:
    op.drop_table("question_answers")
    op.drop_table("book_wikidata")
