"""
Sector Data Refresh Script

Fetches sector/industry data from yfinance for symbols missing this data.
Only updates symbols where sector IS NULL (delta refresh).

Usage:
    python -m scripts.refresh_sector_data [--limit N] [--dry-run]
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from typing import List, Tuple, Optional

import psycopg2
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Get database connection (supports Cloud SQL socket or standard URL)."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL not set")

    # Handle URL-encoded passwords
    import urllib.parse

    # Check if using Cloud SQL socket (contains /cloudsql/)
    if "/cloudsql/" in db_url:
        # Parse the URL and extract components
        # Format: postgresql://user:pass@/dbname?host=/cloudsql/project:region:instance
        parsed = urllib.parse.urlparse(db_url)

        # Extract host parameter from query string or path
        query_params = urllib.parse.parse_qs(parsed.query)
        socket_path = query_params.get('host', [None])[0]

        if not socket_path and parsed.path:
            # Try to extract from path format: @/dbname?host=/cloudsql/...
            pass

        # Decode password if URL-encoded
        password = urllib.parse.unquote(parsed.password) if parsed.password else None

        return psycopg2.connect(
            user=parsed.username,
            password=password,
            database=parsed.path.lstrip('/').split('?')[0],
            host=socket_path or '/cloudsql/spartan-buckeye-474319-q8:us-west1:fr3-pg'
        )
    else:
        # Standard connection string
        return psycopg2.connect(db_url.strip())


def get_symbols_missing_sector(conn, limit: Optional[int] = None) -> List[str]:
    """Get symbols that need sector data (NULL sector)."""
    cur = conn.cursor()

    query = """
        SELECT symbol
        FROM master_tickers
        WHERE is_active = TRUE
          AND sector IS NULL
        ORDER BY last_seen DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    cur.execute(query)
    symbols = [row[0] for row in cur.fetchall()]
    cur.close()
    return symbols


def fetch_sector_from_yfinance(symbol: str, max_retries: int = 2) -> Tuple[Optional[str], Optional[str]]:
    """Fetch sector and industry from yfinance with retry logic."""
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info

            sector = info.get('sector')
            industry = info.get('industry')

            # yfinance returns None or empty string for ETFs/funds
            if sector in (None, '', 'N/A'):
                sector = None
            if industry in (None, '', 'N/A'):
                industry = None

            return sector, industry
        except Exception as e:
            if attempt < max_retries - 1:
                logger.debug(f"Retry {attempt + 1} for {symbol}: {e}")
                time.sleep(1)
            else:
                logger.warning(f"Failed to fetch {symbol} after {max_retries} attempts: {e}")
                return None, None
    return None, None


def update_sector_data(conn, symbol: str, sector: Optional[str], industry: Optional[str]) -> bool:
    """Update sector data for a symbol."""
    cur = conn.cursor()
    try:
        # Even if sector is None, mark as updated so we don't retry constantly
        # Use a placeholder like 'Unknown' for ETFs/funds that don't have sector
        cur.execute("""
            UPDATE master_tickers
            SET sector = COALESCE(%s, 'Unknown'),
                industry = %s,
                sector_updated_at = NOW()
            WHERE symbol = %s
        """, (sector, industry, symbol))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to update {symbol}: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()


def run_refresh(limit: Optional[int] = None, dry_run: bool = False, batch_size: int = 100):
    """Run the sector data refresh."""
    conn = get_db_connection()

    # Get symbols needing sector data
    symbols = get_symbols_missing_sector(conn, limit)
    total = len(symbols)

    logger.info(f"Found {total} symbols missing sector data")

    if dry_run:
        logger.info("Dry run - showing first 20 symbols:")
        for s in symbols[:20]:
            logger.info(f"  {s}")
        conn.close()
        return

    if total == 0:
        logger.info("No symbols to update")
        conn.close()
        return

    # Process in batches
    updated = 0
    failed = 0
    unknown = 0

    for i, symbol in enumerate(symbols):
        sector, industry = fetch_sector_from_yfinance(symbol)

        if update_sector_data(conn, symbol, sector, industry):
            updated += 1
            if sector is None:
                unknown += 1
                logger.debug(f"[{i+1}/{total}] {symbol}: Unknown (ETF/fund?)")
            else:
                logger.info(f"[{i+1}/{total}] {symbol}: {sector} / {industry}")
        else:
            failed += 1

        # Rate limiting - yfinance recommends not hammering
        if (i + 1) % batch_size == 0:
            logger.info(f"Progress: {i+1}/{total} ({updated} updated, {unknown} unknown, {failed} failed)")
            time.sleep(1)  # Brief pause between batches

        # Small delay between requests
        time.sleep(0.1)

    conn.close()

    logger.info("=" * 50)
    logger.info(f"Refresh complete:")
    logger.info(f"  Total processed: {total}")
    logger.info(f"  Updated with sector: {updated - unknown}")
    logger.info(f"  Marked as Unknown: {unknown}")
    logger.info(f"  Failed: {failed}")


def get_sector_stats(conn) -> dict:
    """Get current sector data statistics."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(sector) as with_sector,
            COUNT(*) FILTER (WHERE sector IS NULL) as missing,
            COUNT(*) FILTER (WHERE sector = 'Unknown') as unknown
        FROM master_tickers
        WHERE is_active = TRUE
    """)
    row = cur.fetchone()
    cur.close()
    return {
        'total': row[0],
        'with_sector': row[1],
        'missing': row[2],
        'unknown': row[3]
    }


def show_sector_distribution(conn):
    """Show distribution of symbols by sector."""
    cur = conn.cursor()
    cur.execute("""
        SELECT sector, COUNT(*) as count
        FROM master_tickers
        WHERE is_active = TRUE AND sector IS NOT NULL
        GROUP BY sector
        ORDER BY count DESC
    """)
    rows = cur.fetchall()
    cur.close()

    logger.info("\nSector Distribution:")
    logger.info("-" * 40)
    for sector, count in rows:
        logger.info(f"  {sector}: {count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh sector data from yfinance")
    parser.add_argument("--limit", type=int, help="Limit number of symbols to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--stats", action="store_true", help="Show current sector data statistics")
    parser.add_argument("--distribution", action="store_true", help="Show sector distribution")

    args = parser.parse_args()

    if args.stats or args.distribution:
        conn = get_db_connection()
        if args.stats:
            stats = get_sector_stats(conn)
            logger.info(f"Sector Data Stats:")
            logger.info(f"  Total active symbols: {stats['total']}")
            logger.info(f"  With sector data: {stats['with_sector']}")
            logger.info(f"  Missing sector: {stats['missing']}")
            logger.info(f"  Marked as Unknown: {stats['unknown']}")
        if args.distribution:
            show_sector_distribution(conn)
        conn.close()
    else:
        run_refresh(limit=args.limit, dry_run=args.dry_run)
