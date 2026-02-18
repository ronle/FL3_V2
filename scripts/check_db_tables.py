#!/usr/bin/env python3
"""Check V1 table cleanup status."""

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
    print("DATABASE TABLE STATUS")
    print("=" * 60)

    # Check for v1_backup schema
    backup_schema = await pool.fetchval("""
        SELECT COUNT(*) FROM information_schema.schemata
        WHERE schema_name = 'v1_backup'
    """)
    print(f"\nv1_backup schema exists: {backup_schema > 0}")

    # Tables in v1_backup schema
    if backup_schema > 0:
        backup_tables = await pool.fetch("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'v1_backup'
            ORDER BY tablename
        """)
        print(f"\nTables in v1_backup schema ({len(backup_tables)}):")
        for t in backup_tables:
            print(f"  - {t['tablename']}")

    # Tables in public schema
    public_tables = await pool.fetch("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)
    print(f"\nTables in public schema ({len(public_tables)}):")
    for t in public_tables:
        print(f"  - {t['tablename']}")

    # V1 tables that should have been moved
    v1_tables = [
        "uoa_baselines", "uoa_hits", "uoa_hit_components", "uoa_episodes_daily",
        "uoa_signals", "uoa_meta", "uoa_thresholds", "option_contracts",
        "option_greeks_latest", "option_iv_history", "option_nbbo", "option_oi_daily",
        "greeks_norms_history", "ta_snapshots_daily", "ta_snapshots_latest",
        "tracked_tickers", "price_levels_latest", "signals_generated",
        "ready_for_analysis", "dq_issues", "dq_metrics", "dq_repairs_log",
        "short_interest", "ui_onboard_progress"
    ]

    still_in_public = []
    for table in v1_tables:
        exists = await pool.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_tables
                WHERE schemaname = 'public' AND tablename = $1
            )
        """, table)
        if exists:
            still_in_public.append(table)

    print(f"\n{'='*60}")
    print("V1 TABLES STILL IN PUBLIC SCHEMA")
    print("=" * 60)
    if still_in_public:
        print(f"\n{len(still_in_public)} V1 tables still in public:")
        for t in still_in_public:
            # Get row count
            try:
                count = await pool.fetchval(f"SELECT COUNT(*) FROM {t}")
                print(f"  - {t}: {count:,} rows")
            except:
                print(f"  - {t}: (error getting count)")
    else:
        print("\nAll V1 tables have been moved to v1_backup!")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
