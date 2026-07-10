"""add arc emotions, status, finish_thought, checkins, dna_type_slug

Revision ID: 009_add_arc_status_checkins
Revises: 008_tbr_list
Create Date: 2026-04-20

"""
from alembic import op
import sqlalchemy as sa


revision = "009_add_arc_status_checkins"
down_revision = "008_tbr_list"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- book_entries: arc + status + finish_thought ------------------------
    op.add_column(
        "book_entries",
        sa.Column("arc_start_emotion_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "book_entries",
        sa.Column("arc_middle_emotion_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "book_entries",
        sa.Column("arc_end_emotion_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "book_entries",
        sa.Column("finish_thought", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "book_entries",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="finished",
        ),
    )
    op.create_check_constraint(
        "check_entry_status",
        "book_entries",
        "status IN ('want_to_read','reading','finished')",
    )

    # --- dna_snapshots: dna_type_slug ---------------------------------------
    op.add_column(
        "dna_snapshots",
        sa.Column("dna_type_slug", sa.String(length=50), nullable=True),
    )

    # --- entry_checkins -----------------------------------------------------
    op.create_table(
        "entry_checkins",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entry_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("book_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("emotion_id", sa.String(length=30), nullable=False),
        sa.Column("note", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_entry_checkins_entry_created",
        "entry_checkins",
        ["entry_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_entry_checkins_entry_created", table_name="entry_checkins")
    op.drop_table("entry_checkins")

    op.drop_column("dna_snapshots", "dna_type_slug")

    op.drop_constraint("check_entry_status", "book_entries", type_="check")
    op.drop_column("book_entries", "status")
    op.drop_column("book_entries", "finish_thought")
    op.drop_column("book_entries", "arc_end_emotion_id")
    op.drop_column("book_entries", "arc_middle_emotion_id")
    op.drop_column("book_entries", "arc_start_emotion_id")
