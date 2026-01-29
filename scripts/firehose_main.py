#!/usr/bin/env python3
"""
Firehose Main Orchestrator (Component 3.6)

Main orchestrator that ties together all firehose components:
- Websocket client (Polygon T.*)
- Rolling window aggregator
- UOA detector
- Trigger handler
- Bucket aggregator

Usage:
    python -m scripts.firehose_main
    python -m scripts.firehose_main --test-mode
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time as dt_time
from typing import Optional

import pytz

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from firehose.client import FirehoseClient, Trade
from firehose.aggregator import RollingAggregator
from firehose.bucket_aggregator import BucketAggregator
from uoa.detector_v2 import UOADetector, UOATrigger
from uoa.trigger_handler import TriggerHandler
from utils.occ_parser import extract_underlying

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Market hours (Eastern Time)
ET = pytz.timezone('America/New_York')
MARKET_OPEN = dt_time(9, 30)
MARKET_CLOSE = dt_time(16, 0)

# Health check interval
HEALTH_CHECK_INTERVAL = 60  # seconds


def get_market_status() -> tuple[str, str]:
    """Get current market status."""
    now = datetime.now(ET)

    # Weekend
    if now.weekday() >= 5:
        return "CLOSED", "Weekend"

    current_time = now.time()

    if current_time < dt_time(4, 0):
        return "CLOSED", "Pre-market not open"
    elif current_time < MARKET_OPEN:
        return "PRE_MARKET", f"Opens at {MARKET_OPEN}"
    elif current_time <= MARKET_CLOSE:
        mins_left = int((datetime.combine(now.date(), MARKET_CLOSE) - now.replace(tzinfo=None)).seconds / 60)
        return "OPEN", f"{mins_left} minutes until close"
    elif current_time < dt_time(20, 0):
        return "AFTER_HOURS", f"Closed at {MARKET_CLOSE}"
    else:
        return "CLOSED", "Market closed"


class FirehoseOrchestrator:
    """
    Main orchestrator for the firehose pipeline.

    Coordinates:
    - Websocket client
    - Rolling aggregator
    - UOA detection
    - Trigger handling
    - Bucket storage
    """

    def __init__(
        self,
        api_key: str,
        db_pool=None,
        test_mode: bool = False,
    ):
        """
        Initialize orchestrator.

        Args:
            api_key: Polygon API key
            db_pool: Database connection pool (optional)
            test_mode: Run outside market hours
        """
        self.api_key = api_key
        self.db_pool = db_pool
        self.test_mode = test_mode

        # Components
        self.client = FirehoseClient(api_key)
        self.rolling_agg = RollingAggregator(window_seconds=60)
        self.bucket_agg = BucketAggregator(db_pool=db_pool)
        self.detector = UOADetector(
            on_trigger=self._on_uoa_trigger,
            volume_threshold=3.0,
            cooldown_seconds=300,
        )
        self.trigger_handler = TriggerHandler(db_pool=db_pool)

        # State
        self._running = False
        self._trades_processed = 0
        self._last_health_check = 0
        self._pending_triggers: list[UOATrigger] = []

    async def run(self) -> None:
        """Run the firehose pipeline."""
        # Check market status
        status, msg = get_market_status()
        logger.info(f"Market Status: {status} - {msg}")

        if status not in ("OPEN", "PRE_MARKET", "AFTER_HOURS") and not self.test_mode:
            logger.warning("Market is closed. Use --test-mode to run anyway.")
            return

        if self.test_mode:
            logger.warning("Running in TEST MODE - market may be closed")

        self._running = True
        logger.info("Starting firehose pipeline...")

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        try:
            # Start background tasks
            health_task = asyncio.create_task(self._health_check_loop())
            bucket_task = asyncio.create_task(self._bucket_flush_loop())
            trigger_task = asyncio.create_task(self._trigger_process_loop())

            # Main trade processing loop
            async for trade in self.client.stream():
                if not self._running:
                    break

                await self._process_trade(trade)

            # Cleanup
            health_task.cancel()
            bucket_task.cancel()
            trigger_task.cancel()

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            raise

        finally:
            await self._shutdown()

    async def _process_trade(self, trade: Trade) -> None:
        """Process a single trade."""
        self._trades_processed += 1

        # Extract underlying
        underlying = extract_underlying(trade.symbol)
        if not underlying:
            return

        # Add to rolling aggregator
        self.rolling_agg.add_trade_fast(
            underlying=underlying,
            option_symbol=trade.symbol,
            price=trade.price,
            size=trade.size,
        )

        # Add to bucket aggregator
        self.bucket_agg.add_trade(
            underlying=underlying,
            option_symbol=trade.symbol,
            price=trade.price,
            size=trade.size,
        )

        # Check UOA periodically (every 100 trades per symbol)
        stats = self.rolling_agg.get_stats(underlying)
        if stats and stats.trade_count % 100 == 0:
            self.detector.check(
                symbol=underlying,
                trade_count=stats.trade_count,
                total_notional=stats.total_notional,
                total_contracts=stats.total_contracts,
            )

    def _on_uoa_trigger(self, trigger: UOATrigger) -> None:
        """Handle UOA trigger event."""
        logger.info(f"UOA TRIGGER: {trigger.symbol} {trigger.volume_ratio:.1f}x")
        self._pending_triggers.append(trigger)

    async def _trigger_process_loop(self) -> None:
        """Background loop to process triggers."""
        while self._running:
            if self._pending_triggers:
                trigger = self._pending_triggers.pop(0)
                try:
                    result = await self.trigger_handler.handle(trigger)
                    logger.info(f"Trigger handled: {trigger.symbol} success={result.success}")
                except Exception as e:
                    logger.error(f"Trigger handling error: {e}")

            await asyncio.sleep(0.1)

    async def _bucket_flush_loop(self) -> None:
        """Background loop to flush buckets."""
        while self._running:
            # Flush every 30 minutes
            await asyncio.sleep(60)  # Check every minute

            # Check if we should flush
            info = self.bucket_agg.get_current_bucket_info()
            if info['symbols_count'] > 0:
                now = datetime.now()
                if now.minute in (0, 30):  # Flush at :00 and :30
                    rows = await self.bucket_agg.flush()
                    if rows:
                        logger.info(f"Flushed {rows} bucket rows")

    async def _health_check_loop(self) -> None:
        """Background loop for health checks."""
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            self._log_health()

    def _log_health(self) -> None:
        """Log health metrics."""
        client_metrics = self.client.get_metrics()
        agg_metrics = self.rolling_agg.get_metrics()
        detector_metrics = self.detector.get_metrics()
        bucket_metrics = self.bucket_agg.get_metrics()

        logger.info(
            f"Health: trades={self._trades_processed:,} "
            f"rate={client_metrics['trades_per_second']:.1f}/s "
            f"symbols={agg_metrics['active_symbols']} "
            f"triggers={detector_metrics['total_triggers']} "
            f"lag={client_metrics['max_lag_ms']:.0f}ms"
        )

    def _signal_handler(self) -> None:
        """Handle shutdown signals."""
        logger.info("Shutdown signal received")
        self._running = False

    async def _shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down...")

        # Final bucket flush
        rows = await self.bucket_agg.flush()
        if rows:
            logger.info(f"Final flush: {rows} bucket rows")

        # Disconnect client
        await self.client.disconnect()

        # Log final metrics
        self._log_health()
        logger.info("Shutdown complete")


async def main():
    parser = argparse.ArgumentParser(description="FL3_V2 Firehose Pipeline")
    parser.add_argument("--test-mode", action="store_true", help="Run outside market hours")
    parser.add_argument("--duration", type=int, help="Run for N seconds then exit")
    args = parser.parse_args()

    # Get API key
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        logger.error("POLYGON_API_KEY environment variable not set")
        sys.exit(1)

    # Log startup
    logger.info("=" * 60)
    logger.info("FL3_V2 Firehose Pipeline Starting")
    logger.info("=" * 60)
    logger.info(f"Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S %Z')}")

    status, msg = get_market_status()
    logger.info(f"Market: {status} - {msg}")
    logger.info(f"Test Mode: {args.test_mode}")
    logger.info("=" * 60)

    # Run orchestrator
    orchestrator = FirehoseOrchestrator(
        api_key=api_key,
        test_mode=args.test_mode,
    )

    if args.duration:
        # Run for specified duration
        async def run_with_timeout():
            task = asyncio.create_task(orchestrator.run())
            await asyncio.sleep(args.duration)
            orchestrator._running = False
            await task

        await run_with_timeout()
    else:
        await orchestrator.run()


if __name__ == "__main__":
    asyncio.run(main())
