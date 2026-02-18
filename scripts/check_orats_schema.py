#!/usr/bin/env python3
"""Check ORATS table schema."""

import asyncio
import os
import sys
import asyncpg


async def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        # Get column names
        columns = await pool.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'orats_daily'
            ORDER BY ordinal_position
        """)

        print("ORATS_DAILY COLUMNS:")
        print("=" * 50)
        for c in columns:
            print(f"  {c['column_name']:<30} {c['data_type']}")

        print("\n\nSAMPLE ROW:")
        print("=" * 50)
        sample = await pool.fetchrow("""
            SELECT * FROM orats_daily
            WHERE asof_date = (SELECT MAX(asof_date) FROM orats_daily)
            LIMIT 1
        """)

        if sample:
            for key, value in sample.items():
                print(f"  {key:<30} = {value}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
