"""
Upload Seed Data to GCP Database

Pre-populates the GCP PostgreSQL database with:
1. Prior-day TA values (ta_daily_close) - NEW table for paper trading

NOTE: This script does NOT use V1 tables (intraday_baselines_30m, tracked_tickers_v2).
Paper trading uses its own isolated tables.

Usage:
    python scripts/upload_seed_data.py --create-tables  # Create tables first
    python scripts/upload_seed_data.py --ta             # Upload TA data
    python scripts/upload_seed_data.py --all            # Upload everything (same as --ta)
"""

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List

import psycopg2
from psycopg2.extras import execute_values

# Paths - detect if running in container or locally
if Path("/app").exists():
    # Running in Docker container
    BASE_DIR = Path("/app")
else:
    # Running locally
    BASE_DIR = Path(__file__).parent.parent

RESULTS_DIR = BASE_DIR / "polygon_data" / "backtest_results"
SQL_DIR = BASE_DIR / "sql"


def get_db_connection():
    """Get database connection from environment."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set")
    # Strip any whitespace/newlines from the URL
    database_url = database_url.strip()
    return psycopg2.connect(database_url)


def create_tables(conn):
    """Create seed data tables."""
    print("Creating tables...")

    sql_file = SQL_DIR / "create_seed_tables.sql"
    with open(sql_file) as f:
        sql = f.read()

    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()

    print("Tables created successfully")


def extract_ta_data() -> List[tuple]:
    """
    Extract TA data from signals_with_prior_day_ta.json.

    Returns list of tuples: (symbol, trade_date, rsi_14, macd, macd_signal,
                            macd_histogram, sma_20, ema_9, close_price)
    """
    print("Extracting TA data...")

    ta_file = RESULTS_DIR / "signals_with_prior_day_ta.json"
    with open(ta_file) as f:
        data = json.load(f)

    signals = data.get("signals", [])
    print(f"  Loaded {len(signals):,} signals")

    # Group by (symbol, date) and take the first/best TA values
    ta_by_symbol_date: Dict[tuple, dict] = {}

    for sig in signals:
        symbol = sig.get("symbol")
        detection_time = sig.get("detection_time", "")[:10]  # YYYY-MM-DD

        if not symbol or not detection_time:
            continue

        key = (symbol, detection_time)

        # Only keep first occurrence (or could aggregate)
        if key not in ta_by_symbol_date:
            ta_by_symbol_date[key] = {
                "rsi_14": sig.get("rsi_14_prior"),
                "macd": sig.get("macd_line_prior"),
                "macd_signal": sig.get("macd_signal_prior"),
                "macd_histogram": sig.get("macd_hist_prior"),
                "sma_20": sig.get("sma_20_prior"),
                "ema_9": sig.get("ema_9_prior"),
                "close_price": sig.get("price_prior_close"),
            }

    # Convert to tuples
    rows = []
    for (symbol, trade_date), ta in ta_by_symbol_date.items():
        rows.append((
            symbol,
            trade_date,
            ta.get("rsi_14"),
            ta.get("macd"),
            ta.get("macd_signal"),
            ta.get("macd_histogram"),
            ta.get("sma_20"),
            ta.get("ema_9"),
            ta.get("close_price"),
        ))

    print(f"  Extracted {len(rows):,} unique (symbol, date) TA records")
    return rows


def upload_ta_data(conn, rows: List[tuple]):
    """Upload TA data to database."""
    print(f"Uploading {len(rows):,} TA records...")

    sql = """
        INSERT INTO ta_daily_close
        (symbol, trade_date, rsi_14, macd, macd_signal, macd_histogram, sma_20, ema_9, close_price)
        VALUES %s
        ON CONFLICT (symbol, trade_date)
        DO UPDATE SET
            rsi_14 = EXCLUDED.rsi_14,
            macd = EXCLUDED.macd,
            macd_signal = EXCLUDED.macd_signal,
            macd_histogram = EXCLUDED.macd_histogram,
            sma_20 = EXCLUDED.sma_20,
            ema_9 = EXCLUDED.ema_9,
            close_price = EXCLUDED.close_price
    """

    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=1000)
    conn.commit()

    print("  TA data uploaded successfully")


def main():
    parser = argparse.ArgumentParser(description="Upload seed data to GCP database")
    parser.add_argument("--create-tables", action="store_true", help="Create tables")
    parser.add_argument("--ta", action="store_true", help="Upload TA data")
    parser.add_argument("--all", action="store_true", help="Upload everything (same as --ta)")
    args = parser.parse_args()

    if not any([args.create_tables, args.ta, args.all]):
        parser.print_help()
        return

    print("=" * 60)
    print("GCP Database Seed Data Upload (Paper Trading)")
    print("NOTE: Using only paper trading tables, not V1 tables")
    print("=" * 60)

    conn = get_db_connection()
    print(f"Connected to database\n")

    try:
        if args.create_tables or args.all:
            create_tables(conn)
            print()

        if args.ta or args.all:
            ta_rows = extract_ta_data()
            upload_ta_data(conn, ta_rows)
            print()

        print("=" * 60)
        print("Upload complete!")
        print("=" * 60)

        # Print summary (only paper trading tables)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ta_daily_close")
            ta_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM paper_trades_log")
            trades_count = cur.fetchone()[0]

        print(f"\nPaper Trading Database summary:")
        print(f"  ta_daily_close: {ta_count:,} records")
        print(f"  paper_trades_log: {trades_count:,} trades")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
