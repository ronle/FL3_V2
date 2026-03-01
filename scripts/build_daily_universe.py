"""
Build Cameron Daily Universe

Aggregates from existing DuckDB stock_bars table (2.12B minute bars, already loaded)
into a per-symbol-per-day dataset with gap %, RVOL, price range, and market_cap.

Input:  E:/backtest_cache/fl3_backtest.duckdb  (stock_bars + daily_closes tables)
Output: E:/backtest_cache/cameron_daily_universe.parquet

Usage:
    python -m scripts.build_daily_universe
    python -m scripts.build_daily_universe --start-date 2023-01-01
    python -m scripts.build_daily_universe --start-date 2025-01-01 --end-date 2026-02-27
    python -m scripts.build_daily_universe --no-db  (skip market_cap join)
"""

import argparse
import logging
import os
import sys
import time

import duckdb
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DUCKDB_PATH = "E:/backtest_cache/fl3_backtest.duckdb"
OUTPUT_PATH = "E:/backtest_cache/cameron_daily_universe.parquet"
DEFAULT_START = "2023-01-01"
DEFAULT_END = "2026-12-31"


def load_market_cap() -> pd.DataFrame:
    """Fetch market_cap from master_tickers via Cloud SQL Auth Proxy."""
    import psycopg2

    db_url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
    if not db_url or "/cloudsql/" in db_url:
        db_url = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, market_cap
        FROM master_tickers
        WHERE is_active = TRUE AND market_cap IS NOT NULL
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    df = pd.DataFrame(rows, columns=["symbol", "market_cap"])
    log.info(f"Loaded market_cap for {len(df)} symbols from master_tickers")
    return df


def build_universe(start_date: str, end_date: str, skip_db: bool = False):
    t0 = time.time()

    if not os.path.exists(DUCKDB_PATH):
        log.error(f"Missing DuckDB: {DUCKDB_PATH}")
        sys.exit(1)

    # Open existing DuckDB read-only, create a separate in-memory DB for output
    duck = duckdb.connect()
    duck.execute("SET memory_limit = '8GB'")
    duck.execute("SET threads TO 8")

    # Create a view over the stock_minutes parquet (the DuckDB stock_bars table
    # has stale D: references; read the parquet directly on E:)
    PARQUET_PATH = "E:/backtest_cache/stock_minutes.parquet"
    if not os.path.exists(PARQUET_PATH):
        log.error(f"Missing parquet: {PARQUET_PATH}")
        sys.exit(1)

    duck.execute(f"""
        CREATE OR REPLACE VIEW stock_bars AS
        SELECT * FROM read_parquet('{PARQUET_PATH}')
    """)
    log.info(f"Created view over {PARQUET_PATH}")

    # Quick sanity check
    row_count = duck.execute(f"""
        SELECT COUNT(*) FROM stock_bars
        WHERE CAST(timestamp_et AS DATE) BETWEEN '{start_date}' AND '{end_date}'
    """).fetchone()[0]
    log.info(f"  stock_bars rows in range: {row_count:,}")

    # -----------------------------------------------------------------------
    # Step 1: Aggregate minute bars → daily per symbol
    # -----------------------------------------------------------------------
    log.info(f"Aggregating minute bars to daily ({start_date} to {end_date})...")
    t1 = time.time()

    duck.execute(f"""
        CREATE OR REPLACE TABLE daily_agg AS
        SELECT
            symbol,
            CAST(timestamp_et AS DATE) AS trade_date,

            -- Open: first bar of the day (stock_bars starts at 9:30 RTH)
            FIRST(open ORDER BY timestamp_et) AS open_price,

            -- Close: last bar of the day
            LAST(close ORDER BY timestamp_et) AS close_price,

            -- Intraday extremes
            MIN(low) AS intraday_low,
            MAX(high) AS intraday_high,

            -- Total RTH volume
            SUM(volume) AS daily_volume

        FROM stock_bars
        WHERE CAST(timestamp_et AS DATE) BETWEEN '{start_date}' AND '{end_date}'
        GROUP BY symbol, CAST(timestamp_et AS DATE)
        HAVING open_price IS NOT NULL AND close_price IS NOT NULL
    """)

    agg_count = duck.execute("SELECT COUNT(*) FROM daily_agg").fetchone()[0]
    log.info(f"  Daily aggregation: {agg_count:,} symbol-days in {time.time() - t1:.1f}s")

    # -----------------------------------------------------------------------
    # Step 2: Add prev_close, next_day_close, gap %, RVOL
    # -----------------------------------------------------------------------
    log.info("Computing gap %, rolling avg volume, RVOL...")
    t2 = time.time()

    duck.execute("""
        CREATE OR REPLACE TABLE universe AS
        WITH with_prev_next AS (
            SELECT
                *,
                LAG(close_price) OVER w AS prev_close,
                LEAD(close_price) OVER w AS next_day_close,
                LEAD(open_price) OVER w AS next_day_open,
                LEAD(intraday_high) OVER w AS next_day_high,
                LEAD(intraday_low) OVER w AS next_day_low
            FROM daily_agg
            WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
        )
        SELECT
            symbol,
            trade_date,
            prev_close,
            open_price,
            close_price,
            next_day_close,
            next_day_open,
            next_day_high,
            next_day_low,
            intraday_high,
            intraday_low,
            daily_volume,

            -- Gap %: (open - previous close) / previous close
            (open_price - prev_close) / NULLIF(prev_close, 0) AS gap_pct,

            -- Rolling 30-day average volume (exclude current day)
            AVG(daily_volume) OVER (
                PARTITION BY symbol ORDER BY trade_date
                ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
            ) AS avg_30d_volume,

            -- RVOL: today's volume vs 30-day average
            daily_volume / NULLIF(
                AVG(daily_volume) OVER (
                    PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING
                ), 0
            ) AS rvol

        FROM with_prev_next
        WHERE prev_close IS NOT NULL AND prev_close > 0
    """)

    uni_count = duck.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    log.info(f"  Universe rows: {uni_count:,} in {time.time() - t2:.1f}s")

    # -----------------------------------------------------------------------
    # Step 3: Join market_cap from master_tickers (float proxy)
    # -----------------------------------------------------------------------
    if not skip_db:
        try:
            log.info("Joining market_cap from master_tickers...")
            mc_df = load_market_cap()
            duck.register("market_cap_tbl", mc_df)

            duck.execute("""
                CREATE OR REPLACE TABLE universe AS
                SELECT
                    u.*,
                    mc.market_cap,
                    -- Float proxy: market_cap / close_price ≈ shares outstanding
                    CASE WHEN u.close_price > 0
                         THEN CAST(mc.market_cap / u.close_price AS BIGINT)
                         ELSE NULL
                    END AS est_shares_out
                FROM universe u
                LEFT JOIN market_cap_tbl mc ON u.symbol = mc.symbol
            """)
            mc_matched = duck.execute(
                "SELECT COUNT(*) FROM universe WHERE market_cap IS NOT NULL"
            ).fetchone()[0]
            log.info(f"  Market cap matched: {mc_matched:,} / {uni_count:,} rows")
        except Exception as e:
            log.warning(f"  Skipping market_cap join (DB unavailable): {e}")
            duck.execute("""
                CREATE OR REPLACE TABLE universe AS
                SELECT *, NULL::BIGINT AS market_cap, NULL::BIGINT AS est_shares_out
                FROM universe
            """)
    else:
        log.info("Skipping market_cap join (--no-db flag)")
        duck.execute("""
            CREATE OR REPLACE TABLE universe AS
            SELECT *, NULL::BIGINT AS market_cap, NULL::BIGINT AS est_shares_out
            FROM universe
        """)

    # -----------------------------------------------------------------------
    # Step 4: Write to Parquet
    # -----------------------------------------------------------------------
    log.info(f"Writing to {OUTPUT_PATH}...")
    duck.execute(f"""
        COPY (SELECT * FROM universe ORDER BY trade_date, symbol)
        TO '{OUTPUT_PATH}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)

    file_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    log.info(f"  Output: {file_mb:.1f} MB")

    # -----------------------------------------------------------------------
    # Step 5: Summary stats
    # -----------------------------------------------------------------------
    log.info("=" * 60)
    log.info("BUILD COMPLETE")
    log.info(f"  Total time: {time.time() - t0:.1f}s")
    log.info(f"  Output: {OUTPUT_PATH}")

    stats = duck.execute("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT symbol) AS symbols,
            COUNT(DISTINCT trade_date) AS trading_days,
            MIN(trade_date) AS first_date,
            MAX(trade_date) AS last_date,
            AVG(gap_pct) AS avg_gap,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY gap_pct) AS gap_p95,
            AVG(rvol) FILTER (WHERE rvol < 100) AS avg_rvol,
            COUNT(*) FILTER (WHERE gap_pct >= 0.04) AS gaps_4pct_plus,
            COUNT(*) FILTER (WHERE gap_pct >= 0.10) AS gaps_10pct_plus,
            COUNT(*) FILTER (WHERE rvol >= 5.0 AND rvol < 1e6) AS rvol_5x_plus,
            COUNT(*) FILTER (WHERE close_price BETWEEN 1.0 AND 20.0) AS price_1_to_20,
            COUNT(*) FILTER (WHERE market_cap IS NOT NULL AND market_cap < 100000000) AS mcap_under_100m
        FROM universe
    """).fetchone()

    cols = [
        "total_rows", "symbols", "trading_days", "first_date", "last_date",
        "avg_gap", "gap_p95", "avg_rvol",
        "gaps_4pct_plus", "gaps_10pct_plus", "rvol_5x_plus",
        "price_1_to_20", "mcap_under_100m",
    ]
    for col, val in zip(cols, stats):
        if isinstance(val, float):
            log.info(f"  {col}: {val:.4f}")
        else:
            log.info(f"  {col}: {val}")

    # Cameron signal universe estimate
    cameron_est = duck.execute("""
        SELECT COUNT(*) FROM universe
        WHERE gap_pct >= 0.04
          AND rvol >= 5.0 AND rvol < 1e6
          AND close_price BETWEEN 1.0 AND 20.0
    """).fetchone()[0]
    log.info(f"  Cameron-eligible (gap>=4%, rvol>=5x, $1-$20): {cameron_est:,}")

    duck.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Cameron daily stock universe")
    parser.add_argument("--start-date", default=DEFAULT_START, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=DEFAULT_END, help="End date (YYYY-MM-DD)")
    parser.add_argument("--no-db", action="store_true", help="Skip market_cap join (no DB needed)")
    args = parser.parse_args()

    build_universe(args.start_date, args.end_date, skip_db=args.no_db)
