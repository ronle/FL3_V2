#!/usr/bin/env python3
"""Recreate ready_for_analysis table for V1 news pipeline."""

import asyncio
import os
import sys

import asyncpg


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    print("=" * 60)
    print("RECREATING ready_for_analysis TABLE")
    print("=" * 60)

    # Check if table exists
    exists = await pool.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'public' AND tablename = 'ready_for_analysis'
        )
    """)

    if exists:
        print("Table already exists!")
    else:
        # Create the table
        await pool.execute("""
            CREATE TABLE public.ready_for_analysis (
                article_id BIGINT NOT NULL PRIMARY KEY,
                queued_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        print("Created ready_for_analysis table")

        # Add foreign key constraint
        try:
            await pool.execute("""
                ALTER TABLE public.ready_for_analysis
                ADD CONSTRAINT ready_for_analysis_article_id_fkey
                FOREIGN KEY (article_id) REFERENCES public.articles(id)
                ON DELETE CASCADE
            """)
            print("Added foreign key constraint")
        except Exception as e:
            print(f"Note: Could not add FK constraint: {e}")

        # Grant permissions
        try:
            await pool.execute("GRANT ALL ON TABLE public.ready_for_analysis TO fr3_app")
            await pool.execute("GRANT SELECT ON TABLE public.ready_for_analysis TO readonly")
            print("Granted permissions")
        except Exception as e:
            print(f"Note: Permission grant issue: {e}")

    # Verify
    count = await pool.fetchval("SELECT COUNT(*) FROM ready_for_analysis")
    print(f"\nTable ready_for_analysis: {count} rows")

    await pool.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
