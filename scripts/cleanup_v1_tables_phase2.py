#!/usr/bin/env python3
"""
Phase 2 V1 Table Cleanup - Move additional tables to v1_backup schema.

Based on user decision:
- REMOVE: Move to v1_backup then DROP
- KEEP: Leave in public schema
- PENDING: Report status only (media scheduler tables)
"""

import asyncio
import os
import sys
from datetime import datetime

import asyncpg

# Tables to backup and remove (per user decision 2026-01-31)
TABLES_TO_REMOVE = [
    # Wave engine tables (13)
    "wave_backtest_results",
    "wave_engine_runs",
    "wave_instances",
    "wave_intraday_features_5m_latest",
    "wave_ml_scores_daily",
    "wave_ml_scores_shadow",
    "wave_ml_scores_shadow_dir",
    "wave_pillar_snapshot",
    "wave_symbol_exclusions",
    "wave_trade_decisions",
    "wave_trade_exits",
    "wave_trade_fills",
    "wave_trade_manager_signals",
    # P&D labeling (3)
    "pump_dump_labels_v2",
    "pump_dump_labels_v3",
    "pump_dump_metrics_raw",
    # Other V1 tables (7)
    "cascade_policy",
    "catalyst_calendar",
    "impulse_response_curves",
    "master_tickers",
    "signal_regime_map",
    "spot_returns",
    "uoa_underlying_agg_5m",
]

# Tables pending decision (media scheduler dependent - V1 jobs still active)
TABLES_PENDING_DECISION = [
    "news_backfill_state_fmp",
    "param_change_log",
    "param_reload_signals",
    "scheduler_job_configs",
    "scheduler_job_runs",
    "system_param_overrides",
    "system_parameters",
    "system_params",
]

# Tables to keep in public schema
TABLES_TO_KEEP = [
    "company_alias_candidates",
    "company_aliases",
    "market_holidays",
]


async def get_table_info(pool, table: str, schema: str = "public") -> dict:
    """Get row count and size for a table."""
    full_name = f"{schema}.{table}" if schema != "public" else table
    try:
        exists = await pool.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_tables
                WHERE schemaname = $1 AND tablename = $2
            )
        """, schema, table)

        if not exists:
            return {"exists": False}

        count = await pool.fetchval(f"SELECT COUNT(*) FROM {full_name}")
        size = await pool.fetchval(f"""
            SELECT pg_size_pretty(pg_total_relation_size('{full_name}'))
        """)
        return {"exists": True, "rows": count, "size": size}
    except Exception as e:
        return {"exists": False, "error": str(e)}


async def move_to_backup_and_drop(pool, table: str) -> dict:
    """Move table to v1_backup schema, then drop it."""
    result = {"moved": False, "dropped": False, "error": None}

    try:
        # Check if table exists in public
        exists = await pool.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_tables
                WHERE schemaname = 'public' AND tablename = $1
            )
        """, table)

        if not exists:
            result["error"] = "Table not found in public schema"
            return result

        # Move to v1_backup schema
        await pool.execute(f"ALTER TABLE {table} SET SCHEMA v1_backup")
        result["moved"] = True

        # Drop from v1_backup
        await pool.execute(f"DROP TABLE v1_backup.{table} CASCADE")
        result["dropped"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    print("=" * 70)
    print("V1 TABLE CLEANUP - PHASE 2")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 70)

    # Ensure v1_backup schema exists
    await pool.execute("CREATE SCHEMA IF NOT EXISTS v1_backup")

    # Phase 1: Report current state
    print("\n=== TABLES TO REMOVE ===")
    total_rows = 0
    tables_found = []
    for table in TABLES_TO_REMOVE:
        info = await get_table_info(pool, table)
        if info["exists"]:
            print(f"  {table}: {info['rows']:,} rows ({info['size']})")
            total_rows += info["rows"]
            tables_found.append(table)
        else:
            print(f"  {table}: NOT FOUND (already removed)")

    print(f"\n  Total: {len(tables_found)} tables, {total_rows:,} rows")

    print("\n=== TABLES PENDING DECISION (media scheduler active) ===")
    for table in TABLES_PENDING_DECISION:
        info = await get_table_info(pool, table)
        if info["exists"]:
            print(f"  {table}: {info['rows']:,} rows ({info['size']})")
        else:
            print(f"  {table}: NOT FOUND")

    print("\n=== TABLES TO KEEP ===")
    for table in TABLES_TO_KEEP:
        info = await get_table_info(pool, table)
        if info["exists"]:
            print(f"  {table}: {info['rows']:,} rows ({info['size']})")
        else:
            print(f"  {table}: NOT FOUND")

    # Phase 2: Move and drop
    print("\n" + "=" * 70)
    print("REMOVING TABLES (move to v1_backup then drop)")
    print("=" * 70)

    success_count = 0
    fail_count = 0

    for table in tables_found:
        result = await move_to_backup_and_drop(pool, table)

        if result["dropped"]:
            print(f"  REMOVED: {table}")
            success_count += 1
        elif result["moved"]:
            print(f"  MOVED (drop failed): {table} - {result['error']}")
            fail_count += 1
        else:
            print(f"  FAILED: {table} - {result['error']}")
            fail_count += 1

    # Phase 3: Verify final state
    print("\n" + "=" * 70)
    print("VERIFICATION - Tables remaining in v1_backup schema")
    print("=" * 70)

    backup_tables = await pool.fetch("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'v1_backup'
        ORDER BY tablename
    """)

    if backup_tables:
        print(f"\nTables in v1_backup: {len(backup_tables)}")
        for t in backup_tables:
            info = await get_table_info(pool, t['tablename'], 'v1_backup')
            if info["exists"]:
                print(f"  - {t['tablename']}: {info['rows']:,} rows ({info['size']})")
    else:
        print("\nv1_backup schema is empty (all tables dropped)")

    print("\n" + "=" * 70)
    print("CLEANUP COMPLETE")
    print("=" * 70)
    print(f"Removed: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Pending decision: {len(TABLES_PENDING_DECISION)}")
    print(f"Kept: {len(TABLES_TO_KEEP)}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
