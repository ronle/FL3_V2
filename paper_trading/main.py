"""
Paper Trading Main Orchestrator

Coordinates all paper trading components:
1. Connect to firehose for real-time signals
2. Apply filters (Score >= 10, Uptrend, RSI < 50, $50K+)
3. Manage positions (max 3 concurrent)
4. Close all positions at 3:55 PM ET
5. Track and log all trades

Usage:
    python -m paper_trading.main
    python -m paper_trading.main --dry-run
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from datetime import datetime, date, time as dt_time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Dict

import pytz

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from paper_trading.config import TradingConfig, DEFAULT_CONFIG
from paper_trading.alpaca_trader import AlpacaTrader
from paper_trading.position_manager import PositionManager
from paper_trading.signal_filter import SignalFilter, Signal, SignalGenerator
from paper_trading.eod_closer import EODCloser, time_until_close
from paper_trading.trade_aggregator import TradeAggregator
from paper_trading.dashboard import get_dashboard

from firehose.client import FirehoseClient, Trade

ET = pytz.timezone("America/New_York")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("paper_trading.log"),
    ]
)
logger = logging.getLogger(__name__)


class PaperTradingEngine:
    """
    Main paper trading engine.

    Coordinates firehose, signal filtering, position management,
    and EOD closing.
    """

    def __init__(
        self,
        polygon_api_key: str,
        alpaca_api_key: str,
        alpaca_secret_key: str,
        config: TradingConfig = DEFAULT_CONFIG,
        dry_run: bool = False,
    ):
        self.config = config
        self.dry_run = dry_run

        # Initialize components
        self.firehose = FirehoseClient(polygon_api_key)
        self.aggregator = TradeAggregator()

        self.trader = AlpacaTrader(alpaca_api_key, alpaca_secret_key, config)
        self.position_manager = PositionManager(self.trader, config)
        self.signal_filter = SignalFilter(config)
        self.signal_generator = SignalGenerator()

        self.eod_closer = EODCloser(
            self.position_manager,
            config,
            on_close_complete=self._on_eod_complete,
        )

        # State
        self._running = False
        self._last_daily_reset: Optional[date] = None
        self._eod_complete = False  # Flag to stop signal processing after EOD close

        # TA cache (loaded at startup)
        self._ta_cache: Dict[str, Dict] = {}

    def _get_et_now(self) -> datetime:
        """Get current time in ET."""
        return datetime.now(ET)

    def _is_trading_hours(self) -> bool:
        """Check if within trading hours."""
        now = self._get_et_now().time()
        return self.config.MARKET_OPEN <= now <= self.config.MARKET_CLOSE

    def _check_daily_reset(self):
        """Reset daily state if new trading day."""
        today = self._get_et_now().date()
        now_time = self._get_et_now().time()

        if self._last_daily_reset != today:
            logger.info(f"New trading day: {today}")
            self.position_manager.reset_daily()
            self.signal_filter.reset_stats()
            self.eod_closer.reset_daily()
            self._eod_complete = False  # Re-enable signal processing for new day
            self._last_daily_reset = today

            # Only clear dashboard if we're in pre-market (before 9:30 AM)
            # This prevents wiping data on mid-day restarts
            if now_time < self.config.MARKET_OPEN:
                dashboard = get_dashboard()
                if dashboard.enabled:
                    logger.info("Pre-market: clearing dashboard for new day")
                    dashboard.clear_daily()
            else:
                logger.info("Mid-day restart: preserving dashboard data")

    async def load_ta_cache(self):
        """
        Load prior-day TA data from JSON file.

        This should be pre-computed daily before market open.
        """
        ta_file = Path(__file__).parent.parent / "polygon_data" / "daily_ta_cache.json"

        if ta_file.exists():
            try:
                with open(ta_file) as f:
                    data = json.load(f)
                    self._ta_cache = data.get("ta_data", {})
                    logger.info(f"Loaded TA cache: {len(self._ta_cache)} symbols")
            except Exception as e:
                logger.error(f"Failed to load TA cache: {e}")
        else:
            logger.warning(f"TA cache not found: {ta_file}")

        self.signal_generator.load_ta_cache(self._ta_cache)

    async def _process_trade(self, trade: Trade):
        """Process a single trade from firehose."""
        # Add to aggregator
        self.aggregator.add_trade(trade)

    def _is_entry_allowed(self) -> bool:
        """Check if we're within the time window to open new positions."""
        now = self._get_et_now().time()
        return self.config.MARKET_OPEN <= now <= self.config.LAST_ENTRY_TIME

    async def _check_for_signals(self):
        """
        Check aggregated data for signals that meet criteria.

        This runs periodically to evaluate accumulated trades.
        """
        # Don't open new positions after EOD close or after LAST_ENTRY_TIME
        if self._eod_complete:
            return
        if not self._is_entry_allowed():
            return

        # Get symbols with elevated activity
        triggered = self.aggregator.get_triggered_symbols()

        for symbol, stats in triggered.items():
            # Skip if already traded or have position
            if self.position_manager.already_traded(symbol):
                continue
            if self.position_manager.has_position(symbol):
                continue
            if not self.position_manager.can_open_position:
                logger.info("Max positions reached, skipping signal check")
                break

            # Create signal from aggregated stats (with dynamic TA fetch if needed)
            signal = await self.signal_generator.create_signal_async(
                symbol=symbol,
                score=stats.get("score", 0),
                notional=stats.get("notional", 0),
                contracts=stats.get("contracts", 0),
                price=stats.get("price", 0),
                trend=stats.get("trend", 0),
                # Score breakdown
                ratio=stats.get("ratio", 0),
                call_pct=stats.get("call_pct", 0),
                sweep_pct=stats.get("sweep_pct", 0),
                num_strikes=stats.get("num_strikes", 0),
                score_volume=stats.get("score_volume", 0),
                score_call_pct=stats.get("score_call_pct", 0),
                score_sweep=stats.get("score_sweep", 0),
                score_strikes=stats.get("score_strikes", 0),
                score_notional=stats.get("score_notional", 0),
            )

            # Apply filter
            result = self.signal_filter.apply(signal)
            self.position_manager.record_signal(result.passed)

            if result.passed:
                logger.info(f"Signal passed filter: {symbol}")

                if self.dry_run:
                    logger.info(f"[DRY RUN] Would open position: {symbol}")
                else:
                    # Open position
                    trade = await self.position_manager.open_position(
                        symbol=signal.symbol,
                        signal_score=signal.score,
                        signal_rsi=signal.rsi_14_prior or 0,
                        signal_notional=signal.notional,
                    )

                    if trade:
                        logger.info(
                            f"Position opened: {trade.symbol} "
                            f"{trade.shares} shares @ ${trade.entry_price:.2f}"
                        )

    async def _check_hard_stops(self):
        """Check if any positions hit hard stop."""
        if self.dry_run:
            return

        stopped = await self.position_manager.check_hard_stops()
        for symbol in stopped:
            logger.warning(f"Hard stop triggered: {symbol}")

    def _on_eod_complete(self, closed_trades):
        """Callback when EOD close completes."""
        logger.info(f"EOD close complete: {len(closed_trades)} positions closed")

        # CRITICAL: Stop all signal processing after EOD close
        self._eod_complete = True
        logger.info("Signal processing disabled until next trading day")

        # Log daily summary
        summary = self.position_manager.get_daily_summary()
        logger.info(f"Daily Summary: {json.dumps(summary, indent=2)}")

        # Save to file
        self._save_daily_log(summary, closed_trades)

    def _save_daily_log(self, summary: Dict, trades):
        """Save daily trading log to file."""
        log_dir = Path(__file__).parent.parent / "paper_trading_logs"
        log_dir.mkdir(exist_ok=True)

        today = date.today().isoformat()
        log_file = log_dir / f"{today}.json"

        log_data = {
            "date": today,
            "summary": summary,
            "trades": [
                {
                    "symbol": t.symbol,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                    "entry_price": t.entry_price,
                    "shares": t.shares,
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                    "signal_score": t.signal_score,
                    "signal_rsi": t.signal_rsi,
                }
                for t in trades
            ],
        }

        with open(log_file, "w") as f:
            json.dump(log_data, f, indent=2)

        logger.info(f"Daily log saved: {log_file}")

    async def run(self):
        """
        Main run loop.

        Connects to firehose, processes trades, checks for signals,
        and manages positions.
        """
        logger.info("=" * 60)
        logger.info("Paper Trading Engine Starting")
        logger.info(f"Config: Score >= {self.config.SCORE_THRESHOLD}, "
                   f"RSI < {self.config.RSI_THRESHOLD}, "
                   f"Notional >= ${self.config.MIN_NOTIONAL:,}")
        logger.info(f"Max positions: {self.config.MAX_CONCURRENT_POSITIONS}")
        logger.info(f"Exit time: {self.config.EXIT_TIME}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)

        # Load TA cache
        await self.load_ta_cache()

        # Check account
        if not self.dry_run:
            account = await self.trader.get_account()
            if account:
                logger.info(f"Account: ${account.portfolio_value:,.2f} "
                           f"(${account.buying_power:,.2f} buying power)")
            else:
                logger.error("Failed to connect to Alpaca account")
                return

            # CRITICAL: Sync existing positions before starting
            # This prevents duplicate trades if we restart with open positions
            await self.position_manager.sync_on_startup()

        # Start EOD closer
        self.eod_closer.start()

        self._running = True
        signal_check_interval = self.config.SIGNAL_CHECK_INTERVAL_SEC
        stop_check_interval = self.config.POSITION_CHECK_INTERVAL_SEC

        last_signal_check = 0
        last_stop_check = 0

        try:
            async for trade in self.firehose.stream():
                if not self._running:
                    break

                # Check for daily reset
                self._check_daily_reset()

                # Skip if outside trading hours
                if not self._is_trading_hours():
                    continue

                # Process trade
                await self._process_trade(trade)

                # Periodic signal check
                now = asyncio.get_event_loop().time()
                if now - last_signal_check >= signal_check_interval:
                    await self._check_for_signals()
                    last_signal_check = now

                # Periodic stop check
                if now - last_stop_check >= stop_check_interval:
                    await self._check_hard_stops()
                    last_stop_check = now

        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        except Exception as e:
            logger.error(f"Engine error: {e}")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")

        self._running = False
        self.eod_closer.stop()

        # Close any remaining positions if during market hours
        if self._is_trading_hours() and not self.dry_run:
            logger.warning("Closing positions on shutdown...")
            await self.position_manager.close_all_positions(reason="shutdown")

        await self.firehose.disconnect()
        await self.trader.close()

        # Log final stats
        logger.info(f"Signal filter stats: {self.signal_filter.get_stats()}")
        logger.info(f"Daily summary: {self.position_manager.get_daily_summary()}")

        logger.info("Shutdown complete")


# Global health status for HTTP health check
_health_status = {"status": "starting", "trades": 0, "positions": 0, "last_update": None}


class HealthHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for Cloud Run health checks."""

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            _health_status["last_update"] = datetime.now(ET).isoformat()
            self.wfile.write(json.dumps(_health_status).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


def start_health_server(port: int = 8080):
    """Start HTTP health check server in background thread."""
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server started on port {port}")
    return server


async def main():
    """Entry point."""
    parser = argparse.ArgumentParser(description="Paper Trading Engine")
    parser.add_argument("--dry-run", action="store_true",
                       help="Run without actually trading")
    parser.add_argument("--test", action="store_true",
                       help="Run quick connectivity test")
    args = parser.parse_args()

    # Get API keys from environment
    polygon_key = os.environ.get("POLYGON_API_KEY")
    alpaca_key = os.environ.get("ALPACA_API_KEY")
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")

    if not polygon_key:
        logger.error("POLYGON_API_KEY not set")
        sys.exit(1)

    if not alpaca_key or not alpaca_secret:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        sys.exit(1)

    if args.test:
        # Quick connectivity test
        logger.info("=" * 60)
        logger.info("CONNECTIVITY TEST")
        logger.info("=" * 60)

        # Test Alpaca
        logger.info("\n1. Testing Alpaca connection...")
        trader = AlpacaTrader(alpaca_key, alpaca_secret)
        account = await trader.get_account()

        if account:
            logger.info(f"   Alpaca OK: ${account.portfolio_value:,.2f} equity")
            logger.info(f"   Buying power: ${account.buying_power:,.2f}")

            # Check positions
            positions = await trader.get_positions()
            logger.info(f"   Open positions: {len(positions)}")
        else:
            logger.error("   Alpaca FAILED - check credentials")

        await trader.close()

        # Test firehose with timeout (may fail outside market hours)
        logger.info("\n2. Testing Polygon firehose (10 second timeout)...")
        logger.info("   Note: Options firehose may be empty outside market hours")

        firehose = FirehoseClient(polygon_key)

        try:
            # Use asyncio timeout to prevent hanging
            async def test_firehose():
                trade_count = 0
                async for trade in firehose.stream():
                    trade_count += 1
                    if trade_count >= 5:  # Got some trades, success
                        break
                return trade_count

            trade_count = await asyncio.wait_for(test_firehose(), timeout=10)
            logger.info(f"   Firehose OK: received {trade_count} trades")

        except asyncio.TimeoutError:
            logger.info("   Firehose timeout (normal if market closed)")
            logger.info(f"   Connection stats: {firehose.get_metrics()}")

        except Exception as e:
            logger.error(f"   Firehose error: {e}")

        finally:
            await firehose.disconnect()

        logger.info("\n" + "=" * 60)
        logger.info("TEST COMPLETE")
        logger.info("=" * 60)

        return

    # Start health check server for Cloud Run
    health_port = int(os.environ.get("PORT", 8080))
    start_health_server(health_port)
    _health_status["status"] = "running"

    # Run main engine
    engine = PaperTradingEngine(
        polygon_api_key=polygon_key,
        alpaca_api_key=alpaca_key,
        alpaca_secret_key=alpaca_secret,
        dry_run=args.dry_run,
    )

    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
