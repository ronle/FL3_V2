"""
Backfill 1-min OHLCV bars into spot_prices_1m for tracked symbols.

Usage (Cloud Run Job):
    python -m scripts.backfill_bars_1m [--days 7]

Fetches historical 1-min bars from Alpaca /v2/stocks/bars endpoint,
batches 100 symbols per request, paginates with next_page_token,
and UPSERTs into spot_prices_1m via asyncpg.

With upgraded Alpaca plan, no rate limit concerns.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import aiohttp
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ALPACA_DATA_URL = "https://data.alpaca.markets/v2"
BATCH_SIZE = 100  # symbols per API request
DB_BATCH_SIZE = 5000  # rows per DB insert


def _get_secret(name: str) -> str:
    """Get secret from env or GCP Secret Manager."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        resource = f"projects/{project}/secrets/{name}/versions/latest"
        response = client.access_secret_version(request={"name": resource})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Failed to get secret {name}: {e}")
        sys.exit(1)


def _get_db_url() -> str:
    """Get database URL, transform for Cloud SQL socket if needed."""
    url = _get_secret("DATABASE_URL")
    # Cloud Run uses unix socket; keep as-is
    return url


async def get_symbols(pool: asyncpg.Pool) -> list[str]:
    """Get all tracked symbols."""
    rows = await pool.fetch(
        "SELECT symbol FROM tracked_tickers_v2 WHERE ta_enabled = TRUE ORDER BY symbol"
    )
    return [r["symbol"] for r in rows]


async def fetch_bars_batch(
    session: aiohttp.ClientSession,
    symbols: list[str],
    start: str,
    end: str,
) -> list[tuple]:
    """
    Fetch 1-min bars for a batch of symbols with pagination.
    Returns list of tuples ready for DB insert.
    """
    url = f"{ALPACA_DATA_URL}/stocks/bars"
    all_rows = []
    page_token = None
    page = 0

    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": start,
            "end": end,
            "limit": "10000",
            "feed": "sip",
            "adjustment": "raw",
        }
        if page_token:
            params["page_token"] = page_token

        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"Alpaca API error {resp.status}: {text}")
                break

            data = await resp.json()
            bars = data.get("bars", {})

            for sym, bar_list in bars.items():
                for bar in bar_list:
                    try:
                        all_rows.append((
                            sym,
                            datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
                            float(bar["o"]),
                            float(bar["h"]),
                            float(bar["l"]),
                            float(bar["c"]),
                            int(bar["v"]),
                            float(bar["vw"]) if "vw" in bar else None,
                            int(bar["n"]) if "n" in bar else None,
                        ))
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Failed to parse bar for {sym}: {e}")

            page_token = data.get("next_page_token")
            page += 1

            if not page_token:
                break

    return all_rows


async def insert_bars(pool: asyncpg.Pool, rows: list[tuple]) -> int:
    """Bulk insert bars with UPSERT."""
    if not rows:
        return 0

    total_inserted = 0
    for i in range(0, len(rows), DB_BATCH_SIZE):
        batch = rows[i:i + DB_BATCH_SIZE]
        async with pool.acquire() as conn:
            await conn.executemany("""
                INSERT INTO spot_prices_1m
                    (symbol, bar_ts, open, high, low, close, volume, vwap, trade_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (symbol, bar_ts) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    vwap = EXCLUDED.vwap,
                    trade_count = EXCLUDED.trade_count
            """, batch)
        total_inserted += len(batch)

    return total_inserted


async def run(days: int):
    """Main backfill routine."""
    logger.info(f"Starting backfill: last {days} trading days of 1-min bars")

    # Calculate date range
    end = datetime.now(timezone.utc)
    # Add extra calendar days to account for weekends/holidays
    start = end - timedelta(days=days + (days // 5) * 3 + 3)
    start_str = start.strftime("%Y-%m-%dT00:00:00Z")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(f"Date range: {start_str} to {end_str}")

    # Connect to DB
    db_url = _get_db_url()
    pool = await asyncpg.create_pool(db_url, min_size=2, max_size=5)

    # Get symbols
    symbols = await get_symbols(pool)
    logger.info(f"Found {len(symbols)} tracked symbols")

    # Create API session
    api_key = _get_secret("ALPACA_API_KEY")
    secret_key = _get_secret("ALPACA_SECRET_KEY")
    session = aiohttp.ClientSession(headers={
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": secret_key,
    })

    total_bars = 0
    total_inserted = 0
    batch_count = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE

    try:
        for i in range(0, len(symbols), BATCH_SIZE):
            batch_num = i // BATCH_SIZE + 1
            batch = symbols[i:i + BATCH_SIZE]

            logger.info(f"Batch {batch_num}/{batch_count}: fetching {len(batch)} symbols...")
            rows = await fetch_bars_batch(session, batch, start_str, end_str)
            total_bars += len(rows)

            if rows:
                inserted = await insert_bars(pool, rows)
                total_inserted += inserted
                logger.info(f"  Fetched {len(rows)} bars, inserted {inserted}")
            else:
                logger.info(f"  No bars returned")

        logger.info("=" * 60)
        logger.info(f"Backfill complete!")
        logger.info(f"  Total bars fetched: {total_bars:,}")
        logger.info(f"  Total rows inserted: {total_inserted:,}")
        logger.info(f"  Symbols: {len(symbols)}")
        logger.info(f"  Date range: {start_str} to {end_str}")

    finally:
        await session.close()
        await pool.close()

    # Verify
    pool2 = await asyncpg.create_pool(db_url, min_size=1, max_size=2)
    try:
        row = await pool2.fetchrow("""
            SELECT COUNT(*) as total, COUNT(DISTINCT symbol) as symbols,
                   MIN(bar_ts) as min_ts, MAX(bar_ts) as max_ts
            FROM spot_prices_1m
        """)
        logger.info(f"  DB totals: {row['total']:,} rows, {row['symbols']} symbols, "
                     f"{row['min_ts']} to {row['max_ts']}")
    finally:
        await pool2.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill 1-min bars into spot_prices_1m")
    parser.add_argument("--days", type=int, default=7, help="Number of trading days to backfill")
    args = parser.parse_args()

    asyncio.run(run(args.days))


if __name__ == "__main__":
    main()
