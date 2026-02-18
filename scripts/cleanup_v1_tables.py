#!/usr/bin/env python3
"""
Cleanup V1 Tables Script

Moves obsolete V1 tables to a backup schema and drops views.
Run with: python scripts/cleanup_v1_tables.py
"""

import os
import sys

def main():
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    cleanup_sql = """
    -- FL3 V1 Cleanup Script
    -- Backup tables to a schema before dropping

    -- Create backup schema
    CREATE SCHEMA IF NOT EXISTS v1_backup;

    -- =============================================
    -- STEP 1: Drop Views and Materialized Views
    -- =============================================

    -- Materialized Views (drop first due to dependencies)
    DROP MATERIALIZED VIEW IF EXISTS uoa_episode_map_today_180s CASCADE;
    DROP MATERIALIZED VIEW IF EXISTS uoa_episodes_today_180s CASCADE;

    -- Regular Views
    DROP VIEW IF EXISTS uoa_hits_enriched CASCADE;
    DROP VIEW IF EXISTS uoa_hits_effective CASCADE;
    DROP VIEW IF EXISTS uoa_hits_dedup_today_180s CASCADE;
    DROP VIEW IF EXISTS signals_telemetry_view CASCADE;
    DROP VIEW IF EXISTS dq_failing_today CASCADE;
    DROP VIEW IF EXISTS dq_latest CASCADE;
    """

    # Tables to move to backup schema
    tables_to_backup = [
        # UOA Tables
        "uoa_baselines",
        "uoa_hits",
        "uoa_hit_components",
        "uoa_episodes_daily",
        "uoa_signals",
        "uoa_meta",
        "uoa_thresholds",
        # Options Tables
        "option_contracts",
        "option_greeks_latest",
        "option_iv_history",
        "option_nbbo",
        "option_oi_daily",
        "greeks_norms_history",
        # TA Tables (V1)
        "ta_snapshots_daily",
        "ta_snapshots_latest",
        "tracked_tickers",
        "price_levels_latest",
        # Wave/ML Tables
        "signals_generated",
        "ready_for_analysis",
        # DQ Tables
        "dq_issues",
        "dq_metrics",
        "dq_repairs_log",
        # Other
        "short_interest",
        "ui_onboard_progress",
    ]

    print("Connecting to database...")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    print("\n=== Step 1: Dropping Views ===")
    try:
        cur.execute(cleanup_sql)
        print("Views dropped successfully")
    except Exception as e:
        print(f"Warning dropping views: {e}")

    print("\n=== Step 2: Moving Tables to v1_backup Schema ===")
    for table in tables_to_backup:
        try:
            cur.execute(f"ALTER TABLE IF EXISTS {table} SET SCHEMA v1_backup;")
            print(f"  Moved: {table}")
        except Exception as e:
            print(f"  Skip {table}: {e}")

    print("\n=== Step 3: Verify ===")
    cur.execute("""
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname = 'v1_backup'
        ORDER BY tablename;
    """)
    backup_tables = cur.fetchall()
    print(f"Tables in v1_backup schema: {len(backup_tables)}")
    for schema, table in backup_tables:
        print(f"  - {table}")

    cur.close()
    conn.close()

    print("\n=== Cleanup Complete ===")


if __name__ == "__main__":
    main()
