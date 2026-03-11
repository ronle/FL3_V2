#!/usr/bin/env python3
"""
Baseline Refresh Job (Component 7.4)

Daily job to refresh baseline calculations and clean up old data.
Runs after market close (4:30 PM ET / 1:30 PM PST).

Tasks:
1. Recalculate 20-day rolling baseline averages
2. Clean up bucket data older than 30 days
3. Update baseline cache for next trading day
4. Refresh adv_14d (14-day average daily volume)
5. Refresh flow_signals (engulfing pattern + options flow alignment)

Usage:
    python -m scripts.refresh_baselines
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

import pytz

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Timezone
ET = pytz.timezone('America/New_York')


class BaselineRefreshJob:
    """
    Daily baseline refresh and cleanup job.

    Runs after market close to:
    1. Update rolling baseline averages
    2. Clean up stale data
    3. Generate health report
    """

    def __init__(self, db_pool=None):
        self.db_pool = db_pool
        self.stats = {
            'buckets_analyzed': 0,
            'rows_deleted': 0,
            'symbols_refreshed': 0,
            'errors': 0,
        }

    async def run(self) -> dict:
        """
        Run the full refresh job.

        Returns:
            dict with job statistics
        """
        start_time = datetime.now(ET)
        logger.info("=" * 60)
        logger.info("FL3_V2 Baseline Refresh Job")
        logger.info(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        logger.info("=" * 60)

        if not self.db_pool:
            logger.warning("No db_pool, running in dry-run mode")
            return await self._dry_run()

        try:
            # Step 1: Clean up old bucket data (> 30 days)
            await self._cleanup_old_buckets()

            # Step 2: Clean up old TA snapshots (> 7 days)
            await self._cleanup_old_ta_snapshots()

            # Step 3: Analyze baseline statistics
            await self._analyze_baselines()

            # Step 4: Generate health report
            report = await self._generate_health_report()

            # Step 5: Refresh ADV 14-day table (for engulfing dashboard)
            await self._refresh_adv_14d()

            # Step 6: Refresh flow_signals (engulfing + options flow alignment)
            await self._refresh_flow_signals()

        except Exception as e:
            logger.error(f"Refresh job failed: {e}")
            self.stats['errors'] += 1

        end_time = datetime.now(ET)
        duration = (end_time - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("Job Complete")
        logger.info(f"Duration: {duration:.1f}s")
        logger.info(f"Buckets analyzed: {self.stats['buckets_analyzed']}")
        logger.info(f"Rows deleted: {self.stats['rows_deleted']}")
        logger.info(f"Symbols refreshed: {self.stats['symbols_refreshed']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("=" * 60)

        return self.stats

    async def _cleanup_old_buckets(self):
        """Delete bucket data older than 30 days."""
        logger.info("Cleaning up old bucket data (> 30 days)...")

        cutoff_date = datetime.now(ET).date() - timedelta(days=30)

        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM intraday_baselines_30m
                    WHERE trade_date < $1
                """, cutoff_date)

                # Parse "DELETE X" result
                deleted = int(result.split()[-1]) if result else 0
                self.stats['rows_deleted'] += deleted
                logger.info(f"Deleted {deleted} old bucket rows")

        except Exception as e:
            logger.error(f"Bucket cleanup failed: {e}")
            self.stats['errors'] += 1

    async def _cleanup_old_ta_snapshots(self):
        """Delete TA snapshots older than 7 days."""
        logger.info("Cleaning up old TA snapshots (> 7 days)...")

        cutoff_ts = datetime.now(ET) - timedelta(days=7)

        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM ta_snapshots_v2
                    WHERE snapshot_ts < $1
                """, cutoff_ts)

                deleted = int(result.split()[-1]) if result else 0
                self.stats['rows_deleted'] += deleted
                logger.info(f"Deleted {deleted} old TA snapshot rows")

        except Exception as e:
            logger.error(f"TA cleanup failed: {e}")
            self.stats['errors'] += 1

    async def _analyze_baselines(self):
        """Analyze baseline statistics for reporting."""
        logger.info("Analyzing baseline statistics...")

        try:
            async with self.db_pool.acquire() as conn:
                # Count unique symbols with bucket data
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(DISTINCT symbol) as symbols,
                        COUNT(*) as total_buckets,
                        MIN(trade_date) as earliest,
                        MAX(trade_date) as latest
                    FROM intraday_baselines_30m
                    WHERE trade_date >= CURRENT_DATE - INTERVAL '20 days'
                """)

                if row:
                    self.stats['symbols_refreshed'] = row['symbols'] or 0
                    self.stats['buckets_analyzed'] = row['total_buckets'] or 0
                    logger.info(f"Found {row['symbols']} symbols with bucket data")
                    logger.info(f"Date range: {row['earliest']} to {row['latest']}")

        except Exception as e:
            logger.error(f"Baseline analysis failed: {e}")
            self.stats['errors'] += 1

    async def _generate_health_report(self) -> dict:
        """Generate health report for monitoring."""
        logger.info("Generating health report...")

        report = {
            'timestamp': datetime.now(ET).isoformat(),
            'status': 'healthy' if self.stats['errors'] == 0 else 'degraded',
        }

        try:
            async with self.db_pool.acquire() as conn:
                # Check table sizes
                tables = await conn.fetch("""
                    SELECT
                        relname as table_name,
                        n_live_tup as row_count
                    FROM pg_stat_user_tables
                    WHERE relname IN (
                        'intraday_baselines_30m',
                        'uoa_triggers_v2',
                        'gex_metrics_snapshot',
                        'pd_phase_signals',
                        'tracked_tickers_v2',
                        'ta_snapshots_v2'
                    )
                    ORDER BY relname
                """)

                report['tables'] = {r['table_name']: r['row_count'] for r in tables}

                for table, count in report['tables'].items():
                    logger.info(f"  {table}: {count:,} rows")

                # Check ORATS freshness
                orats = await conn.fetchrow("""
                    SELECT MAX(asof_date) as latest FROM orats_daily
                """)
                report['orats_latest'] = str(orats['latest']) if orats else None
                logger.info(f"  ORATS latest: {report['orats_latest']}")

        except Exception as e:
            logger.error(f"Health report failed: {e}")
            report['status'] = 'error'
            report['error'] = str(e)

        return report

    async def _refresh_adv_14d(self):
        """Compute 14-day average daily volume from spot_prices_1m and upsert into adv_14d."""
        logger.info("Refreshing adv_14d (14-day average daily volume)...")

        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute("""
                    INSERT INTO adv_14d (symbol, avg_volume, trading_days, computed_at)
                    SELECT
                        symbol,
                        ROUND(AVG(daily_vol))::bigint AS avg_volume,
                        COUNT(*)::integer AS trading_days,
                        NOW() AS computed_at
                    FROM (
                        SELECT symbol, bar_ts::date AS trade_date, SUM(volume) AS daily_vol
                        FROM spot_prices_1m
                        WHERE bar_ts >= CURRENT_DATE - INTERVAL '14 days'
                          AND bar_ts < CURRENT_DATE
                        GROUP BY symbol, bar_ts::date
                    ) daily
                    GROUP BY symbol
                    HAVING COUNT(*) >= 5
                    ON CONFLICT (symbol) DO UPDATE SET
                        avg_volume   = EXCLUDED.avg_volume,
                        trading_days = EXCLUDED.trading_days,
                        computed_at  = EXCLUDED.computed_at
                """)

                # Parse "INSERT X Y" result
                parts = result.split() if result else []
                upserted = int(parts[-1]) if parts else 0
                logger.info(f"adv_14d refreshed: {upserted} symbols upserted")

        except Exception as e:
            logger.error(f"adv_14d refresh failed: {e}")
            self.stats['errors'] += 1

    async def _refresh_flow_signals(self):
        """Join engulfing_scores (daily) with orats_daily options flow and upsert to flow_signals.

        Alignment rules:
        - Bullish: put_call_ratio <= 0.7 AND volume_zscore >= 1.5
        - Bearish: put_call_ratio >= 1.3 AND volume_zscore >= 1.5
        Also cleans up rows older than 30 days.
        """
        logger.info("Refreshing flow_signals (engulfing + options flow alignment)...")

        try:
            async with self.db_pool.acquire() as conn:
                # Upsert flow signals for recent daily patterns
                # Subquery deduplicates when a symbol has both bullish+bearish
                # on the same date. Picks the latest scan_ts per (symbol, date).
                result = await conn.execute("""
                    INSERT INTO flow_signals
                        (symbol, pattern_date, direction, iv_rank, volume_zscore,
                         put_call_ratio, flow_aligned, computed_at)
                    SELECT symbol, pattern_date, direction, iv_rank, volume_zscore,
                           put_call_ratio, flow_aligned, computed_at
                    FROM (
                        SELECT DISTINCT ON (es.symbol, es.pattern_date::DATE)
                            es.symbol,
                            es.pattern_date::DATE AS pattern_date,
                            es.direction,
                            od.iv_rank,
                            od.volume_zscore,
                            od.put_call_ratio,
                            CASE
                                WHEN es.direction = 'bullish'
                                     AND od.put_call_ratio <= 0.7
                                     AND od.volume_zscore >= 1.5 THEN TRUE
                                WHEN es.direction = 'bearish'
                                     AND od.put_call_ratio >= 1.3
                                     AND od.volume_zscore >= 1.5 THEN TRUE
                                ELSE FALSE
                            END AS flow_aligned,
                            NOW() AS computed_at
                        FROM engulfing_scores es
                        JOIN orats_daily od
                            ON od.symbol = es.symbol
                            AND od.asof_date = es.pattern_date::DATE
                        WHERE es.timeframe = '1D'
                          AND es.pattern_date::DATE >= CURRENT_DATE - INTERVAL '5 days'
                          AND od.put_call_ratio > 0
                        ORDER BY es.symbol, es.pattern_date::DATE, es.scan_ts DESC
                    ) sub
                    ON CONFLICT (symbol, pattern_date) DO UPDATE SET
                        direction      = EXCLUDED.direction,
                        iv_rank        = EXCLUDED.iv_rank,
                        volume_zscore  = EXCLUDED.volume_zscore,
                        put_call_ratio = EXCLUDED.put_call_ratio,
                        flow_aligned   = EXCLUDED.flow_aligned,
                        computed_at    = EXCLUDED.computed_at
                """)

                parts = result.split() if result else []
                upserted = int(parts[-1]) if parts else 0
                logger.info(f"flow_signals refreshed: {upserted} rows upserted")

                # Count aligned signals for reporting
                row = await conn.fetchrow("""
                    SELECT COUNT(*) as aligned
                    FROM flow_signals
                    WHERE flow_aligned = TRUE
                      AND pattern_date >= CURRENT_DATE - INTERVAL '1 day'
                """)
                if row:
                    logger.info(f"flow_signals aligned today: {row['aligned']}")

                # Cleanup old rows (> 30 days)
                cleanup = await conn.execute("""
                    DELETE FROM flow_signals WHERE pattern_date < CURRENT_DATE - INTERVAL '30 days'
                """)
                deleted = int(cleanup.split()[-1]) if cleanup else 0
                if deleted:
                    logger.info(f"flow_signals cleanup: {deleted} old rows deleted")
                    self.stats['rows_deleted'] += deleted

        except Exception as e:
            logger.error(f"flow_signals refresh failed: {e}")
            self.stats['errors'] += 1

    async def _dry_run(self) -> dict:
        """Simulate job without database."""
        logger.info("DRY RUN - No database connection")
        logger.info("Would clean up buckets > 30 days old")
        logger.info("Would clean up TA snapshots > 7 days old")
        logger.info("Would analyze baseline statistics")
        logger.info("Would refresh adv_14d")
        logger.info("Would refresh flow_signals")

        return {
            'mode': 'dry_run',
            'buckets_analyzed': 0,
            'rows_deleted': 0,
            'symbols_refreshed': 0,
            'errors': 0,
        }


async def create_db_pool():
    """Create database connection pool."""
    import asyncpg

    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.warning("DATABASE_URL not set")
        return None

    try:
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        logger.info("Database pool created")
        return pool
    except Exception as e:
        logger.error(f"Failed to create pool: {e}")
        return None


async def main():
    """Main entry point."""
    # Create database pool
    pool = await create_db_pool()

    try:
        # Run refresh job
        job = BaselineRefreshJob(db_pool=pool)
        stats = await job.run()

        # Exit with error if job had issues
        if stats.get('errors', 0) > 0:
            sys.exit(1)

    finally:
        if pool:
            await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
