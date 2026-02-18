#!/usr/bin/env python3
"""List all database objects in public schema."""

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

    # Get all tables
    tables = await pool.fetch("""
        SELECT tablename,
               pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as size
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)

    print("=" * 70)
    print("TABLES IN PUBLIC SCHEMA")
    print("=" * 70)
    for t in tables:
        # Get row count
        try:
            count = await pool.fetchval(f"SELECT COUNT(*) FROM {t['tablename']}")
            print(f"{t['tablename']}: {count:,} rows ({t['size']})")
        except Exception as e:
            print(f"{t['tablename']}: ERROR - {e}")

    # Get all views
    views = await pool.fetch("""
        SELECT viewname FROM pg_views
        WHERE schemaname = 'public'
        ORDER BY viewname
    """)

    print("\n" + "=" * 70)
    print("VIEWS IN PUBLIC SCHEMA")
    print("=" * 70)
    for v in views:
        print(f"  {v['viewname']}")

    # Get all materialized views
    matviews = await pool.fetch("""
        SELECT matviewname,
               pg_size_pretty(pg_total_relation_size('public.' || matviewname)) as size
        FROM pg_matviews
        WHERE schemaname = 'public'
        ORDER BY matviewname
    """)

    print("\n" + "=" * 70)
    print("MATERIALIZED VIEWS IN PUBLIC SCHEMA")
    print("=" * 70)
    for mv in matviews:
        try:
            count = await pool.fetchval(f"SELECT COUNT(*) FROM {mv['matviewname']}")
            print(f"  {mv['matviewname']}: {count:,} rows ({mv['size']})")
        except Exception as e:
            print(f"  {mv['matviewname']}: ERROR - {e}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
