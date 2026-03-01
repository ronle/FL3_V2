"""
Backfill orats_daily_returns for 2023 using stock_price from orats_daily.

Uses LEAD() window function to compute forward returns at +1, +3, +5, +10, +15, +20
trading days. LEAD() naturally handles trading-day offsets (weekends/holidays skipped).

Inserts ~1.46M rows in batches. Safe to re-run (ON CONFLICT does nothing).

Usage:
    python -m scripts.backfill_returns_2023
    python -m scripts.backfill_returns_2023 --dry-run
"""

import os
import sys
import time
import argparse
import psycopg2
from psycopg2.extras import execute_values

# ── DB connection ────────────────────────────────────────────────────
_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
DATABASE_URL = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL or "/cloudsql/" in DATABASE_URL:
    DATABASE_URL = _LOCAL_DB

# ── SQL: compute forward returns via LEAD() ──────────────────────────
# We include a small buffer into 2024 so that late-2023 dates can compute
# r_p20 (needs ~4 weeks of lookahead).
COMPUTE_SQL = """
WITH prices AS (
    SELECT
        symbol,
        asof_date,
        stock_price,
        LEAD(stock_price,  1) OVER w AS px_1,
        LEAD(stock_price,  3) OVER w AS px_3,
        LEAD(stock_price,  5) OVER w AS px_5,
        LEAD(stock_price, 10) OVER w AS px_10,
        LEAD(stock_price, 15) OVER w AS px_15,
        LEAD(stock_price, 20) OVER w AS px_20
    FROM orats_daily
    WHERE asof_date >= '2023-01-01' AND asof_date < '2024-02-15'
      AND stock_price > 0
    WINDOW w AS (PARTITION BY symbol ORDER BY asof_date)
)
SELECT
    symbol,
    asof_date,
    stock_price,
    ROUND((px_1  - stock_price) / stock_price, 6) AS r_p1,
    ROUND((px_3  - stock_price) / stock_price, 6) AS r_p3,
    ROUND((px_5  - stock_price) / stock_price, 6) AS r_p5,
    ROUND((px_10 - stock_price) / stock_price, 6) AS r_p10,
    ROUND((px_15 - stock_price) / stock_price, 6) AS r_p15,
    ROUND((px_20 - stock_price) / stock_price, 6) AS r_p20
FROM prices
WHERE asof_date >= '2023-01-01' AND asof_date < '2024-01-01'
ORDER BY symbol, asof_date
"""

INSERT_SQL = """
INSERT INTO orats_daily_returns (ticker, trade_date, px, r_p1, r_p3, r_p5, r_p10, r_p15, r_p20)
VALUES %s
ON CONFLICT DO NOTHING
"""

BATCH_SIZE = 10_000


def main():
    parser = argparse.ArgumentParser(description="Backfill 2023 forward returns")
    parser.add_argument("--dry-run", action="store_true", help="Compute but don't insert")
    args = parser.parse_args()

    print(f"Connecting to DB...")
    read_conn = psycopg2.connect(DATABASE_URL)
    write_conn = psycopg2.connect(DATABASE_URL)

    # Check existing 2023 data
    with read_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM orats_daily_returns WHERE trade_date < '2024-01-01'")
        existing = cur.fetchone()[0]
        if existing > 0:
            print(f"WARNING: {existing:,} rows already exist for 2023. ON CONFLICT will skip dupes.")

    # Compute forward returns
    print("Computing forward returns via LEAD() window function...")
    print("  (scanning orats_daily 2023-01-01 to 2024-02-15 for lookahead)")
    t0 = time.time()

    with read_conn.cursor(name="returns_cursor") as cur:
        cur.itersize = BATCH_SIZE
        cur.execute(COMPUTE_SQL)

        total = 0
        inserted = 0
        batch = []

        for row in cur:
            symbol, asof_date, stock_price, r_p1, r_p3, r_p5, r_p10, r_p15, r_p20 = row
            batch.append((symbol, asof_date, stock_price, r_p1, r_p3, r_p5, r_p10, r_p15, r_p20))
            total += 1

            if len(batch) >= BATCH_SIZE:
                if not args.dry_run:
                    with write_conn.cursor() as wcur:
                        execute_values(wcur, INSERT_SQL, batch, page_size=BATCH_SIZE)
                    write_conn.commit()
                    inserted += len(batch)
                elapsed = time.time() - t0
                print(f"  {total:>10,} rows computed | {inserted:>10,} inserted | {elapsed:.0f}s")
                batch = []

        # Final batch
        if batch:
            if not args.dry_run:
                with write_conn.cursor() as wcur:
                    execute_values(wcur, INSERT_SQL, batch, page_size=BATCH_SIZE)
                write_conn.commit()
                inserted += len(batch)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Total computed: {total:,}")
    print(f"  Inserted:       {inserted:,}")
    if args.dry_run:
        print("  (DRY RUN — nothing written)")

    # Verify
    if not args.dry_run:
        with read_conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(r_p1), COUNT(r_p20),
                       MIN(trade_date), MAX(trade_date)
                FROM orats_daily_returns
                WHERE trade_date >= '2023-01-01' AND trade_date < '2024-01-01'
            """)
            cnt, cnt_r1, cnt_r20, mn, mx = cur.fetchone()
            print(f"\nVerification:")
            print(f"  2023 rows:    {cnt:,}")
            print(f"  r_p1 filled:  {cnt_r1:,}")
            print(f"  r_p20 filled: {cnt_r20:,}")
            print(f"  Date range:   {mn} to {mx}")

    read_conn.close()
    write_conn.close()


if __name__ == "__main__":
    main()
