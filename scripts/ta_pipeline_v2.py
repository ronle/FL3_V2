#!/usr/bin/env python3
"""
TA Pipeline V2 Orchestrator (Component 4.4)

Orchestrates the 5-minute TA refresh cycle for all tracked symbols:
1. Get active symbols from TrackedTickersManager
2. Batch fetch bars from Alpaca
3. Calculate TA indicators
4. Store snapshots to database

Usage:
    python -m scripts.ta_pipeline_v2
    python -m scripts.ta_pipeline_v2 --once  # Single run
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, time as dt_time

import pytz

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracking.ticker_manager_v2 import TrackedTickersManager
from adapters.alpaca_bars_batch import AlpacaBarsFetcher
from analysis.ta_calculator import TACalculator, TASnapshot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Market hours (Eastern Time)
ET = pytz.timezone('America/New_York')
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)

# TA refresh interval
REFRESH_INTERVAL = 300  # 5 minutes

# Bars to fetch for TA calculation
BARS_TO_FETCH = 50  # Enough for RSI-14, ATR-14, SMA-20, EMA-9


def is_market_hours() -> bool:
    """Check if currently in market hours."""
    now = datetime.now(ET)

    # Weekend
    if now.weekday() >= 5:
        return False

    current_time = now.time()
    return MARKET_OPEN <= current_time <= MARKET_CLOSE


class TAPipelineOrchestrator:
    """
    Orchestrates the TA pipeline refresh cycle.

    Every 5 minutes during market hours:
    1. Get tracked symbols
    2. Fetch bars from Alpaca (batched)
    3. Calculate TA indicators
    4. Store to database

    Usage:
        orchestrator = TAPipelineOrchestrator(db_pool, api_key, secret_key)
        await orchestrator.run()  # Continuous refresh loop
        await orchestrator.run_once()  # Single refresh
    """

    def __init__(
        self,
        db_pool=None,
        alpaca_api_key: str = "",
        alpaca_secret_key: str = "",
        batch_size: int = 50,
    ):
        """
        Initialize orchestrator.

        Args:
            db_pool: Database connection pool
            alpaca_api_key: Alpaca API key
            alpaca_secret_key: Alpaca secret key
            batch_size: Symbols per Alpaca request
        """
        self.db_pool = db_pool
        self.batch_size = batch_size

        # Components
        self.ticker_manager = TrackedTickersManager(db_pool=db_pool)
        self.bars_fetcher = AlpacaBarsFetcher(
            api_key=alpaca_api_key,
            secret_key=alpaca_secret_key,
            max_symbols_per_request=batch_size,
        )
        self.ta_calculator = TACalculator()

        # State
        self._running = False
        self._last_refresh = None

        # Metrics
        self._total_refreshes = 0
        self._total_symbols_processed = 0
        self._total_errors = 0
        self._last_duration_ms = 0

    async def run(self) -> None:
        """Run continuous refresh loop."""
        self._running = True
        logger.info("Starting TA pipeline...")
        logger.info(f"Refresh interval: {REFRESH_INTERVAL}s")

        while self._running:
            try:
                # Check market hours
                if not is_market_hours():
                    logger.debug("Market closed, waiting...")
                    await asyncio.sleep(60)
                    continue

                # Run refresh
                start = datetime.now()
                await self.run_once()
                duration = (datetime.now() - start).total_seconds()
                self._last_duration_ms = int(duration * 1000)

                logger.info(
                    f"Refresh completed in {duration:.1f}s, "
                    f"next in {REFRESH_INTERVAL - duration:.0f}s"
                )

                # Wait for next interval
                sleep_time = max(0, REFRESH_INTERVAL - duration)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                logger.info("Pipeline cancelled")
                break
            except Exception as e:
                logger.error(f"Pipeline error: {e}")
                self._total_errors += 1
                await asyncio.sleep(60)  # Wait before retry

        await self._shutdown()

    async def run_once(self) -> dict:
        """
        Run a single refresh cycle.

        Returns:
            Dict with refresh stats
        """
        start_time = datetime.now()
        self._total_refreshes += 1

        # Step 1: Get active symbols
        symbols = await self.ticker_manager.get_active_symbols(ta_enabled_only=True)
        logger.info(f"Refreshing TA for {len(symbols)} symbols")

        if not symbols:
            return {"symbols": 0, "stored": 0, "duration_ms": 0}

        # Step 2: Fetch bars in batches
        all_bars = {}
        batches = [symbols[i:i + self.batch_size] for i in range(0, len(symbols), self.batch_size)]

        for i, batch in enumerate(batches):
            try:
                bars = await self.bars_fetcher.get_bars_batch(
                    symbols=batch,
                    timeframe="5Min",
                    limit=BARS_TO_FETCH,
                )
                all_bars.update(bars)
                logger.debug(f"Fetched batch {i+1}/{len(batches)}: {len(batch)} symbols")
            except Exception as e:
                logger.error(f"Batch {i+1} fetch failed: {e}")
                self._total_errors += 1

        # Step 3: Calculate TA
        snapshots = self.ta_calculator.calculate_batch(all_bars)
        valid_snapshots = [s for s in snapshots.values() if s.price > 0]

        # Step 4: Store to database
        stored_count = 0
        if self.db_pool and valid_snapshots:
            stored_count = await self._store_snapshots(valid_snapshots)

        # Update metrics
        duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
        self._total_symbols_processed += len(symbols)
        self._last_refresh = datetime.now()
        self._last_duration_ms = duration_ms

        return {
            "symbols": len(symbols),
            "fetched": len(all_bars),
            "valid": len(valid_snapshots),
            "stored": stored_count,
            "duration_ms": duration_ms,
        }

    async def _store_snapshots(self, snapshots: list[TASnapshot]) -> int:
        """Store TA snapshots to database."""
        if not self.db_pool:
            return 0

        now = datetime.now(ET)

        try:
            async with self.db_pool.acquire() as conn:
                # Batch insert
                rows = [
                    (s.symbol, now, s.price, s.volume, s.rsi_14, s.atr_14,
                     s.vwap, s.sma_20, s.ema_9)
                    for s in snapshots
                ]

                await conn.executemany("""
                    INSERT INTO ta_snapshots_v2
                    (symbol, snapshot_ts, price, volume, rsi_14, atr_14, vwap, sma_20, ema_9)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (symbol, snapshot_ts) DO UPDATE SET
                        price = EXCLUDED.price,
                        volume = EXCLUDED.volume,
                        rsi_14 = EXCLUDED.rsi_14,
                        atr_14 = EXCLUDED.atr_14,
                        vwap = EXCLUDED.vwap,
                        sma_20 = EXCLUDED.sma_20,
                        ema_9 = EXCLUDED.ema_9
                """, rows)

                logger.debug(f"Stored {len(rows)} TA snapshots")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to store snapshots: {e}")
            self._total_errors += 1
            return 0

    async def _shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down TA pipeline...")
        await self.bars_fetcher.close()
        logger.info("Shutdown complete")

    def stop(self) -> None:
        """Signal the pipeline to stop."""
        self._running = False

    def get_metrics(self) -> dict:
        """Get pipeline metrics."""
        return {
            "total_refreshes": self._total_refreshes,
            "total_symbols_processed": self._total_symbols_processed,
            "total_errors": self._total_errors,
            "last_refresh": self._last_refresh.isoformat() if self._last_refresh else None,
            "last_duration_ms": self._last_duration_ms,
            "fetcher_metrics": self.bars_fetcher.get_metrics(),
            "tracker_metrics": self.ticker_manager.get_metrics(),
        }


async def main():
    parser = argparse.ArgumentParser(description="FL3_V2 TA Pipeline")
    parser.add_argument("--once", action="store_true", help="Run single refresh and exit")
    parser.add_argument("--test", action="store_true", help="Run with mock data")
    args = parser.parse_args()

    # Get API keys
    alpaca_api_key = os.environ.get("ALPACA_API_KEY", "")
    alpaca_secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    if not alpaca_api_key and not args.test:
        logger.warning("ALPACA_API_KEY not set, running in test mode")
        args.test = True

    # Log startup
    now = datetime.now(ET)
    logger.info("=" * 60)
    logger.info("FL3_V2 TA Pipeline Starting")
    logger.info("=" * 60)
    logger.info(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Market Hours: {is_market_hours()}")
    logger.info(f"Mode: {'Test' if args.test else 'Production'}")
    logger.info("=" * 60)

    # Create orchestrator
    orchestrator = TAPipelineOrchestrator(
        db_pool=None,  # Would be set in production
        alpaca_api_key=alpaca_api_key,
        alpaca_secret_key=alpaca_secret_key,
    )

    if args.test:
        # Run test with mock symbols
        logger.info("Running test with mock data...")

        # Add mock symbols
        now = datetime.now()
        await orchestrator.ticker_manager.add_symbol("AAPL", now)
        await orchestrator.ticker_manager.add_symbol("TSLA", now)
        await orchestrator.ticker_manager.add_symbol("NVDA", now)
        await orchestrator.ticker_manager.add_symbol("MSFT", now)
        await orchestrator.ticker_manager.add_symbol("AMZN", now)

        # Run single refresh
        try:
            result = await orchestrator.run_once()
            logger.info(f"Test result: {result}")
            logger.info(f"Metrics: {orchestrator.get_metrics()}")
        finally:
            await orchestrator._shutdown()

    elif args.once:
        # Single production run
        result = await orchestrator.run_once()
        logger.info(f"Refresh result: {result}")

    else:
        # Continuous production run
        await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
