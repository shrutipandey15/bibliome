"""Book identity spine: re-normalize the catalog, collapse duplicates, link entries (B8.1).

Three steps, strictly ordered:

1. Re-normalize ``books.title_normalized`` / ``author_normalized`` with the
   accent-folding ``normalize()`` (P8-1). The old function let the same name
   produce two keys depending on Unicode form — "Emily Brontë" as NFC kept the
   "ë", as NFD lost it — which fractured Wuthering Heights into two rows.
2. Collapse the duplicates that re-normalizing exposes, keeping the most
   popular row per key (same rule as 014).
3. Add ``book_entries.book_id`` (nullable FK + index). Nullable because
   resolution is a separate backfill pass — see scripts/backfill_book_ids.py.

Deliberately does NOT resolve entries here: find-or-create needs to invent
catalog rows for titles nobody has searched, which is application logic, not a
schema step.

Revision ID: 023_book_identity
Revises: 022_vocab_axes
Create Date: 2026-07-27 00:00:00.000000
"""
import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023_book_identity"
down_revision: Union[str, None] = "022_vocab_axes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors app.services.book_search.normalize. Inlined on purpose: a migration
# must keep doing what it did the day it ran, even if the app's function moves on.
_NOISE_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", _NOISE_RE.sub("", folded.lower())).strip()


def upgrade() -> None:
    conn = op.get_bind()

    # --- 1. Re-normalize with the accent-folding rules -----------------------
    # The unique key is on exactly the columns being rewritten, so it has to come
    # off first: re-normalizing row-by-row transiently collides with rows not yet
    # rewritten (both Brontë spellings land on "emily bronte").
    op.drop_constraint("uq_books_title_author", "books", type_="unique")

    rows = conn.execute(sa.text("SELECT id, title, author FROM books")).fetchall()
    for row in rows:
        conn.execute(
            sa.text(
                "UPDATE books SET title_normalized = :t, author_normalized = :a WHERE id = :id"
            ),
            {"t": _normalize(row.title)[:300], "a": _normalize(row.author or "")[:200], "id": row.id},
        )

    # --- 2. Collapse duplicates the re-normalization exposed ------------------
    # Keep the most popular (then oldest, then lowest id). Entries still address
    # books by title/author at this point, so nothing needs repointing yet.
    conn.execute(sa.text(
        """
        DELETE FROM books a USING books b
        WHERE a.title_normalized = b.title_normalized
          AND a.author_normalized = b.author_normalized
          AND (
                a.popularity < b.popularity
             OR (a.popularity = b.popularity AND a.created_at > b.created_at)
             OR (a.popularity = b.popularity AND a.created_at = b.created_at AND a.id > b.id)
          )
        """
    ))
    op.create_unique_constraint(
        "uq_books_title_author", "books", ["title_normalized", "author_normalized"]
    )

    # --- 3. Give entries a book identity -------------------------------------
    op.add_column("book_entries", sa.Column("book_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_book_entries_book_id", "book_entries", "books", ["book_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_book_entries_book_id", "book_entries", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_book_entries_book_id", table_name="book_entries")
    op.drop_constraint("fk_book_entries_book_id", "book_entries", type_="foreignkey")
    op.drop_column("book_entries", "book_id")
    # The re-normalization is not reversed: the old keys were the bug.
