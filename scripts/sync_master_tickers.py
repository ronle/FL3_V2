#!/usr/bin/env python3
"""
Sync master_tickers with unique symbols from orats_daily.

Run daily after ORATS ingest to ensure new tickers are added.
Can be called from refresh_baselines.py or run standalone.
"""

import asyncio
import os
import sys
import logging

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def sync_master_tickers(pool: asyncpg.Pool) -> dict:
    """
    Sync master_tickers with orats_daily symbols.

    Returns:
        dict with keys: added, updated, total
    """
    result = {"added": 0, "updated": 0, "total": 0}

    # Get count before
    before_count = await pool.fetchval("SELECT COUNT(*) FROM master_tickers")

    # Insert new symbols from orats_daily (last 30 days)
    insert_result = await pool.execute("""
        INSERT INTO master_tickers (symbol, name, is_active, source_tags)
        SELECT DISTINCT
            symbol,
            symbol as name,
            TRUE,
            ARRAY['orats_daily']
        FROM orats_daily
        WHERE asof_date > CURRENT_DATE - INTERVAL '30 days'
        ON CONFLICT (symbol) DO UPDATE SET
            last_seen = NOW(),
            is_active = TRUE,
            source_tags = CASE
                WHEN NOT ('orats_daily' = ANY(master_tickers.source_tags))
                THEN array_append(master_tickers.source_tags, 'orats_daily')
                ELSE master_tickers.source_tags
            END
    """)

    # Also add from tracked_tickers_v2
    await pool.execute("""
        INSERT INTO master_tickers (symbol, name, is_active, source_tags)
        SELECT DISTINCT
            symbol,
            symbol as name,
            TRUE,
            ARRAY['tracked_v2']
        FROM tracked_tickers_v2
        ON CONFLICT (symbol) DO UPDATE SET
            last_seen = NOW(),
            is_active = TRUE
    """)

    # Get count after
    after_count = await pool.fetchval("SELECT COUNT(*) FROM master_tickers")

    result["added"] = after_count - before_count
    result["total"] = after_count

    # Count recently updated (last_seen today)
    result["updated"] = await pool.fetchval("""
        SELECT COUNT(*) FROM master_tickers
        WHERE last_seen::date = CURRENT_DATE
    """)

    return result


async def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=2)

    logger.info("=" * 60)
    logger.info("MASTER_TICKERS DAILY SYNC")
    logger.info("=" * 60)

    result = await sync_master_tickers(pool)

    logger.info(f"New tickers added: {result['added']}")
    logger.info(f"Tickers updated today: {result['updated']}")
    logger.info(f"Total tickers: {result['total']}")

    await pool.close()
    logger.info("Sync complete")


if __name__ == "__main__":
    asyncio.run(main())
