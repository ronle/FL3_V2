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
import re
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
from paper_trading.dashboard import get_dashboard, Dashboard
from paper_trading.engulfing_checker import PatternPoller
from paper_trading.rsi_screener import MomentumScreener
from paper_trading.cameron_checker import CameronChecker
from paper_trading.cameron_scanner import CameronScanner

from firehose.client import FirehoseClient, Trade
from firehose.stock_price_monitor import StockPriceMonitor
from firehose.bucket_aggregator import BucketAggregator
from firehose.aggregator import RollingAggregator
from firehose.hot_options_detector import HotOptionsDetector
from paper_trading.bar_collector import IntradayBarCollector

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
        self.polygon_api_key = polygon_api_key
        self._alpaca_api_key = alpaca_api_key
        self._alpaca_secret_key = alpaca_secret_key

        # Initialize components
        self.firehose = FirehoseClient(polygon_api_key)
        self.aggregator = TradeAggregator()

        # 5-min rolling aggregator for hot options detection
        self.rolling_agg_5m = RollingAggregator(window_seconds=300)
        self.hot_detector: Optional[HotOptionsDetector] = None  # Initialized after DB pool

        # Stock price monitor for real-time prices (PROD-1)
        self.stock_monitor = StockPriceMonitor(
            api_key=alpaca_api_key,
            secret_key=alpaca_secret_key,
            subscribe_trades=True,
            subscribe_quotes=True,
        )
        self.stock_monitor.on_price_update = self._on_stock_price_update
        self.stock_monitor.on_connect = self._on_websocket_connect
        self.stock_monitor.on_disconnect = self._on_websocket_disconnect

        self.trader = AlpacaTrader(alpaca_api_key, alpaca_secret_key, config)
        self.position_manager = PositionManager(self.trader, config)
        self.signal_filter = SignalFilter(config)
        self.signal_generator = SignalGenerator(
            database_url=os.environ.get("DATABASE_URL")
        )

        self.eod_closer = EODCloser(
            self.position_manager,
            config,
            on_close_complete=self._on_eod_complete,
        )

        # Account B — Big-Hitter Pattern Trader
        self._account_b_enabled = False
        self.dashboard_b = None
        if config.USE_ACCOUNT_B:
            alpaca_key_b = os.environ.get("ALPACA_API_KEY_B")
            alpaca_secret_b = os.environ.get("ALPACA_SECRET_KEY_B")
            if alpaca_key_b and alpaca_secret_b:
                db_url = os.environ.get("DATABASE_URL")
                self.trader_b = AlpacaTrader(alpaca_key_b, alpaca_secret_b, config)
                self.dashboard_b = Dashboard(tab_prefix="Account B ")
                self.position_manager_b = PositionManager(
                    self.trader_b, config,
                    trades_table="paper_trades_log_b",
                    skip_dashboard=False,
                    dashboard=self.dashboard_b,
                )
                self.pattern_poller = PatternPoller(
                    database_url=db_url,
                    max_candle_range=config.ACCOUNT_B_MAX_CANDLE_RANGE,
                    min_risk_per_share=config.ACCOUNT_B_MIN_RISK_PER_SHARE,
                    lookback_min=config.ACCOUNT_B_LOOKBACK_MIN,
                    filter_weak=config.ACCOUNT_B_FILTER_WEAK,
                )
                self.eod_closer_b = EODCloser(
                    self.position_manager_b, config,
                    on_close_complete=self._on_eod_complete_b,
                    use_prior_day_close=False,  # Account B always closes all at EOD
                )
                self._account_b_enabled = True
                logger.info("Account B (Big-Hitter Pattern Trader) initialized")
            else:
                logger.warning("Account B: ALPACA keys not found, disabled")

        # Account C — Cameron B2 Pattern Trader
        self._account_c_enabled = False
        self.dashboard_c = None
        if config.USE_ACCOUNT_C:
            alpaca_key_c = os.environ.get("ALPACA_API_KEY_C")
            alpaca_secret_c = os.environ.get("ALPACA_SECRET_KEY_C")
            if alpaca_key_c and alpaca_secret_c:
                db_url = os.environ.get("DATABASE_URL")
                self.trader_c = AlpacaTrader(alpaca_key_c, alpaca_secret_c, config)
                self.dashboard_c = Dashboard(tab_prefix="Cameron ")
                self.position_manager_c = PositionManager(
                    self.trader_c, config,
                    trades_table="paper_trades_log_c",
                    skip_dashboard=False,
                    dashboard=self.dashboard_c,
                )
                self.cameron_checker = CameronChecker(
                    database_url=db_url,
                    max_bf_per_day=config.CAMERON_MAX_BF_PER_DAY,
                    lookback_min=10,
                )
                # Scanner initialized in run() after db_pool is created
                self.cameron_scanner: Optional[CameronScanner] = None
                self.eod_closer_c = EODCloser(
                    self.position_manager_c, config,
                    on_close_complete=self._on_eod_complete_c,
                    use_prior_day_close=False,  # Account C always closes all at EOD
                )
                self._account_c_enabled = True
                self._closing_symbols_c: set = set()
                logger.info("Account C (Cameron B2 Pattern Trader) initialized")
            else:
                logger.warning("Account C: ALPACA keys not found, disabled")

        # Engulfing dashboard account — order history sync at EOD
        self.engulfing_trader: Optional[AlpacaTrader] = None
        engulfing_key = os.environ.get("ALPACA_API_KEY_ENGULFING")
        engulfing_secret = os.environ.get("ALPACA_SECRET_KEY_ENGULFING")
        if engulfing_key and engulfing_secret:
            self.engulfing_trader = AlpacaTrader(engulfing_key, engulfing_secret, config)
            logger.info("Engulfing dashboard Alpaca client initialized (order history sync)")
        else:
            logger.info("Engulfing dashboard: ALPACA keys not set, order history sync disabled")

        # State
        self._running = False
        self._graceful_shutdown = False
        self._last_daily_reset: Optional[date] = None
        self._eod_complete = False  # Flag to stop signal processing after EOD close

        # TA cache (loaded at startup)
        self._ta_cache: Dict[str, Dict] = {}

        # Real-time price cache from WebSocket
        self._realtime_prices: Dict[str, float] = {}

        # BucketAggregator for baseline generation (initialized in run())
        self.bucket_aggregator = None
        self._db_pool = None

        # Intraday bar collector (initialized in run())
        self.bar_collector: Optional[IntradayBarCollector] = None
        self._bar_retention_pending = False

        # WebSocket health tracking (PROD-1 graceful degradation)
        self._websocket_healthy = False
        self._websocket_enabled = config.USE_STOCK_WEBSOCKET
        self._websocket_reconnect_failures = 0

        # Hard stop debounce — prevent duplicate close tasks for same symbol
        self._closing_symbols: set = set()
        self._closing_symbols_b: set = set()

        # v58: Momentum EOD Screener state (Account A)
        self._momentum_screener: Optional[MomentumScreener] = None  # Initialized in run() after db_pool
        self._momentum_candidates = []
        self._momentum_screened_today = False
        self._momentum_bought_today = False

    def _get_et_now(self) -> datetime:
        """Get current time in ET."""
        return datetime.now(ET)

    def _is_trading_hours(self) -> bool:
        """Check if within trading hours."""
        now = self._get_et_now().time()
        return self.config.MARKET_OPEN <= now <= self.config.MARKET_CLOSE

    def _on_stock_price_update(self, symbol: str, price: float, timestamp: datetime):
        """
        Callback for real-time stock price updates from WebSocket.

        Used for:
        - Hard stop monitoring (faster than REST polling)
        - Entry price validation
        """
        self._realtime_prices[symbol] = price

        # Only act on prices during trading hours — Cloud Run can restart
        # on weekends/overnight and replay stale WebSocket data.
        if not self._is_trading_hours():
            return

        # Check hard stop if we have a position in this symbol (Account A)
        if symbol in self.position_manager.active_trades and self.config.USE_HARD_STOP:
            trade = self.position_manager.active_trades[symbol]
            pnl_pct = (price - trade.entry_price) / trade.entry_price

            if pnl_pct <= self.config.HARD_STOP_PCT and symbol not in self._closing_symbols:
                logger.warning(
                    f"HARD STOP triggered via WebSocket: {symbol} "
                    f"@ ${price:.2f} ({pnl_pct*100:.1f}%)"
                )
                self._closing_symbols.add(symbol)
                asyncio.create_task(self._async_hard_stop(symbol))

        # Check stop/target for Account B (direction-aware)
        if (self._account_b_enabled
                and symbol in self.position_manager_b.active_trades):
            trade_b = self.position_manager_b.active_trades[symbol]
            if symbol not in self._closing_symbols_b:
                should_close = False
                reason = ""

                if trade_b.direction == "bullish":
                    if trade_b.stop_price and price <= trade_b.stop_price:
                        should_close = True
                        reason = "stop"
                    elif trade_b.target_price and price >= trade_b.target_price:
                        should_close = True
                        reason = "target"
                else:  # bearish
                    if trade_b.stop_price and price >= trade_b.stop_price:
                        should_close = True
                        reason = "stop"
                    elif trade_b.target_price and price <= trade_b.target_price:
                        should_close = True
                        reason = "target"

                if should_close:
                    logger.warning(
                        f"ACCOUNT B {reason.upper()} triggered via WebSocket: "
                        f"{trade_b.direction} {symbol} @ ${price:.2f}"
                    )
                    self._closing_symbols_b.add(symbol)
                    asyncio.create_task(self._async_exit_b(symbol, reason))

        # Check stop/target for Account C (bullish only — Cameron is long-only)
        if (self._account_c_enabled
                and symbol in self.position_manager_c.active_trades):
            trade_c = self.position_manager_c.active_trades[symbol]
            if symbol not in self._closing_symbols_c:
                should_close = False
                reason = ""

                if trade_c.stop_price and price <= trade_c.stop_price:
                    should_close = True
                    reason = "stop"
                elif trade_c.target_price and price >= trade_c.target_price:
                    should_close = True
                    reason = "target"

                if should_close:
                    logger.warning(
                        f"CAMERON {reason.upper()} triggered via WebSocket: "
                        f"{symbol} @ ${price:.2f}"
                    )
                    self._closing_symbols_c.add(symbol)
                    asyncio.create_task(self._async_exit_c(symbol, reason))

    async def _async_hard_stop(self, symbol: str):
        """Execute hard stop asynchronously."""
        if self.dry_run:
            self._closing_symbols.discard(symbol)
            logger.info(f"[DRY RUN] Would close {symbol} for hard stop")
            return

        try:
            trade = await self.position_manager.close_position(symbol, "stop")
            if trade:
                logger.info(f"Hard stop executed: {symbol} P&L: ${trade.pnl:+.2f}")
        except Exception as e:
            logger.error(f"Hard stop execution failed for {symbol}: {e}")
        finally:
            self._closing_symbols.discard(symbol)

    async def _async_exit_b(self, symbol: str, reason: str = "stop"):
        """Execute stop or target exit for Account B asynchronously."""
        if self.dry_run:
            self._closing_symbols_b.discard(symbol)
            logger.info(f"[DRY RUN] Would close Account B {symbol} for {reason}")
            return

        try:
            trade = await self.position_manager_b.close_position(symbol, reason)
            if trade:
                logger.info(f"Account B {reason} executed: {symbol} P&L: ${trade.pnl:+.2f}")
        except Exception as e:
            logger.error(f"Account B {reason} execution failed for {symbol}: {e}")
        finally:
            self._closing_symbols_b.discard(symbol)

    async def _async_exit_c(self, symbol: str, reason: str = "stop"):
        """Execute stop or target exit for Account C asynchronously."""
        if self.dry_run:
            self._closing_symbols_c.discard(symbol)
            logger.info(f"[DRY RUN] Would close Cameron {symbol} for {reason}")
            return

        try:
            trade = await self.position_manager_c.close_position(symbol, reason)
            if trade:
                logger.info(f"Cameron {reason} executed: {symbol} P&L: ${trade.pnl:+.2f}")
        except Exception as e:
            logger.error(f"Cameron {reason} execution failed for {symbol}: {e}")
        finally:
            self._closing_symbols_c.discard(symbol)

    async def _poll_account_b_patterns(self):
        """
        Poll engulfing_scores for qualifying big-hitter patterns and submit limit orders.

        Filter stages applied here (Stage 2 + 3 — see engulfing_checker.py for Stage 1):

        Stage 2 — TA stamping (observation only, no gating as of v80):
          RSI/SMA values are stamped onto TradeSetup for logging to
          paper_trades_log_b.signal_rsi. No trades are rejected based on TA.
          History: v78 added RSI/SMA gates (fail-open bug → never ran).
          v79 made fail-closed (blocked all trades missing TA). v80 removed
          gates entirely — never validated on live trades.

        Stage 3 — Time gate:
          - Before 9:35 AM ET: skip (v79 — open-bar noise, 39% WR in 9am window)
          - After 11:00 AM ET: skip (v73 — 3yr backtest morning +$17K, afternoon -$209)
        """
        if not self._account_b_enabled or self._eod_complete:
            return
        if not self._is_entry_allowed():
            return
        now_et = self._get_et_now().time()
        # Stage 3: time gate — no entries before 9:35 AM (v79: open-bar noise buffer)
        if now_et < self.config.ACCOUNT_B_FIRST_ENTRY_TIME:
            return
        # Stage 3: time gate — no entries after 11 AM ET (v73: 3yr backtest validated)
        if now_et > self.config.ACCOUNT_B_LAST_ENTRY_TIME:
            return

        setups = self.pattern_poller.poll_qualifying_patterns()
        if not setups:
            return

        # Stage 2 — TA stamping (observation only, no gating — v80)
        # Stamp RSI/SMA onto setup for logging; do NOT reject trades based on TA.
        for setup in setups:
            ta = self.signal_generator._get_ta_for_symbol(setup.symbol)
            rsi = ta.get("rsi_14")
            sma_20 = ta.get("sma_20")
            sma_50 = ta.get("sma_50")
            if rsi is not None:
                setup.rsi_14 = float(rsi)
            if sma_20 is not None:
                setup.sma_20 = float(sma_20)
            if sma_50 is not None:
                setup.sma_50 = float(sma_50)

        for setup in setups:
            if not self.position_manager_b.can_open_position:
                logger.info("Account B at max positions, stopping pattern poll")
                break

            if self.position_manager_b.already_traded(setup.symbol):
                continue

            # Log signal to dashboard — use actual RSI from TA lookup (v79: was hardcoded 0)
            if self.dashboard_b and self.dashboard_b.enabled:
                self.dashboard_b.log_signal(
                    symbol=setup.symbol,
                    score=0,
                    rsi=setup.rsi_14 or 0,
                    ratio=0,
                    notional=0,
                    price=setup.entry_price,
                    direction=setup.direction,
                    stop_loss=setup.stop_loss,
                    target_1=setup.target_1,
                    risk_per_share=setup.risk_per_share,
                    pattern_strength=setup.pattern_strength,
                )

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Account B: {setup.direction.upper()} {setup.symbol} "
                    f"@ ${setup.entry_price:.2f} (stop=${setup.stop_loss:.2f}, "
                    f"target=${setup.target_1:.2f}, strength={setup.pattern_strength})"
                )
                continue

            trade = await self.position_manager_b.open_limit_position(
                setup, max_risk=self.config.ACCOUNT_B_MAX_RISK_PER_TRADE
            )
            if trade:
                # Subscribe for real-time price updates
                if self.stock_monitor.is_connected:
                    await self.stock_monitor.subscribe([setup.symbol])

    async def _check_account_b_pending(self):
        """Check pending limit orders for fills/expiry."""
        if not self._account_b_enabled:
            return

        if not self.position_manager_b._pending_limit_orders:
            return

        result = await self.position_manager_b.check_pending_orders(
            max_age_minutes=self.config.ACCOUNT_B_CONFIRMATION_WINDOW_MIN
        )

        for trade in result["filled"]:
            logger.info(f"Account B FILL: {trade.direction} {trade.symbol} "
                       f"{trade.shares} shares @ ${trade.entry_price:.2f}")
            # Subscribe for price monitoring
            if self.stock_monitor.is_connected:
                await self.stock_monitor.subscribe([trade.symbol])

        for trade in result["cancelled"]:
            logger.info(f"Account B CANCEL: {trade.symbol} (unfilled/expired)")

    async def _poll_cameron_patterns(self):
        """Poll cameron_scores for qualifying Cameron B2 patterns and submit limit orders."""
        if not self._account_c_enabled or self._eod_complete:
            return
        if not self._is_entry_allowed():
            return

        setups = self.cameron_checker.poll_qualifying_patterns()
        if not setups:
            return

        # Enrich setups with article data (data collection, not a filter)
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            from paper_trading.article_lookup import check_articles_for_symbol
            for setup in setups:
                info = check_articles_for_symbol(db_url, setup.symbol)
                setup.has_news = info.has_news
                setup.article_count = info.article_count
                if info.has_news:
                    logger.info(
                        f"Cameron NEWS: {setup.symbol} has {info.article_count} articles"
                    )

        for setup in setups:
            if not self.position_manager_c.can_open_position:
                logger.info("Cameron: at max positions, stopping pattern poll")
                break

            if self.position_manager_c.already_traded(setup.symbol):
                continue

            # Log signal to dashboard (annotate with news count if present)
            pattern_display = f"{setup.pattern_type} ({setup.pattern_strength})"
            if setup.has_news:
                pattern_display += f" [NEWS x{setup.article_count}]"

            if self.dashboard_c and self.dashboard_c.enabled:
                self.dashboard_c.log_signal(
                    symbol=setup.symbol,
                    score=0,
                    rsi=0,
                    ratio=0,
                    notional=0,
                    price=setup.entry_price,
                    direction=setup.direction,
                    stop_loss=setup.stop_loss,
                    target_1=setup.target_1,
                    risk_per_share=setup.risk_per_share,
                    pattern_strength=pattern_display,
                )

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Cameron: BUY {setup.symbol} "
                    f"@ ${setup.entry_price:.2f} (stop=${setup.stop_loss:.2f}, "
                    f"target=${setup.target_1:.2f}, type={setup.pattern_type}, "
                    f"gap={setup.gap_pct:.0%}, rvol={setup.rvol:.0f}x)"
                )
                continue

            trade = await self.position_manager_c.open_limit_position(
                setup, max_risk=self.config.CAMERON_MAX_RISK_PER_TRADE
            )
            if trade:
                # Mark symbol as traded so we don't re-enter after stop/redeploy
                self.cameron_checker._traded_today.add(setup.symbol)
                # Subscribe for real-time price updates
                if self.stock_monitor.is_connected:
                    await self.stock_monitor.subscribe([setup.symbol])

    async def _check_cameron_pending(self):
        """Check pending Cameron limit orders for fills/expiry."""
        if not self._account_c_enabled:
            return

        if not self.position_manager_c._pending_limit_orders:
            return

        result = await self.position_manager_c.check_pending_orders(
            max_age_minutes=self.config.CAMERON_CONFIRMATION_WINDOW_MIN
        )

        for trade in result["filled"]:
            logger.info(f"Cameron FILL: {trade.symbol} "
                       f"{trade.shares} shares @ ${trade.entry_price:.2f}")
            if self.stock_monitor.is_connected:
                await self.stock_monitor.subscribe([trade.symbol])

        for trade in result["cancelled"]:
            logger.info(f"Cameron CANCEL: {trade.symbol} (unfilled/expired)")

    async def _cameron_scan_tick(self):
        """Run one Cameron scanner cycle if within scan window."""
        if not self._account_c_enabled or not self.cameron_scanner:
            return

        now_et = self._get_et_now()
        if not self.cameron_scanner.is_scan_window(now_et):
            return

        # Load candidates once per day on first tick
        if not self.cameron_scanner._candidates_loaded:
            await self.cameron_scanner.load_candidates()
            # Subscribe candidates to WS for real-time prices
            if self.cameron_scanner._candidates and self.stock_monitor:
                syms = [c["symbol"] for c in self.cameron_scanner._candidates]
                await self.stock_monitor.subscribe(syms)
                logger.info(f"Cameron: subscribed {len(syms)} candidates to WS")

        try:
            count = await self.cameron_scanner.scan_tick()
            if count > 0:
                logger.info(f"Cameron scan: {count} new patterns detected")
        except Exception as e:
            logger.warning(f"Cameron scan_tick failed: {e}")

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """Get real-time price from WebSocket cache."""
        return self._realtime_prices.get(symbol)

    def _on_websocket_connect(self):
        """Callback when WebSocket connects successfully."""
        self._websocket_healthy = True
        self._websocket_reconnect_failures = 0
        logger.info("Stock WebSocket connected - using real-time prices")

    def _on_websocket_disconnect(self):
        """Callback when WebSocket disconnects."""
        self._websocket_healthy = False
        self._websocket_reconnect_failures += 1
        logger.warning(f"Stock WebSocket disconnected (failure #{self._websocket_reconnect_failures})")

        # Check if we should disable WebSocket entirely
        if self._websocket_reconnect_failures >= self.config.WEBSOCKET_MAX_RECONNECT_ATTEMPTS:
            if self.config.WEBSOCKET_FALLBACK_TO_REST:
                logger.warning(
                    f"WebSocket failed {self._websocket_reconnect_failures} times - "
                    "falling back to REST polling permanently for this session"
                )
                self._websocket_enabled = False
            else:
                logger.error("WebSocket failed and fallback is disabled!")

    @property
    def use_websocket_prices(self) -> bool:
        """Check if we should use WebSocket prices (healthy and enabled)."""
        return self._websocket_enabled and self._websocket_healthy

    async def _update_stock_subscriptions(self):
        """Update stock monitor subscriptions based on active positions."""
        # Skip if WebSocket is disabled
        if not self._websocket_enabled:
            return

        # Subscribe to all symbols with active positions (both accounts)
        position_symbols = list(self.position_manager.active_trades.keys())
        pending_symbols = list(self.position_manager._pending_buys)

        # Include Account B positions and pending limit orders
        if self._account_b_enabled:
            position_symbols += list(self.position_manager_b.active_trades.keys())
            pending_symbols += list(self.position_manager_b._pending_limit_orders.keys())

        # Include Account C positions and pending limit orders
        if self._account_c_enabled:
            position_symbols += list(self.position_manager_c.active_trades.keys())
            pending_symbols += list(self.position_manager_c._pending_limit_orders.keys())

        all_symbols = list(set(position_symbols + pending_symbols))

        if all_symbols and self.stock_monitor.is_connected:
            await self.stock_monitor.set_symbols(all_symbols)
            logger.debug(f"Stock subscriptions updated: {all_symbols}")

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
            self._bar_retention_pending = True  # Schedule old bar cleanup

            # v58: Reset momentum screener state for new day
            self._momentum_screened_today = False
            self._momentum_bought_today = False
            self._momentum_candidates = []
            self._last_daily_reset = today

            # Account B daily reset
            if self._account_b_enabled:
                self.position_manager_b.reset_daily()
                self.eod_closer_b.reset_daily()
                self.pattern_poller.reset_daily()

            # Account C daily reset
            if self._account_c_enabled:
                self.position_manager_c.reset_daily()
                self.eod_closer_c.reset_daily()
                self.cameron_checker.reset_daily()
                if self.cameron_scanner:
                    self.cameron_scanner.reset_daily()

            # Only clear dashboard if we're in pre-market (before 9:30 AM)
            # This prevents wiping data on mid-day restarts
            if now_time < self.config.MARKET_OPEN:
                dashboard = get_dashboard()
                if dashboard.enabled:
                    logger.info("Pre-market: clearing dashboard for new day")
                    dashboard.clear_daily()
                if self.dashboard_b and self.dashboard_b.enabled:
                    logger.info("Pre-market: clearing Account B dashboard for new day")
                    self.dashboard_b.clear_daily()
                if self.dashboard_c and self.dashboard_c.enabled:
                    logger.info("Pre-market: clearing Cameron dashboard for new day")
                    self.dashboard_c.clear_daily()
            else:
                logger.info("Mid-day restart: preserving dashboard data")

    async def load_ta_cache(self):
        """
        Load prior-day TA data from database (preferred) or JSON file (fallback).

        Data source priority:
        1. Database (ta_daily_close table) - shared across containers
        2. Local JSON file - only works if premarket job ran in same container

        The premarket-ta-cache job writes to both database and JSON.
        """
        # Try database first (works across containers)
        database_url = os.environ.get("DATABASE_URL")
        if database_url:
            try:
                import psycopg2
                conn = psycopg2.connect(database_url.strip())
                cur = conn.cursor()

                # Get most recent TA data for each symbol
                cur.execute("""
                    SELECT symbol, rsi_14, macd_histogram, sma_20, sma_50, close_price,
                           CASE WHEN close_price > sma_20 THEN 1 ELSE -1 END as trend
                    FROM ta_daily_close
                    WHERE trade_date = (SELECT MAX(trade_date) FROM ta_daily_close)
                """)

                for row in cur.fetchall():
                    symbol, rsi_14, macd_hist, sma_20, sma_50, last_close, trend = row
                    self._ta_cache[symbol] = {
                        "rsi_14": float(rsi_14) if rsi_14 else None,
                        "macd_hist": float(macd_hist) if macd_hist else None,
                        "sma_20": float(sma_20) if sma_20 else None,
                        "sma_50": float(sma_50) if sma_50 else None,
                        "last_close": float(last_close) if last_close else None,
                        "trend": trend,
                    }

                cur.close()
                conn.close()

                if self._ta_cache:
                    logger.info(f"Loaded TA cache from database: {len(self._ta_cache)} symbols")
                    self.signal_generator.load_ta_cache(self._ta_cache)
                    return

            except Exception as e:
                logger.warning(f"Failed to load TA from database: {e}")

        # Fallback to JSON file (only works in same container as premarket job)
        ta_file = Path(__file__).parent.parent / "polygon_data" / "daily_ta_cache.json"

        if ta_file.exists():
            try:
                with open(ta_file) as f:
                    data = json.load(f)
                    self._ta_cache = data.get("ta_data", {})
                    logger.info(f"Loaded TA cache from file: {len(self._ta_cache)} symbols")
            except Exception as e:
                logger.error(f"Failed to load TA cache from file: {e}")
        else:
            logger.warning(f"TA cache not found (DB or file). Will fetch from Polygon on demand.")

        self.signal_generator.load_ta_cache(self._ta_cache)

    async def load_baselines(self):
        """
        Load per-symbol baselines from intraday_baselines_30m table.

        Calculates average notional per symbol from recent trading days.
        Falls back to $50K default if query fails or no data.
        """
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            logger.warning("DATABASE_URL not set, using default $50K baselines")
            return

        try:
            import psycopg2
            conn = psycopg2.connect(database_url.strip())
            cur = conn.cursor()

            # Get average notional per symbol from last 20 trading days
            cur.execute("""
                SELECT
                    symbol,
                    AVG(notional) as avg_notional
                FROM intraday_baselines_30m
                WHERE trade_date > CURRENT_DATE - 20
                  AND bucket_start BETWEEN '09:30' AND '16:00'
                GROUP BY symbol
                HAVING AVG(notional) > 0
            """)

            baselines = {}
            for row in cur.fetchall():
                symbol, avg_notional = row
                baselines[symbol] = float(avg_notional)

            cur.close()
            conn.close()

            if baselines:
                self.aggregator.load_baselines(baselines)
                logger.info(f"Loaded baselines: {len(baselines)} symbols from intraday_baselines_30m")
                # Log some stats
                avg_baseline = sum(baselines.values()) / len(baselines)
                logger.info(f"  Average baseline: ${avg_baseline:,.0f}")
                logger.info(f"  Range: ${min(baselines.values()):,.0f} - ${max(baselines.values()):,.0f}")
            else:
                logger.warning("No baselines found in database, using default $50K")

        except Exception as e:
            logger.warning(f"Failed to load baselines from database: {e}")
            logger.warning("Using default $50K baselines")

    async def _process_trade(self, trade: Trade):
        """Process a single trade from firehose."""
        # Add to aggregator
        self.aggregator.add_trade(trade)

        # Feed bucket aggregator for baseline generation
        if self.bucket_aggregator:
            match = re.match(r"O:([A-Z]+)\d{6}[CP]\d{8}", trade.symbol)
            if match:
                underlying = match.group(1)

                # Also feed 5-min rolling aggregator for hot options
                self.rolling_agg_5m.add_trade_fast(
                    underlying=underlying,
                    option_symbol=trade.symbol,
                    price=trade.price,
                    size=trade.size,
                )
                boundary_crossed = self.bucket_aggregator.add_trade(
                    underlying=underlying,
                    option_symbol=trade.symbol,
                    price=trade.price,
                    size=trade.size,
                )
                if boundary_crossed:
                    try:
                        rows = await self.bucket_aggregator.flush()
                        logger.info(f"Bucket boundary: flushed {rows} baseline rows to DB")
                        # Compute intraday flow signals after flush
                        if rows:
                            await self._compute_intraday_flow_signals()
                    except Exception as e:
                        logger.warning(f"Bucket flush failed: {e}")

    async def _compute_intraday_flow_signals(self) -> None:
        """Compute flow_signals from today's call/put volumes + latest ORATS."""
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
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
                            tv.put_vol::numeric / NULLIF(tv.call_vol, 0) AS put_call_ratio,
                            CASE
                                WHEN es.direction = 'bullish'
                                     AND tv.call_vol > 0
                                     AND (tv.put_vol::numeric / tv.call_vol) <= 0.7
                                     AND COALESCE(od.volume_zscore, 0) >= 1.5 THEN TRUE
                                WHEN es.direction = 'bearish'
                                     AND tv.call_vol > 0
                                     AND (tv.put_vol::numeric / tv.call_vol) >= 1.3
                                     AND COALESCE(od.volume_zscore, 0) >= 1.5 THEN TRUE
                                ELSE FALSE
                            END AS flow_aligned,
                            NOW() AS computed_at
                        FROM engulfing_scores es
                        LEFT JOIN LATERAL (
                            SELECT iv_rank, volume_zscore FROM orats_daily
                            WHERE symbol = es.symbol ORDER BY asof_date DESC LIMIT 1
                        ) od ON TRUE
                        JOIN (
                            SELECT symbol, SUM(call_volume) AS call_vol, SUM(put_volume) AS put_vol
                            FROM intraday_baselines_30m
                            WHERE trade_date = CURRENT_DATE
                            GROUP BY symbol
                        ) tv ON tv.symbol = es.symbol
                        WHERE es.timeframe = '1D'
                          AND es.pattern_date::DATE >= CURRENT_DATE - INTERVAL '2 days'
                          AND tv.call_vol > 0
                        ORDER BY es.symbol, es.pattern_date::DATE, es.scan_ts DESC
                    ) sub
                    ON CONFLICT (symbol, pattern_date) DO UPDATE SET
                        direction = EXCLUDED.direction, iv_rank = EXCLUDED.iv_rank,
                        volume_zscore = EXCLUDED.volume_zscore, put_call_ratio = EXCLUDED.put_call_ratio,
                        flow_aligned = EXCLUDED.flow_aligned, computed_at = EXCLUDED.computed_at
                """)
                parts = result.split() if result else []
                upserted = int(parts[-1]) if parts else 0
                if upserted > 0:
                    logger.info(f"Intraday flow_signals: {upserted} rows upserted")
        except Exception as e:
            logger.error(f"Intraday flow_signals failed: {e}")

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

        # Track ALL triggered symbols for TA monitoring (not just passed)
        if triggered and self._db_pool:
            try:
                now = self._get_et_now()
                async with self._db_pool.acquire() as conn:
                    await conn.executemany("""
                        INSERT INTO tracked_tickers_v2
                        (symbol, first_trigger_ts, trigger_count, last_trigger_ts, ta_enabled)
                        VALUES ($1, $2, 1, $2, TRUE)
                        ON CONFLICT (symbol) DO UPDATE SET
                            trigger_count = tracked_tickers_v2.trigger_count + 1,
                            last_trigger_ts = $2
                    """, [(sym, now) for sym in triggered.keys()])
            except Exception as e:
                logger.warning(f"Failed to track triggered symbols: {e}")

        # Pre-subscribe to triggered symbols for real-time prices
        if triggered and self.stock_monitor.is_connected:
            triggered_symbols = list(triggered.keys())[:10]  # Limit to 10 candidates
            await self.stock_monitor.subscribe(triggered_symbols)

        for symbol, stats in triggered.items():
            # Account B now uses independent pattern polling, not UOA triggers
            a_eligible = (self.position_manager.can_open_position
                          and not self.position_manager.already_traded(symbol)
                          and not self.position_manager.has_position(symbol))

            # ── Account A: full filter chain (disabled when momentum screener active) ──
            if not self.config.USE_UOA_SIGNALS:
                continue  # v58: Account A uses momentum screener, not UOA signals
            if not a_eligible:
                if not self.position_manager.can_open_position:
                    logger.info("Account A at max positions, skipping signal check")
                    break
                continue

            # Create signal from aggregated stats (with dynamic TA fetch if needed)
            # Note: price/trend are None from aggregator - signal_filter fetches from Alpaca
            # Returns None if TA data unavailable (fetch timeout/failure)
            signal = await self.signal_generator.create_signal_async(
                symbol=symbol,
                score=stats.get("score", 0),
                notional=stats.get("notional", 0),
                contracts=stats.get("contracts", 0),
                price=stats.get("price"),  # None from aggregator, fetched by signal_filter
                trend=stats.get("trend"),  # None from aggregator, computed from TA
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

            # Skip if signal creation failed (missing TA data)
            if signal is None:
                continue

            # Apply filter
            result = self.signal_filter.apply(signal)
            self.position_manager.record_signal(result.passed)

            if result.passed:
                logger.info(f"Signal passed filter: {symbol}")

                if not self.dry_run:
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
                else:
                    logger.info(f"[DRY RUN] Would open position: {symbol}")

    async def _execute_momentum_buys(self):
        """
        Execute buys for momentum screener candidates (v58).

        Iterates through candidates (sorted by momentum ascending = most beaten-down first),
        skips if already holding, opens position until max reached.
        """
        if not self._momentum_candidates:
            logger.info("Momentum buy: no candidates to buy")
            return

        bought = 0
        skipped = 0
        for candidate in self._momentum_candidates:
            if not self.position_manager.can_open_position:
                logger.info(f"Momentum buy: max positions reached after {bought} buys")
                break

            symbol = candidate.symbol
            if self.position_manager.has_position(symbol):
                logger.info(f"Momentum buy: skip {symbol} — already holding")
                skipped += 1
                continue
            if self.position_manager.already_traded(symbol):
                logger.info(f"Momentum buy: skip {symbol} — already traded today")
                skipped += 1
                continue

            if self.dry_run:
                logger.info(
                    f"[DRY RUN] Momentum buy: {symbol} mom={candidate.momentum:.1%} "
                    f"price=${candidate.price:.2f}"
                )
                bought += 1
                continue

            trade = await self.position_manager.open_position(
                symbol=symbol,
                signal_score=0,  # No UOA score for momentum screener
                signal_rsi=0,    # No RSI for momentum screener
                signal_notional=0,
            )

            if trade:
                # Log to Google Sheet Active Signals tab
                dashboard = get_dashboard()
                if dashboard.enabled:
                    dashboard.log_signal(
                        symbol=symbol,
                        score=0,
                        rsi=candidate.momentum * 100,  # Show momentum % in RSI column
                        ratio=0,
                        notional=0,
                        price=trade.entry_price,
                    )

                logger.info(
                    f"Momentum buy: {symbol} mom={candidate.momentum:.1%} "
                    f"{trade.shares} shares @ ${trade.entry_price:.2f} "
                    f"(overnight hold)"
                )
                bought += 1
            else:
                logger.warning(f"Momentum buy: failed to open {symbol}")

        logger.info(f"Momentum buy complete: {bought} opened, {skipped} skipped, "
                     f"{len(self._momentum_candidates)} candidates")

    async def _check_hard_stops(self):
        """
        Check if any positions hit hard stop.

        This is the REST-based fallback check. When WebSocket is healthy,
        hard stops are triggered immediately via _on_stock_price_update callback.
        This periodic check serves as a safety net.
        """
        if self.dry_run:
            return

        # Log which mode we're using (occasionally)
        if not hasattr(self, '_last_mode_log') or \
           (asyncio.get_event_loop().time() - self._last_mode_log) > 300:  # Every 5 min
            mode = "WebSocket" if self.use_websocket_prices else "REST polling"
            logger.info(f"Price monitoring mode: {mode}")
            self._last_mode_log = asyncio.get_event_loop().time()

        stopped = await self.position_manager.check_hard_stops()
        for symbol in stopped:
            logger.warning(f"Hard stop triggered (REST check): {symbol}")

    async def _refresh_sparklines(self):
        """Refresh sparkline_1d table from today's spot_prices_1m bars."""
        if not self._db_pool:
            return

        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT symbol, array_agg(close ORDER BY bar_ts) AS closes
                    FROM spot_prices_1m
                    WHERE bar_ts >= CURRENT_DATE + INTERVAL '13 hours 30 minutes'
                    GROUP BY symbol
                """)

            if not rows:
                return

            today = self._get_et_now().date()
            max_pts = self.config.SPARKLINE_POINTS
            upsert_data = []

            for row in rows:
                symbol = row['symbol']
                closes = [float(c) for c in row['closes']]
                n = len(closes)
                if n <= max_pts:
                    sampled = closes
                else:
                    step = n // max_pts
                    sampled = closes[::step]

                upsert_data.append((
                    symbol, today, json.dumps(sampled), len(closes)
                ))

            if upsert_data:
                async with self._db_pool.acquire() as conn:
                    await conn.executemany("""
                        INSERT INTO sparkline_1d (symbol, trade_date, closes, bar_count, updated_at)
                        VALUES ($1, $2, $3::jsonb, $4, NOW())
                        ON CONFLICT (symbol) DO UPDATE SET
                            trade_date = EXCLUDED.trade_date,
                            closes     = EXCLUDED.closes,
                            bar_count  = EXCLUDED.bar_count,
                            updated_at = NOW()
                    """, upsert_data)

                logger.info(f"sparkline_1d refreshed: {len(upsert_data)} symbols")

        except Exception as e:
            logger.warning(f"Sparkline refresh failed: {e}")

    def _on_eod_complete_b(self, closed_trades):
        """Callback when Account B EOD close completes."""
        # Cancel any pending limit orders at EOD
        asyncio.ensure_future(self._cancel_account_b_pending_at_eod())

        logger.info(f"Account B EOD: closed {len(closed_trades)} positions")
        for t in closed_trades:
            logger.info(f"  Account B: {t.symbol} P&L: ${t.pnl:+.2f} ({t.pnl_pct:+.2f}%)")

    def _on_eod_complete_c(self, closed_trades):
        """Callback when Account C (Cameron) EOD close completes."""
        asyncio.ensure_future(self._cancel_cameron_pending_at_eod())
        logger.info(f"Cameron EOD: closed {len(closed_trades)} positions")
        for t in closed_trades:
            logger.info(f"  Cameron: {t.symbol} P&L: ${t.pnl:+.2f} ({t.pnl_pct:+.2f}%)")

    async def _cancel_cameron_pending_at_eod(self):
        """Cancel all pending Cameron limit orders at EOD."""
        if not self._account_c_enabled:
            return
        cancelled = await self.position_manager_c.cancel_all_pending_limit_orders()
        if cancelled:
            logger.info(f"Cameron EOD: cancelled {len(cancelled)} pending limit orders")

    async def _cancel_account_b_pending_at_eod(self):
        """Cancel all pending Account B limit orders at EOD."""
        if not self._account_b_enabled:
            return
        cancelled = await self.position_manager_b.cancel_all_pending_limit_orders()
        if cancelled:
            logger.info(f"Account B EOD: cancelled {len(cancelled)} pending limit orders")

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

        # Sync engulfing dashboard order history (non-blocking, non-critical)
        if self.engulfing_trader:
            asyncio.ensure_future(self._sync_engulfing_orders())

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

    async def _sync_engulfing_orders(self):
        """Fetch engulfing account order history and write to Google Sheet."""
        try:
            orders = await self.engulfing_trader.get_all_orders()
            if orders:
                dashboard = get_dashboard()
                if dashboard.enabled:
                    dashboard.sync_order_history(orders)
        except Exception as e:
            logger.warning(f"Engulfing order history sync failed: {e}")

    async def run(self):
        """
        Main run loop.

        Connects to firehose, processes trades, checks for signals,
        and manages positions.
        """
        logger.info("=" * 60)
        logger.info("Paper Trading Engine Starting")
        if self.config.USE_MOMENTUM_SCREENER:
            logger.info(f"Account A: Momentum<{self.config.MOMENTUM_THRESHOLD:.0%} EOD Screener "
                       f"(screen@{self.config.MOMENTUM_SCREEN_TIME}, buy@{self.config.MOMENTUM_BUY_TIME})")
            logger.info(f"  Price >= ${self.config.MOMENTUM_PRICE_FLOOR}, "
                       f"max {self.config.MOMENTUM_MAX_CANDIDATES} candidates")
        else:
            logger.info(f"Account A: UOA signals — Score >= {self.config.SCORE_THRESHOLD}, "
                       f"RSI < {self.config.RSI_THRESHOLD}, "
                       f"Notional >= ${self.config.MIN_NOTIONAL:,}")
        logger.info(f"Hard stop: {self.config.HARD_STOP_PCT*100:.0f}%")
        logger.info(f"Max positions: {self.config.MAX_CONCURRENT_POSITIONS}")
        logger.info(f"Exit time: {self.config.EXIT_TIME}")
        if self._account_b_enabled:
            logger.info(f"Account B: Big-Hitter Pattern Trader "
                       f"(poll every {self.config.ACCOUNT_B_POLL_INTERVAL_SEC}s, "
                       f"max_risk=${self.config.ACCOUNT_B_MAX_RISK_PER_TRADE}, "
                       f"candle_range<={self.config.ACCOUNT_B_MAX_CANDLE_RANGE})")
        if self._account_c_enabled:
            logger.info(f"Account C: Cameron B2 Pattern Trader "
                       f"(scan {self.config.CAMERON_SCAN_START}-{self.config.CAMERON_SCAN_END}, "
                       f"poll every {self.config.CAMERON_POLL_INTERVAL_SEC}s, "
                       f"max_risk=${self.config.CAMERON_MAX_RISK_PER_TRADE}, "
                       f"rvol>={self.config.CAMERON_RVOL_MIN}x)")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)

        # Load TA cache
        await self.load_ta_cache()

        # Load per-symbol baselines from database
        await self.load_baselines()

        # Create asyncpg pool for BucketAggregator and symbol tracking
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            try:
                import asyncpg
                self._db_pool = await asyncpg.create_pool(
                    db_url.strip(), min_size=1, max_size=5, command_timeout=30
                )
                self.bucket_aggregator = BucketAggregator(db_pool=self._db_pool)
                logger.info("BucketAggregator initialized with asyncpg pool")

                # Hot options detector (5-min cycle)
                self.hot_detector = HotOptionsDetector(
                    rolling_agg=self.rolling_agg_5m,
                    db_pool=self._db_pool,
                    polygon_api_key=self.polygon_api_key,
                )
                await self.hot_detector.refresh_baselines()
                logger.info("HotOptionsDetector initialized")

                # Initialize momentum screener (v58)
                if self.config.USE_MOMENTUM_SCREENER:
                    self._momentum_screener = MomentumScreener(
                        db_pool=self._db_pool,
                        momentum_threshold=self.config.MOMENTUM_THRESHOLD,
                        price_floor=self.config.MOMENTUM_PRICE_FLOOR,
                        min_adv=self.config.MIN_ADV if self.config.USE_ADV_FILTER else 0,
                        max_candidates=self.config.MOMENTUM_MAX_CANDIDATES,
                    )
                    logger.info(
                        f"Momentum Screener initialized: threshold={self.config.MOMENTUM_THRESHOLD:.0%}, "
                        f"price>=${self.config.MOMENTUM_PRICE_FLOOR}, "
                        f"screen@{self.config.MOMENTUM_SCREEN_TIME}, buy@{self.config.MOMENTUM_BUY_TIME}"
                    )

                # Initialize intraday bar collector
                if self.config.COLLECT_INTRADAY_BARS:
                    self.bar_collector = IntradayBarCollector(
                        api_key=self._alpaca_api_key,
                        secret_key=self._alpaca_secret_key,
                        db_pool=self._db_pool,
                        max_batches=self.config.INTRADAY_BARS_MAX_BATCHES,
                    )
                    logger.info(
                        f"IntradayBarCollector initialized: "
                        f"max_batches={self.config.INTRADAY_BARS_MAX_BATCHES} "
                        f"({self.config.INTRADAY_BARS_MAX_BATCHES * 100} symbols)"
                    )

                # Initialize Cameron scanner (Account C)
                if self._account_c_enabled:
                    self.cameron_scanner = CameronScanner(
                        db_pool=self._db_pool,
                        alpaca_key=os.environ.get("ALPACA_API_KEY", ""),
                        alpaca_secret=os.environ.get("ALPACA_SECRET_KEY", ""),
                        scan_start=self.config.CAMERON_SCAN_START,
                        scan_end=self.config.CAMERON_SCAN_END,
                        rvol_min=self.config.CAMERON_RVOL_MIN,
                    )
                    logger.info(
                        f"CameronScanner initialized: "
                        f"scan={self.config.CAMERON_SCAN_START}-{self.config.CAMERON_SCAN_END}, "
                        f"rvol>={self.config.CAMERON_RVOL_MIN}x"
                    )

                    # Pre-load candidates at startup so they're published to
                    # cameron_candidates_daily before 9:45 AM (V1 article
                    # fetch job reads this table at 8:30 AM ET)
                    try:
                        n = await self.cameron_scanner.load_candidates()
                        logger.info(f"Cameron pre-loaded {n} candidates at startup")
                    except Exception as e:
                        logger.warning(f"Cameron pre-load candidates failed: {e}")
            except Exception as e:
                logger.warning(f"Failed to create asyncpg pool for BucketAggregator: {e}")

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

        # Account B: sync positions and start EOD closer
        if self._account_b_enabled and not self.dry_run:
            account_b = await self.trader_b.get_account()
            if account_b:
                logger.info(f"Account B: ${account_b.portfolio_value:,.2f} "
                           f"(${account_b.buying_power:,.2f} buying power)")
            await self.position_manager_b.sync_on_startup()
            self.eod_closer_b.start()

        # Account C: sync positions and start EOD closer
        if self._account_c_enabled and not self.dry_run:
            account_c = await self.trader_c.get_account()
            if account_c:
                logger.info(f"Account C: ${account_c.portfolio_value:,.2f} "
                           f"(${account_c.buying_power:,.2f} buying power)")
            await self.position_manager_c.sync_on_startup()
            self.eod_closer_c.start()

        # Start stock price WebSocket monitor (PROD-1)
        # Always starts receive loop with retry — initial failure is non-fatal
        if self.config.USE_STOCK_WEBSOCKET:
            logger.info("Starting stock price WebSocket monitor (Alpaca SIP)...")
            stock_monitor_started = await self.stock_monitor.start()
            if stock_monitor_started:
                self._websocket_healthy = True
                logger.info("Stock price WebSocket connected - real-time prices enabled")
            else:
                self._websocket_healthy = False
                logger.warning("Stock WebSocket initial connect failed - retry loop active, REST fallback until connected")
        else:
            logger.info("Stock WebSocket disabled by config - using REST polling")

        self._running = True
        signal_check_interval = self.config.SIGNAL_CHECK_INTERVAL_SEC
        stop_check_interval = self.config.POSITION_CHECK_INTERVAL_SEC
        subscription_update_interval = 5  # Update subscriptions every 5 seconds
        dashboard_update_interval = 30  # Update dashboard positions every 30 seconds
        bar_collect_interval = self.config.INTRADAY_BARS_INTERVAL_SEC
        account_b_poll_interval = self.config.ACCOUNT_B_POLL_INTERVAL_SEC if self._account_b_enabled else 9999
        cameron_poll_interval = self.config.CAMERON_POLL_INTERVAL_SEC if self._account_c_enabled else 9999
        cameron_scan_interval = self.config.CAMERON_SCAN_INTERVAL_SEC if self._account_c_enabled else 9999
        sparkline_interval = self.config.SPARKLINE_REFRESH_INTERVAL_SEC
        hot_options_interval = 300  # 5 minutes

        last_signal_check = 0
        last_stop_check = 0
        last_subscription_update = 0
        last_dashboard_update = 0
        last_bar_collect = 0
        last_account_b_poll = 0
        last_cameron_poll = 0
        last_cameron_scan = 0
        last_sparkline_refresh = 0
        last_hot_options_check = 0

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

                    # v58: Momentum screener time-based checks (inside signal interval)
                    if self._momentum_screener:
                        now_et = self._get_et_now()
                        now_time = now_et.time()

                        # Run momentum screen at 3:50 PM
                        if (not self._momentum_screened_today
                                and now_time >= self.config.MOMENTUM_SCREEN_TIME):
                            try:
                                self._momentum_candidates = await self._momentum_screener.screen()
                                self._momentum_screened_today = True
                                logger.info(f"Momentum screen complete: {len(self._momentum_candidates)} candidates")
                            except Exception as e:
                                logger.error(f"Momentum screen failed: {e}")
                                self._momentum_screened_today = True  # Don't retry

                        # Execute momentum buys at 3:56 PM (after EOD closer runs at 3:55)
                        if (self._momentum_candidates
                                and not self._momentum_bought_today
                                and now_time >= self.config.MOMENTUM_BUY_TIME):
                            await self._execute_momentum_buys()
                            self._momentum_bought_today = True

                # Periodic stop check (fallback if WebSocket missed something)
                if now - last_stop_check >= stop_check_interval:
                    await self._check_hard_stops()
                    # Account B: REST fallback for stop/target check
                    if self._account_b_enabled and self.position_manager_b.active_trades:
                        async def _get_price_b(sym):
                            return await self.trader_b.get_latest_price(sym)
                        await self.position_manager_b.check_stops_and_targets(_get_price_b)
                    # Account C: REST fallback for stop/target check
                    if self._account_c_enabled and self.position_manager_c.active_trades:
                        async def _get_price_c(sym):
                            return await self.trader_c.get_latest_price(sym)
                        await self.position_manager_c.check_stops_and_targets(_get_price_c)
                    last_stop_check = now

                # Account B: poll patterns and check pending limit orders
                if self._account_b_enabled and now - last_account_b_poll >= account_b_poll_interval:
                    try:
                        await self._poll_account_b_patterns()
                        await self._check_account_b_pending()
                    except Exception as e:
                        logger.warning(f"Account B poll/check failed: {e}")
                    last_account_b_poll = now

                # Cameron scanner: run pattern detection every 60s
                if self._account_c_enabled and now - last_cameron_scan >= cameron_scan_interval:
                    try:
                        await self._cameron_scan_tick()
                    except Exception as e:
                        logger.warning(f"Cameron scan failed: {e}")
                    last_cameron_scan = now

                # Cameron checker: poll patterns and check pending orders every 30s
                if self._account_c_enabled and now - last_cameron_poll >= cameron_poll_interval:
                    try:
                        await self._poll_cameron_patterns()
                        await self._check_cameron_pending()
                    except Exception as e:
                        logger.warning(f"Cameron poll/check failed: {e}")
                    last_cameron_poll = now

                # Periodic subscription update for stock monitor
                if now - last_subscription_update >= subscription_update_interval:
                    await self._update_stock_subscriptions()
                    last_subscription_update = now

                # Periodic dashboard position update (current prices and PnL)
                if now - last_dashboard_update >= dashboard_update_interval:
                    try:
                        await self.position_manager.update_dashboard_positions()
                    except Exception as e:
                        logger.warning(f"Dashboard position update failed: {e}")
                    if self._account_b_enabled:
                        try:
                            await self.position_manager_b.update_dashboard_positions()
                        except Exception as e:
                            logger.warning(f"Account B dashboard position update failed: {e}")
                    if self._account_c_enabled:
                        try:
                            await self.position_manager_c.update_dashboard_positions()
                        except Exception as e:
                            logger.warning(f"Cameron dashboard position update failed: {e}")
                    last_dashboard_update = now

                # Periodic intraday bar collection
                if self.bar_collector and now - last_bar_collect >= bar_collect_interval:
                    try:
                        if self._db_pool:
                            async with self._db_pool.acquire() as conn:
                                rows = await conn.fetch(
                                    "SELECT symbol FROM tracked_tickers_v2 "
                                    "WHERE ta_enabled = TRUE ORDER BY symbol"
                                )
                                symbols = [r['symbol'] for r in rows]
                            if symbols:
                                bars = await self.bar_collector.collect(symbols)
                                if bars:
                                    logger.debug(f"Collected {bars} intraday bars")
                    except Exception as e:
                        logger.warning(f"Intraday bar collection failed: {e}")
                    last_bar_collect = now

                # Periodic sparkline refresh (every 5 min, after bars collected)
                if now - last_sparkline_refresh >= sparkline_interval:
                    try:
                        await self._refresh_sparklines()
                    except Exception as e:
                        logger.warning(f"Sparkline refresh failed: {e}")
                    last_sparkline_refresh = now

                # Hot options detection (every 5 min)
                if self.hot_detector and now - last_hot_options_check >= hot_options_interval:
                    try:
                        # Refresh baselines every 30 min
                        if now - (self.hot_detector._last_baseline_refresh or 0) > 1800:
                            await self.hot_detector.refresh_baselines()
                        hot = await self.hot_detector.detect()
                        if hot:
                            await self.hot_detector.flush_to_db(hot)
                            logger.info(f"Hot options: {len(hot)} symbols detected")
                    except Exception as e:
                        logger.warning(f"Hot options detection failed: {e}")
                    last_hot_options_check = now

                # Run bar retention if flagged by daily reset
                if self._bar_retention_pending and self.bar_collector:
                    try:
                        await self.bar_collector.run_retention(
                            self.config.INTRADAY_BARS_RETENTION_DAYS
                        )
                    except Exception as e:
                        logger.warning(f"Bar retention failed: {e}")
                    self._bar_retention_pending = False

        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
            self._graceful_shutdown = True
        except Exception as e:
            logger.error(f"Engine error: {e}")
            self._graceful_shutdown = False
            raise
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")

        self._running = False
        self.eod_closer.stop()

        # Account B shutdown
        if self._account_b_enabled:
            if hasattr(self, 'eod_closer_b'):
                self.eod_closer_b.stop()
            # Cancel any pending limit orders
            await self.position_manager_b.cancel_all_pending_limit_orders()

        # Account C shutdown
        if self._account_c_enabled:
            if hasattr(self, 'eod_closer_c'):
                self.eod_closer_c.stop()
            await self.position_manager_c.cancel_all_pending_limit_orders()
            if self.cameron_scanner:
                await self.cameron_scanner.close()

        # Only close positions on intentional shutdown (KeyboardInterrupt / EOD)
        # Do NOT close on code crashes — positions are safer left open
        if self._graceful_shutdown and self._is_trading_hours() and not self.dry_run:
            logger.warning("Closing positions on graceful shutdown...")
            await self.position_manager.close_all_positions(reason="shutdown")
            if self._account_b_enabled:
                await self.position_manager_b.close_all_positions(reason="shutdown")
            if self._account_c_enabled:
                await self.position_manager_c.close_all_positions(reason="shutdown")
        elif not self._graceful_shutdown:
            logger.warning("Crash shutdown — preserving positions (not closing)")

        # Stop stock price monitor
        await self.stock_monitor.stop()
        logger.info(f"Stock monitor metrics: {self.stock_monitor.get_metrics()}")

        # Flush remaining bucket data
        if self.bucket_aggregator:
            try:
                rows = await self.bucket_aggregator.flush()
                if rows:
                    logger.info(f"Shutdown: flushed {rows} remaining baseline rows")
            except Exception:
                pass

        # Flush remaining bar data and close session
        if self.bar_collector:
            try:
                rows = await self.bar_collector.flush()
                if rows:
                    logger.info(f"Shutdown: flushed {rows} remaining bar records")
                await self.bar_collector.close()
                logger.info(f"Bar collector metrics: {self.bar_collector.get_metrics()}")
            except Exception:
                pass

        if self._db_pool:
            await self._db_pool.close()
            logger.info("Asyncpg pool closed")

        await self.firehose.disconnect()
        await self.trader.close()
        if self._account_b_enabled and hasattr(self, 'trader_b'):
            await self.trader_b.close()
        if self._account_c_enabled and hasattr(self, 'trader_c'):
            await self.trader_c.close()
        if self.engulfing_trader:
            await self.engulfing_trader.close()

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

        # Test stock price WebSocket (Alpaca SIP)
        logger.info("\n3. Testing Stock Price WebSocket (Alpaca SIP)...")
        stock_monitor = StockPriceMonitor(alpaca_key, alpaca_secret)

        try:
            started = await stock_monitor.start()
            if started:
                logger.info("   Stock WebSocket connected")

                # Subscribe to test symbols
                await stock_monitor.subscribe(["AAPL", "SPY"])
                logger.info("   Subscribed to AAPL, SPY")

                # Wait for some price updates
                await asyncio.sleep(5)

                aapl_price = stock_monitor.get_last_price("AAPL")
                spy_price = stock_monitor.get_last_price("SPY")
                metrics = stock_monitor.get_metrics()

                logger.info(f"   AAPL price: ${aapl_price:.2f}" if aapl_price else "   AAPL: no data yet")
                logger.info(f"   SPY price: ${spy_price:.2f}" if spy_price else "   SPY: no data yet")
                logger.info(f"   Trades received: {metrics['trades_received']}")
                logger.info(f"   Quotes received: {metrics['quotes_received']}")

                await stock_monitor.stop()
                logger.info("   Stock WebSocket OK")
            else:
                logger.warning("   Stock WebSocket failed to connect")

        except Exception as e:
            logger.error(f"   Stock WebSocket error: {e}")
        finally:
            await stock_monitor.stop()

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
