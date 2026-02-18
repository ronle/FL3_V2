#!/usr/bin/env python3
"""Recreate master_tickers table for ticker validation."""

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
    print("RECREATING master_tickers TABLE")
    print("=" * 60)

    # Check if table exists
    exists = await pool.fetchval("""
        SELECT EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'public' AND tablename = 'master_tickers'
        )
    """)

    if exists:
        count = await pool.fetchval("SELECT COUNT(*) FROM master_tickers")
        print(f"Table already exists with {count:,} rows")
    else:
        # Create the table
        await pool.execute("""
            CREATE TABLE public.master_tickers (
                id BIGSERIAL PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE,
                name TEXT,
                exchange TEXT,
                mic TEXT,
                cik TEXT,
                is_active BOOLEAN DEFAULT TRUE NOT NULL,
                first_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                last_seen TIMESTAMPTZ DEFAULT NOW() NOT NULL,
                source_tags TEXT[]
            )
        """)
        print("Created master_tickers table")

        # Create index on symbol
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_master_tickers_symbol
            ON master_tickers(symbol)
        """)
        print("Created index on symbol")

        # Create the basic view
        await pool.execute("""
            CREATE OR REPLACE VIEW public.master_tickers_basic AS
            SELECT symbol, name, exchange, cik, is_active
            FROM public.master_tickers
        """)
        print("Created master_tickers_basic view")

        # Grant permissions
        try:
            await pool.execute("GRANT ALL ON TABLE public.master_tickers TO fr3_app")
            await pool.execute("GRANT SELECT ON TABLE public.master_tickers TO readonly")
            await pool.execute("GRANT USAGE, SELECT ON SEQUENCE master_tickers_id_seq TO fr3_app")
            print("Granted permissions")
        except Exception as e:
            print(f"Note: Permission grant issue: {e}")

        # Seed with common tickers from orats_daily
        print("\nSeeding from orats_daily symbols...")
        result = await pool.execute("""
            INSERT INTO master_tickers (symbol, name, is_active, source_tags)
            SELECT DISTINCT
                symbol,
                symbol as name,
                TRUE,
                ARRAY['orats_seed']
            FROM orats_daily
            WHERE asof_date > CURRENT_DATE - INTERVAL '30 days'
            ON CONFLICT (symbol) DO NOTHING
        """)

        count = await pool.fetchval("SELECT COUNT(*) FROM master_tickers")
        print(f"Seeded {count:,} tickers from orats_daily")

        # Also seed from tracked_tickers_v2
        result = await pool.execute("""
            INSERT INTO master_tickers (symbol, name, is_active, source_tags)
            SELECT DISTINCT
                symbol,
                symbol as name,
                TRUE,
                ARRAY['tracked_v2']
            FROM tracked_tickers_v2
            ON CONFLICT (symbol) DO NOTHING
        """)

        count = await pool.fetchval("SELECT COUNT(*) FROM master_tickers")
        print(f"Total tickers after tracked_tickers_v2: {count:,}")

    await pool.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
