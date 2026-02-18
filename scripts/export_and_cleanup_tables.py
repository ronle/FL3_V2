#!/usr/bin/env python3
"""
Export V1 tables to GCS bucket and remove from database.

Tables are exported as SQL files, uploaded to GCS, then dropped.
"""

import asyncio
import os
import subprocess
import sys
from datetime import datetime

import asyncpg

# GCS bucket for backups
GCS_BUCKET = "gs://fl3-v2-db-backups/v1-tables-export"

# Tables to backup and remove (per user decision)
TABLES_TO_REMOVE = [
    # Wave engine tables
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
    # P&D labeling
    "pump_dump_labels_v2",
    "pump_dump_labels_v3",
    "pump_dump_metrics_raw",
    # Other V1 tables
    "cascade_policy",
    "catalyst_calendar",
    "impulse_response_curves",
    "master_tickers",
    "signal_regime_map",
    "spot_returns",
    "uoa_underlying_agg_5m",
]

# Tables pending decision (media scheduler dependent)
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

# Tables to keep
TABLES_TO_KEEP = [
    "company_alias_candidates",
    "company_aliases",
    "market_holidays",
]


async def get_table_info(pool, table: str) -> dict:
    """Get row count and size for a table."""
    try:
        count = await pool.fetchval(f"SELECT COUNT(*) FROM {table}")
        size = await pool.fetchval(f"""
            SELECT pg_size_pretty(pg_total_relation_size('{table}'))
        """)
        return {"exists": True, "rows": count, "size": size}
    except Exception as e:
        return {"exists": False, "error": str(e)}


def export_table_to_gcs(table: str, db_url: str) -> bool:
    """Export table to SQL and upload to GCS."""
    timestamp = datetime.now().strftime("%Y%m%d")
    filename = f"{table}_{timestamp}.sql"
    gcs_path = f"{GCS_BUCKET}/{filename}"

    print(f"  Exporting {table}...")

    # Use pg_dump to export table
    # Parse connection string for pg_dump
    # Format: postgresql://user:pass@host:port/db
    try:
        # pg_dump directly to gsutil
        dump_cmd = f'pg_dump "{db_url}" -t {table} --no-owner --no-acl'
        upload_cmd = f'gsutil cp - {gcs_path}'

        # Pipe pg_dump to gsutil
        result = subprocess.run(
            f'{dump_cmd} | gzip | gsutil cp - {gcs_path}.gz',
            shell=True,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            print(f"  Uploaded to {gcs_path}.gz")
            return True
        else:
            print(f"  Export failed: {result.stderr}")
            return False

    except Exception as e:
        print(f"  Export error: {e}")
        return False


async def drop_table(pool, table: str) -> bool:
    """Drop a table from the database."""
    try:
        await pool.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        print(f"  Dropped: {table}")
        return True
    except Exception as e:
        print(f"  Drop failed: {e}")
        return False


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    print("=" * 70)
    print("V1 TABLE EXPORT AND CLEANUP")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"GCS Bucket: {GCS_BUCKET}")
    print("=" * 70)

    # Phase 1: Report on all tables
    print("\n=== TABLES TO REMOVE (after backup) ===")
    tables_to_process = []
    for table in TABLES_TO_REMOVE:
        info = await get_table_info(pool, table)
        if info["exists"]:
            print(f"  {table}: {info['rows']:,} rows ({info['size']})")
            tables_to_process.append(table)
        else:
            print(f"  {table}: NOT FOUND (skipping)")

    print("\n=== TABLES PENDING DECISION (media scheduler) ===")
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

    # Phase 2: Export and remove
    print("\n" + "=" * 70)
    print("EXPORTING TABLES TO GCS")
    print("=" * 70)

    success_count = 0
    fail_count = 0

    for table in tables_to_process:
        print(f"\nProcessing: {table}")

        # Export to GCS
        if export_table_to_gcs(table, db_url):
            # Drop after successful export
            if await drop_table(pool, table):
                success_count += 1
            else:
                fail_count += 1
        else:
            print(f"  SKIPPED DROP (export failed)")
            fail_count += 1

    print("\n" + "=" * 70)
    print("CLEANUP COMPLETE")
    print("=" * 70)
    print(f"Successfully exported and removed: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Pending decision: {len(TABLES_PENDING_DECISION)}")
    print(f"Kept: {len(TABLES_TO_KEEP)}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
