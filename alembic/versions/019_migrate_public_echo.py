"""migrate legacy public_echo rows into real Echoes, then drop the column (Phase 5 B5.2)

Decision (Shruti): "echoes are meant to be public" → do not drop or hide them;
move them onto the one correct public surface. Each non-empty public_echo becomes
a book-anchored Echo authored by the user's (pseudonymous) handle. Visibility is
`community` (visible to signed-in members, not search-indexable) — the safer read
of consent for text written under the old model. The old surface exposed the raw
username; this does not.

Revision ID: 019_migrate_public_echo
Revises: 018_remove_reading_room
Create Date: 2026-07-13 09:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "019_migrate_public_echo"
down_revision: Union[str, None] = "018_remove_reading_room"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# book_key mirrors app.services.book_search.normalize as closely as SQL allows:
# lowercase, strip non-alphanumeric (keeping spaces), collapse whitespace, trim.
def _norm_sql(col: str) -> str:
    return (
        f"trim(regexp_replace(regexp_replace(lower(coalesce({col},'')), "
        f"'[^a-z0-9 ]', '', 'g'), '\\s+', ' ', 'g'))"
    )


def upgrade() -> None:
    book_key = f"{_norm_sql('be.title')} || '|' || {_norm_sql('be.author')}"
    op.execute(
        f"""
        INSERT INTO echoes
            (id, author_id, book_key, book_title, book_author,
             primary_emotion, secondary_emotion, body, visibility, status, created_at)
        SELECT gen_random_uuid(), be.user_id, {book_key}, be.title, be.author,
               NULL, NULL, left(be.public_echo, 500), 'community', 'active', be.created_at
        FROM book_entries be
        WHERE be.public_echo IS NOT NULL AND btrim(be.public_echo) <> ''
        """
    )
    op.drop_column("book_entries", "public_echo")


def downgrade() -> None:
    # Re-add the column (data is not restored; the migrated Echoes remain).
    op.add_column("book_entries", sa.Column("public_echo", sa.Text, nullable=True))
