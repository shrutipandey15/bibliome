"""Read-only inspection of the current DB schema. No writes."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from app.database import async_session


async def run() -> None:
    async with async_session() as session:
        print("=" * 70)
        print("ALEMBIC VERSION")
        print("=" * 70)
        r = await session.execute(text("SELECT version_num FROM alembic_version"))
        for row in r:
            print(f"  {row.version_num}")

        print()
        print("=" * 70)
        print("TABLES IN public SCHEMA")
        print("=" * 70)
        r = await session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ))
        tables = [row.tablename for row in r]
        for t in tables:
            print(f"  {t}")

        for target in ("book_entries", "dna_snapshots", "entry_checkins", "users"):
            if target not in tables:
                print(f"\n(table '{target}' does not exist)")
                continue
            print()
            print("=" * 70)
            print(f"COLUMNS: {target}")
            print("=" * 70)
            r = await session.execute(text("""
                SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t
                ORDER BY ordinal_position
            """), {"t": target})
            for row in r:
                length = f"({row.character_maximum_length})" if row.character_maximum_length else ""
                null = "NULL" if row.is_nullable == "YES" else "NOT NULL"
                default = f" DEFAULT {row.column_default}" if row.column_default else ""
                print(f"  {row.column_name:30} {row.data_type}{length:12} {null}{default}")

        print()
        print("=" * 70)
        print("LOOKING FOR TBR-RELATED ARTIFACTS")
        print("=" * 70)
        # any table with 'tbr' in name
        r = await session.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename ILIKE '%tbr%'"
        ))
        tbr_tables = [row.tablename for row in r]
        print(f"  tables matching 'tbr': {tbr_tables or 'none'}")

        # any column with 'tbr' or 'status' in any table
        r = await session.execute(text("""
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema='public'
              AND (column_name ILIKE '%tbr%' OR column_name = 'status')
            ORDER BY table_name, column_name
        """))
        matches = list(r)
        print(f"  columns matching 'tbr' or named 'status':")
        if not matches:
            print(f"    (none)")
        for row in matches:
            print(f"    {row.table_name}.{row.column_name}")


if __name__ == "__main__":
    asyncio.run(run())
