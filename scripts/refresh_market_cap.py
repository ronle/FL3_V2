"""
Market Cap Refresh Script

Fetches market_cap from Polygon ticker details API for all active symbols
in master_tickers where market_cap IS NULL (delta refresh).

Polygon free tier: 5 req/sec → ~20 min for ~5,980 symbols.

Usage:
    python -m scripts.refresh_market_cap [--limit N] [--dry-run] [--stats] [--all]

    --all       Re-fetch ALL symbols (not just NULL market_cap)
    --limit N   Process at most N symbols
    --dry-run   Show what would be updated without writing
    --stats     Show current market cap statistics
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io/v3/reference/tickers"


def get_polygon_api_key() -> str:
    """Resolve Polygon API key from env or Secret Manager."""
    val = os.environ.get("POLYGON_API_KEY")
    if val:
        return val.strip()

    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        name = f"projects/{project}/secrets/POLYGON_API_KEY/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("utf-8").strip()
    except Exception as e:
        raise ValueError(f"Cannot resolve POLYGON_API_KEY: {e}")


def get_db_connection():
    """Connect as fr3_app (owns master_tickers)."""
    # Check for explicit env override first
    db_url = os.environ.get("DATABASE_URL")
    if db_url and "fr3_app" in db_url:
        return psycopg2.connect(db_url.strip())

    # Default: Cloud SQL Auth Proxy on localhost:5433
    return psycopg2.connect(
        host="127.0.0.1",
        port=5433,
        dbname="fl3",
        user="fr3_app",
        password=r"cviHs9NaUqS45$0gjkBu2znKyFV!@LCTOQd18RDW",
    )


def get_symbols_missing_market_cap(conn, limit: Optional[int] = None, fetch_all: bool = False) -> list[str]:
    """Get active symbols needing market cap data."""
    cur = conn.cursor()
    if fetch_all:
        query = "SELECT symbol FROM master_tickers WHERE is_active = TRUE ORDER BY symbol"
    else:
        query = "SELECT symbol FROM master_tickers WHERE is_active = TRUE AND market_cap IS NULL ORDER BY symbol"
    if limit:
        query += f" LIMIT {limit}"
    cur.execute(query)
    symbols = [row[0] for row in cur.fetchall()]
    cur.close()
    return symbols


def fetch_market_cap(symbol: str, api_key: str, session: requests.Session) -> Optional[int]:
    """Fetch market_cap from Polygon /v3/reference/tickers/{ticker}."""
    url = f"{POLYGON_BASE}/{symbol}"
    try:
        resp = session.get(url, params={"apiKey": api_key}, timeout=10)
        if resp.status_code == 429:
            # Rate limited — caller should back off
            return -1  # sentinel
        if resp.status_code != 200:
            logger.debug(f"{symbol}: HTTP {resp.status_code}")
            return None
        data = resp.json()
        results = data.get("results", {})
        mc = results.get("market_cap")
        if mc is not None:
            return int(mc)
        return None
    except Exception as e:
        logger.warning(f"{symbol}: request error: {e}")
        return None


def update_market_cap(conn, symbol: str, market_cap: Optional[int]) -> bool:
    """Write market_cap to master_tickers."""
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE master_tickers
               SET market_cap = %s, market_cap_updated_at = NOW()
               WHERE symbol = %s""",
            (market_cap, symbol),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB update failed for {symbol}: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()


def run_refresh(limit: Optional[int] = None, dry_run: bool = False, fetch_all: bool = False):
    api_key = get_polygon_api_key()
    conn = get_db_connection()

    symbols = get_symbols_missing_market_cap(conn, limit, fetch_all)
    total = len(symbols)
    logger.info(f"Found {total} symbols to process (all={fetch_all})")

    if dry_run:
        logger.info("Dry run — first 20:")
        for s in symbols[:20]:
            logger.info(f"  {s}")
        conn.close()
        return

    if total == 0:
        logger.info("Nothing to update")
        conn.close()
        return

    session = requests.Session()
    updated = 0
    skipped = 0
    failed = 0
    rate_limit_hits = 0

    for i, symbol in enumerate(symbols):
        mc = fetch_market_cap(symbol, api_key, session)

        if mc == -1:
            # Rate limited — back off and retry once
            rate_limit_hits += 1
            logger.warning(f"Rate limited at {symbol}, sleeping 12s...")
            time.sleep(12)
            mc = fetch_market_cap(symbol, api_key, session)
            if mc == -1:
                logger.error("Still rate limited after backoff, stopping.")
                break

        if mc is not None:
            if update_market_cap(conn, symbol, mc):
                updated += 1
                if (i + 1) % 200 == 0 or i < 5:
                    logger.info(f"[{i+1}/{total}] {symbol}: ${mc:,}")
            else:
                failed += 1
        else:
            # No market_cap in Polygon (ETF, warrant, etc.) — stamp updated_at so we don't retry
            update_market_cap(conn, symbol, None)
            skipped += 1
            if (i + 1) % 200 == 0:
                logger.info(f"[{i+1}/{total}] {symbol}: no market_cap (ETF/warrant?)")

        # Polygon free tier: 5 req/sec → 200ms between requests
        time.sleep(0.22)

        if (i + 1) % 500 == 0:
            logger.info(f"Progress: {i+1}/{total} — {updated} updated, {skipped} no data, {failed} failed, {rate_limit_hits} rate limits")

    conn.close()
    session.close()

    logger.info("=" * 50)
    logger.info(f"Refresh complete:")
    logger.info(f"  Processed: {updated + skipped + failed}/{total}")
    logger.info(f"  Updated with market_cap: {updated}")
    logger.info(f"  No data (NULL): {skipped}")
    logger.info(f"  Failed: {failed}")
    logger.info(f"  Rate limit hits: {rate_limit_hits}")


def show_stats():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(market_cap) as with_cap,
            COUNT(*) FILTER (WHERE market_cap IS NULL AND market_cap_updated_at IS NOT NULL) as checked_null,
            COUNT(*) FILTER (WHERE market_cap IS NULL AND market_cap_updated_at IS NULL) as never_checked,
            MIN(market_cap_updated_at) as oldest_update,
            MAX(market_cap_updated_at) as newest_update
        FROM master_tickers
        WHERE is_active = TRUE
    """)
    row = cur.fetchone()

    logger.info("Market Cap Stats:")
    logger.info(f"  Total active:       {row[0]}")
    logger.info(f"  With market_cap:    {row[1]}")
    logger.info(f"  Checked (NULL):     {row[2]}")
    logger.info(f"  Never checked:      {row[3]}")
    logger.info(f"  Oldest update:      {row[4]}")
    logger.info(f"  Newest update:      {row[5]}")

    # Top 10 by market cap
    cur.execute("""
        SELECT symbol, market_cap
        FROM master_tickers
        WHERE is_active = TRUE AND market_cap IS NOT NULL
        ORDER BY market_cap DESC
        LIMIT 10
    """)
    rows = cur.fetchall()
    if rows:
        logger.info("\nTop 10 by market cap:")
        for sym, mc in rows:
            logger.info(f"  {sym}: ${mc:,}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh market_cap from Polygon API")
    parser.add_argument("--limit", type=int, help="Max symbols to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--all", action="store_true", help="Re-fetch all symbols (not just NULL)")
    parser.add_argument("--stats", action="store_true", help="Show current statistics")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    else:
        run_refresh(limit=args.limit, dry_run=args.dry_run, fetch_all=getattr(args, "all"))
