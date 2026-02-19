"""
End-of-Day Position Closer

Closes all positions at 3:55 PM ET (5 minutes before market close).
"""

import asyncio
import logging
from datetime import datetime, time as dt_time
from typing import Callable, Optional

import pytz

from .config import TradingConfig, DEFAULT_CONFIG
from .position_manager import PositionManager

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")


class EODCloser:
    """
    Automatically closes all positions before market close.

    Runs as a background task that monitors time and triggers
    position closure at the configured exit time.
    """

    def __init__(
        self,
        position_manager: PositionManager,
        config: TradingConfig = DEFAULT_CONFIG,
        on_close_complete: Optional[Callable] = None,
    ):
        self.position_manager = position_manager
        self.config = config
        self.on_close_complete = on_close_complete

        self._running = False
        self._closed_today = False
        self._task: Optional[asyncio.Task] = None

    def _get_et_time(self) -> dt_time:
        """Get current time in ET."""
        return datetime.now(ET).time()

    def _get_et_datetime(self) -> datetime:
        """Get current datetime in ET."""
        return datetime.now(ET)

    def is_market_hours(self) -> bool:
        """Check if market is currently open."""
        now = self._get_et_time()
        return self.config.MARKET_OPEN <= now <= self.config.MARKET_CLOSE

    def should_close(self) -> bool:
        """Check if we should close positions now."""
        if self._closed_today:
            return False

        now = self._get_et_time()

        # Close at or after exit time (3:55 PM ET through end of day).
        # Previously required now < MARKET_CLOSE, but that missed the
        # window if the service started after 4 PM ET, leaving orphaned
        # positions on Alpaca overnight.
        return now >= self.config.EXIT_TIME

    def reset_daily(self):
        """Reset for new trading day."""
        self._closed_today = False
        logger.info("EOD closer reset for new day")

    async def close_positions(self):
        """Close all positions for EOD."""
        if self._closed_today:
            logger.info("Positions already closed today")
            return

        logger.info("EOD: Closing all positions...")

        # Get current positions
        positions = self.position_manager.active_trades
        if not positions:
            logger.info("No positions to close")
            self._closed_today = True
            return

        # Close all
        closed = await self.position_manager.close_all_positions(reason="eod")

        logger.info(f"EOD: Closed {len(closed)} positions")

        for trade in closed:
            logger.info(
                f"  {trade.symbol}: ${trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)"
            )

        # Calculate total P&L
        total_pnl = sum(t.pnl for t in closed if t.pnl)
        logger.info(f"EOD Total P&L: ${total_pnl:+.2f}")

        self._closed_today = True

        if self.on_close_complete:
            self.on_close_complete(closed)

    async def monitor_loop(self):
        """
        Background loop that monitors for EOD close time.

        Runs continuously during market hours.
        """
        self._running = True
        check_interval = 30  # Check every 30 seconds

        logger.info(f"EOD closer started. Exit time: {self.config.EXIT_TIME}")

        while self._running:
            try:
                # Check if we should close
                if self.should_close() and not self._closed_today:
                    await self.close_positions()

                # Wait before next check
                await asyncio.sleep(check_interval)

            except asyncio.CancelledError:
                logger.info("EOD closer cancelled")
                break
            except Exception as e:
                logger.error(f"EOD closer error: {e}")
                await asyncio.sleep(check_interval)

    def start(self):
        """Start the EOD closer background task."""
        if self._task and not self._task.done():
            logger.warning("EOD closer already running")
            return

        self._task = asyncio.create_task(self.monitor_loop())
        logger.info("EOD closer task started")

    def stop(self):
        """Stop the EOD closer."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("EOD closer stopped")

    async def force_close_now(self):
        """Force close all positions immediately (manual trigger)."""
        logger.warning("Manual EOD close triggered")
        self._closed_today = False  # Allow force close
        await self.close_positions()


def time_until_close(config: TradingConfig = DEFAULT_CONFIG) -> Optional[float]:
    """
    Get seconds until exit time.

    Returns None if market is closed or past exit time.
    """
    now = datetime.now(ET)
    today = now.date()

    # Check if weekend
    if now.weekday() >= 5:
        return None

    exit_dt = ET.localize(datetime.combine(today, config.EXIT_TIME))
    market_close_dt = ET.localize(datetime.combine(today, config.MARKET_CLOSE))

    if now >= market_close_dt:
        return None  # Market closed

    if now >= exit_dt:
        return 0  # Should close now

    return (exit_dt - now).total_seconds()


if __name__ == "__main__":
    print("EOD Closer Test")
    print("=" * 60)

    config = TradingConfig()

    print(f"Exit time: {config.EXIT_TIME}")
    print(f"Market close: {config.MARKET_CLOSE}")

    now_et = datetime.now(ET)
    print(f"Current time (ET): {now_et.strftime('%H:%M:%S')}")

    secs = time_until_close(config)
    if secs is None:
        print("Market is closed or past exit time")
    elif secs == 0:
        print("Should close positions NOW")
    else:
        mins = secs / 60
        print(f"Time until EOD close: {mins:.1f} minutes")
