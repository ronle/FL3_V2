"""
Position Manager

Tracks positions, enforces limits, and manages trade state.
"""

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List, Dict, Callable, Awaitable

from .config import TradingConfig, DEFAULT_CONFIG
from .alpaca_trader import AlpacaTrader, Position, Order, OrderType, OrderSide
from .dashboard import (
    get_dashboard, update_signal_trade_placed, close_signal_in_db,
    log_trade_open, log_trade_close, load_open_trades_from_db,
)
import os

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Record of a trade for tracking."""
    symbol: str
    entry_time: datetime
    entry_price: float
    shares: int
    signal_score: int
    signal_rsi: float
    signal_notional: float
    trade_db_id: Optional[int] = None  # paper_trades_log.id for targeted updates
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    exit_reason: Optional[str] = None  # 'eod', 'stop', 'target', 'manual'
    # Account B big-hitter fields (defaults keep Account A unaffected)
    direction: str = "bullish"          # 'bullish' or 'bearish'
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_per_share: Optional[float] = None
    limit_order_id: Optional[str] = None
    order_submitted_at: Optional[datetime] = None
    pattern_date: Optional[datetime] = None
    candle_range: Optional[float] = None
    pattern_strength: Optional[str] = None
    # Article enrichment (Cameron Account C data collection)
    has_news: bool = False
    article_count: int = 0


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: date
    trades_entered: int = 0
    trades_exited: int = 0
    signals_seen: int = 0
    signals_filtered: int = 0
    total_pnl: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0


class PositionManager:
    """
    Manages positions and enforces trading rules.

    Responsibilities:
    - Track open positions and their state
    - Enforce max concurrent positions limit
    - Enforce position sizing rules
    - Track daily statistics
    - Check for hard stop triggers
    """

    def __init__(
        self,
        trader: AlpacaTrader,
        config: TradingConfig = DEFAULT_CONFIG,
        trades_table: str = "paper_trades_log",
        skip_dashboard: bool = False,
        dashboard=None,
    ):
        self.trader = trader
        self.config = config
        self.trades_table = trades_table
        self.skip_dashboard = skip_dashboard
        self._dashboard_override = dashboard

        # Active trades (symbol -> TradeRecord)
        self.active_trades: Dict[str, TradeRecord] = {}

        # Completed trades (today)
        self.completed_trades: List[TradeRecord] = []

        # Daily stats
        self.daily_stats = DailyStats(date=date.today())

        # Symbols we've already traded today (no re-entry)
        self.traded_today: set = set()

        # Symbols with pending buy orders (prevents duplicate orders) — Account A
        self._pending_buys: set = set()

        # Pending limit orders for Account B big-hitter (symbol -> TradeRecord)
        self._pending_limit_orders: Dict[str, TradeRecord] = {}

        # Symbols with close in progress (prevents WS + REST race)
        self._closing_in_progress: set = set()

    def _get_dashboard(self):
        """Get the dashboard instance (override or singleton)."""
        if self._dashboard_override is not None:
            return self._dashboard_override
        return get_dashboard()

    def reset_daily(self):
        """Reset for new trading day."""
        self.completed_trades = []
        self.daily_stats = DailyStats(date=date.today())
        self.traded_today = set()
        self._pending_buys = set()
        self._pending_limit_orders = {}
        self._closing_in_progress = set()
        logger.info("Position manager reset for new day")

    @property
    def num_positions(self) -> int:
        """Current number of open positions."""
        return len(self.active_trades)

    @property
    def num_pending(self) -> int:
        """Number of pending buy orders (Account A market + Account B limit)."""
        return len(self._pending_buys) + len(self._pending_limit_orders)

    @property
    def can_open_position(self) -> bool:
        """Check if we can open a new position (includes pending orders)."""
        total_positions = self.num_positions + self.num_pending
        return total_positions < self.config.MAX_CONCURRENT_POSITIONS

    def already_traded(self, symbol: str) -> bool:
        """Check if we already traded this symbol today (includes pending)."""
        return (symbol in self.traded_today
                or symbol in self._pending_buys
                or symbol in self._pending_limit_orders)

    def has_position(self, symbol: str) -> bool:
        """Check if we have an open position or pending order in symbol."""
        return (symbol in self.active_trades
                or symbol in self._pending_buys
                or symbol in self._pending_limit_orders)

    async def sync_positions(self):
        """Sync local state with Alpaca positions."""
        positions = await self.trader.get_positions()

        # Update or add positions we have
        alpaca_symbols = set()
        for pos in positions:
            alpaca_symbols.add(pos.symbol)
            # Mark as traded today to prevent re-entry
            self.traded_today.add(pos.symbol)
            if pos.symbol not in self.active_trades:
                # Position opened outside our tracking
                logger.warning(f"Found untracked position: {pos.symbol}")
                self.active_trades[pos.symbol] = TradeRecord(
                    symbol=pos.symbol,
                    entry_time=datetime.now(),
                    entry_price=pos.avg_entry_price,
                    shares=int(pos.qty),
                    signal_score=0,
                    signal_rsi=0,
                    signal_notional=0,
                )

        # Remove positions that were closed
        closed = [s for s in self.active_trades if s not in alpaca_symbols]
        for symbol in closed:
            trade = self.active_trades.pop(symbol)
            logger.info(f"Position {symbol} no longer open (external close)")

    async def sync_on_startup(self):
        """
        Sync positions at startup via 3-way reconciliation (DB + Alpaca).

        Cases:
        - DB + Alpaca: Restore TradeRecord with signal metadata from DB, live data from Alpaca
        - DB only (no Alpaca position): Position was closed externally / crash recovery — mark closed in DB
        - Alpaca only (no DB record): External trade — create DB record with zeroed signal metadata
        """
        logger.info("Syncing positions at startup (3-way reconciliation)...")

        # 1. Load open trades from paper_trades_log (or paper_trades_log_b)
        db_url = os.environ.get("DATABASE_URL")
        db_trades = {}
        if db_url:
            raw = load_open_trades_from_db(db_url, table_name=self.trades_table)
            for t in raw:
                db_trades[t["symbol"]] = t
            logger.info(f"Loaded {len(db_trades)} open trades from paper_trades_log")

        # 2. Get current Alpaca positions
        positions = await self.trader.get_positions()
        alpaca_map = {pos.symbol: pos for pos in positions}
        logger.info(f"Found {len(alpaca_map)} Alpaca positions")

        # Case A: DB + Alpaca — restore with full metadata
        for symbol in set(db_trades) & set(alpaca_map):
            db = db_trades[symbol]
            pos = alpaca_map[symbol]
            self.traded_today.add(symbol)
            self.active_trades[symbol] = TradeRecord(
                symbol=symbol,
                entry_time=db["entry_time"] or datetime.now(),
                entry_price=pos.avg_entry_price,
                shares=int(pos.qty),
                signal_score=db["signal_score"],
                signal_rsi=db["signal_rsi"],
                signal_notional=db["signal_notional"],
                trade_db_id=db["db_id"],
                # Account B big-hitter fields (present when table is paper_trades_log_b)
                direction=db.get("direction", "bullish"),
                stop_price=db.get("stop_price"),
                target_price=db.get("target_price"),
                risk_per_share=db.get("risk_per_share"),
                limit_order_id=db.get("limit_order_id"),
                pattern_date=db.get("pattern_date"),
                candle_range=db.get("candle_range"),
                pattern_strength=db.get("pattern_strength"),
            )
            logger.info(f"Restored from DB+Alpaca: {symbol} "
                       f"({pos.qty} shares @ ${pos.avg_entry_price:.2f}, "
                       f"direction={db.get('direction', 'bullish')})")

        # Case B: DB only — position closed externally or during crash
        for symbol in set(db_trades) - set(alpaca_map):
            db = db_trades[symbol]
            self.traded_today.add(symbol)
            if db_url:
                log_trade_close(
                    db_url=db_url,
                    trade_db_id=db["db_id"],
                    symbol=symbol,
                    exit_time=datetime.now(),
                    exit_price=db["entry_price"],
                    pnl=0.0,
                    pnl_pct=0.0,
                    exit_reason="crash_recovery",
                    table_name=self.trades_table,
                )
            logger.warning(f"DB-only (no Alpaca position): {symbol} — marked closed as crash_recovery")

        # Case C: Alpaca only — orphaned position, no DB record.
        # Adopt into active_trades and create a DB record so future restarts
        # see them as Case A. Normal exit logic (EOD closer / hard stop)
        # handles closing at the right time.
        orphaned = set(alpaca_map) - set(db_trades)
        if orphaned:
            logger.warning(f"Found {len(orphaned)} orphaned Alpaca positions (no DB record): "
                          f"{sorted(orphaned)}")
            for symbol in orphaned:
                pos = alpaca_map[symbol]
                self.traded_today.add(symbol)

                # Create DB record so future restarts find them as Case A
                trade_db_id = None
                if db_url:
                    try:
                        trade_db_id = log_trade_open(
                            db_url=db_url, symbol=symbol,
                            entry_time=datetime.now(), entry_price=pos.avg_entry_price,
                            shares=int(pos.qty), signal_score=0,
                            signal_rsi=0, signal_notional=0,
                            table_name=self.trades_table,
                        )
                    except Exception as e:
                        logger.error(f"Failed to create DB record for orphan {symbol}: {e}")

                self.active_trades[symbol] = TradeRecord(
                    symbol=symbol,
                    entry_time=datetime.now(),
                    entry_price=pos.avg_entry_price,
                    shares=int(pos.qty),
                    signal_score=0,
                    signal_rsi=0,
                    signal_notional=0,
                    trade_db_id=trade_db_id,
                )
                logger.warning(f"Adopted orphaned position: {symbol} "
                              f"({pos.qty} shares @ ${pos.avg_entry_price:.2f}) — "
                              f"will exit via normal EOD/stop logic")

        logger.info(f"Startup sync complete: {len(self.active_trades)} positions, "
                   f"{len(orphaned)} orphaned adopted, "
                   f"can_open={self.can_open_position} "
                   f"(max {self.config.MAX_CONCURRENT_POSITIONS})")

    async def calculate_position_size(self, symbol: str) -> int:
        """
        Calculate position size based on account and config.

        Returns number of shares to buy.
        """
        account = await self.trader.get_account()
        if not account:
            logger.error("Failed to get account for position sizing")
            return 0

        price = await self.trader.get_latest_price(symbol)
        if not price or price <= 0:
            logger.error(f"Failed to get price for {symbol}")
            return 0

        # Max position value based on portfolio
        max_position_value = account.portfolio_value * self.config.MAX_POSITION_SIZE_PCT

        # Don't exceed available buying power
        max_position_value = min(max_position_value, account.buying_power * 0.95)

        # Calculate shares
        shares = int(max_position_value / price)

        # Ensure at least 1 share if we're going to trade
        if shares < 1 and max_position_value > price:
            shares = 1

        logger.info(
            f"Position size for {symbol}: {shares} shares "
            f"(${shares * price:,.0f} / max ${max_position_value:,.0f})"
        )

        return shares

    async def open_position(
        self,
        symbol: str,
        signal_score: int,
        signal_rsi: float,
        signal_notional: float,
        volume_ratio: Optional[float] = None,
    ) -> Optional[TradeRecord]:
        """
        Open a new position.

        Returns TradeRecord if successful, None otherwise.
        """
        if not self.can_open_position:
            logger.warning(f"Cannot open {symbol}: max positions reached "
                          f"({self.num_positions} active + {self.num_pending} pending)")
            return None

        if self.already_traded(symbol):
            logger.info(f"Skipping {symbol}: already traded today")
            return None

        if self.has_position(symbol):
            logger.info(f"Already have position in {symbol}")
            return None

        # CRITICAL: Check actual Alpaca positions before buying
        # This catches any positions opened outside our tracking
        existing_position = await self.trader.get_position(symbol)
        if existing_position:
            logger.warning(f"Already have Alpaca position in {symbol} "
                          f"({existing_position.qty} shares) - syncing state")
            self.traded_today.add(symbol)
            return None

        # CRITICAL: Mark as pending BEFORE calculating size or submitting order
        # This prevents duplicate orders during async operations
        self._pending_buys.add(symbol)
        self.traded_today.add(symbol)
        logger.info(f"Marked {symbol} as pending buy "
                   f"(positions: {self.num_positions}, pending: {self.num_pending})")

        try:
            # Calculate position size
            shares = await self.calculate_position_size(symbol)
            if shares <= 0:
                logger.warning(f"Position size 0 for {symbol}")
                return None

            # Submit buy order
            order = await self.trader.buy(symbol, shares)
            if not order:
                logger.error(f"Failed to submit buy order for {symbol}")
                return None

            # Wait for fill with retry (market open fills can take >2s)
            position = None
            for attempt in range(4):  # 2s + 3s + 5s + 8s = 18s max
                wait = [2, 3, 5, 8][attempt]
                await asyncio.sleep(wait)

                filled_order = await self.trader.get_order(order.id)
                if filled_order and filled_order.filled_qty and int(filled_order.filled_qty) > 0:
                    position = await self.trader.get_position(symbol)
                    if position:
                        break

                logger.warning(f"Order not filled yet for {symbol} "
                              f"(attempt {attempt + 1}/4, waited {wait}s)")

            if not position:
                logger.error(f"Position not found after buy: {symbol} — "
                            f"order {order.id} may fill later (ghost position risk)")
                return None

            # Create trade record
            trade = TradeRecord(
                symbol=symbol,
                entry_time=datetime.now(),
                entry_price=position.avg_entry_price,
                shares=int(position.qty),
                signal_score=signal_score,
                signal_rsi=signal_rsi,
                signal_notional=signal_notional,
            )

            self.active_trades[symbol] = trade
            self.daily_stats.trades_entered += 1

            logger.info(
                f"Opened position: {symbol} {trade.shares} shares @ ${trade.entry_price:.2f}"
            )

            # Update dashboard
            if not self.skip_dashboard:
                dashboard = self._get_dashboard()
                if dashboard.enabled:
                    dashboard.update_position(symbol, trade.entry_price, trade.entry_price, "HOLDING", score=trade.signal_score)

            # Persist trade to DB
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                trade.trade_db_id = log_trade_open(
                    db_url=db_url,
                    symbol=trade.symbol,
                    entry_time=trade.entry_time,
                    entry_price=trade.entry_price,
                    shares=trade.shares,
                    signal_score=trade.signal_score,
                    signal_rsi=trade.signal_rsi,
                    signal_notional=trade.signal_notional,
                    table_name=self.trades_table,
                    volume_ratio=volume_ratio,
                )
                if not self.skip_dashboard:
                    update_signal_trade_placed(db_url, symbol, trade.entry_price)

            return trade

        finally:
            # Always remove from pending when done (success or failure)
            self._pending_buys.discard(symbol)

    async def close_position(
        self,
        symbol: str,
        reason: str = "manual",
    ) -> Optional[TradeRecord]:
        """
        Close a position.

        Returns completed TradeRecord if successful.
        Guards against concurrent close attempts (WS + REST race).
        """
        if symbol not in self.active_trades:
            logger.warning(f"No active trade for {symbol}")
            return None

        # Prevent concurrent close for the same symbol
        if symbol in self._closing_in_progress:
            logger.info(f"Close already in progress for {symbol}, skipping duplicate")
            return None
        self._closing_in_progress.add(symbol)

        try:
            trade = self.active_trades[symbol]

            # Get current position to verify
            position = await self.trader.get_position(symbol)
            if not position:
                # Position already closed
                logger.warning(f"Position {symbol} already closed")
                self.active_trades.pop(symbol, None)
                return None

            # Close position
            order = await self.trader.close_position(symbol)
            if not order:
                logger.error(f"Failed to close position {symbol}")
                return None

            # Wait for fill
            await asyncio.sleep(2)

            # Get exit price
            exit_price = order.filled_avg_price
            if not exit_price:
                # Try to get from latest trade
                latest = await self.trader.get_latest_price(symbol)
                exit_price = latest or trade.entry_price

            # Update trade record (direction-aware P&L)
            trade.exit_time = datetime.now()
            trade.exit_price = exit_price
            trade.exit_reason = reason
            if trade.direction == "bearish":
                trade.pnl = (trade.entry_price - trade.exit_price) * trade.shares
                trade.pnl_pct = (trade.entry_price - trade.exit_price) / trade.entry_price * 100
            else:
                trade.pnl = (trade.exit_price - trade.entry_price) * trade.shares
                trade.pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price * 100

            # Move to completed
            self.active_trades.pop(symbol, None)
            self.completed_trades.append(trade)

            # Update stats
            self.daily_stats.trades_exited += 1
            self.daily_stats.total_pnl += trade.pnl
            if trade.pnl > 0:
                self.daily_stats.winning_trades += 1
            else:
                self.daily_stats.losing_trades += 1

            logger.info(
                f"Closed position: {symbol} @ ${exit_price:.2f} "
                f"P&L: ${trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%) [{reason}]"
            )

            # Update dashboard - move to closed
            if not self.skip_dashboard:
                dashboard = self._get_dashboard()
                if dashboard.enabled:
                    dashboard.close_position(
                        symbol, trade.entry_price, exit_price, trade.exit_time,
                        shares=trade.shares, pnl_dollars=trade.pnl,
                        score=trade.signal_score,
                    )

            # Persist trade close to DB
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                log_trade_close(
                    db_url=db_url,
                    trade_db_id=trade.trade_db_id,
                    symbol=trade.symbol,
                    exit_time=trade.exit_time,
                    exit_price=trade.exit_price,
                    pnl=trade.pnl,
                    pnl_pct=trade.pnl_pct,
                    exit_reason=trade.exit_reason,
                    table_name=self.trades_table,
                )
                if not self.skip_dashboard:
                    close_signal_in_db(db_url, symbol, exit_price, trade.pnl_pct)

            return trade

        finally:
            self._closing_in_progress.discard(symbol)

    async def close_all_positions(self, reason: str = "eod") -> List[TradeRecord]:
        """Close all open positions."""
        closed = []
        symbols = list(self.active_trades.keys())

        for symbol in symbols:
            trade = await self.close_position(symbol, reason)
            if trade:
                closed.append(trade)

        return closed

    async def check_hard_stops(self) -> List[str]:
        """
        Check if any positions hit hard stop.

        Returns list of symbols that were stopped out.
        """
        if not self.config.USE_HARD_STOP:
            return []

        stopped = []
        positions = await self.trader.get_positions()

        for pos in positions:
            if pos.symbol not in self.active_trades:
                continue

            # Check if loss exceeds hard stop
            if pos.unrealized_plpc <= self.config.HARD_STOP_PCT:
                logger.warning(
                    f"Hard stop triggered: {pos.symbol} "
                    f"({pos.unrealized_plpc*100:.1f}%)"
                )
                trade = await self.close_position(pos.symbol, "stop")
                if trade:
                    stopped.append(pos.symbol)

        return stopped

    def get_daily_summary(self) -> Dict:
        """Get daily trading summary."""
        stats = self.daily_stats
        win_rate = (
            stats.winning_trades / stats.trades_exited * 100
            if stats.trades_exited > 0
            else 0
        )

        return {
            "date": stats.date.isoformat(),
            "trades_entered": stats.trades_entered,
            "trades_exited": stats.trades_exited,
            "signals_seen": stats.signals_seen,
            "signals_filtered": stats.signals_filtered,
            "total_pnl": stats.total_pnl,
            "winning_trades": stats.winning_trades,
            "losing_trades": stats.losing_trades,
            "win_rate": win_rate,
            "open_positions": self.num_positions,
        }

    async def update_dashboard_positions(self):
        """Update dashboard with current position prices and PnL.

        Uses clear-and-rewrite to prevent stale entries (self-healing).
        """
        if self.skip_dashboard:
            return
        dashboard = self._get_dashboard()
        if not dashboard.enabled:
            return

        positions = await self.trader.get_positions()
        rows = []
        for pos in positions:
            if pos.symbol in self.active_trades:
                trade = self.active_trades[pos.symbol]
                pnl = ((pos.current_price - trade.entry_price) / trade.entry_price) * 100 if trade.entry_price > 0 else 0
                rows.append([
                    pos.symbol,
                    trade.signal_score,
                    f"${trade.entry_price:.2f}",
                    f"${pos.current_price:.2f}",
                    f"{pnl:.2f}%",
                    "HOLDING",
                ])
        dashboard.rewrite_positions(rows)

    async def open_limit_position(self, setup, max_risk: float) -> Optional[TradeRecord]:
        """
        Submit a limit order for a big-hitter pattern (Account B).

        Args:
            setup: TradeSetup from PatternPoller
            max_risk: Maximum dollar risk per trade

        Returns:
            TradeRecord tracked in _pending_limit_orders, or None on failure.
        """
        from .engulfing_checker import TradeSetup  # avoid circular at module level

        if not self.can_open_position:
            logger.warning(f"Cannot open {setup.symbol}: max positions reached")
            return None
        if self.already_traded(setup.symbol):
            logger.info(f"Skipping {setup.symbol}: already traded today")
            return None

        # Calculate qty from risk budget
        qty = math.floor(max_risk / setup.risk_per_share)
        if qty < 1:
            logger.info(f"Skipping {setup.symbol}: risk/share ${setup.risk_per_share:.2f} "
                        f"exceeds max_risk ${max_risk:.2f}")
            return None

        # Submit limit order
        if setup.direction == "bullish":
            order = await self.trader.submit_order(
                setup.symbol, qty, OrderSide.BUY, OrderType.LIMIT,
                limit_price=round(setup.entry_price, 2),
            )
        else:
            order = await self.trader.sell_short(
                setup.symbol, qty, limit_price=round(setup.entry_price, 2),
            )

        if not order:
            logger.error(f"Limit order submission failed: {setup.symbol}")
            return None

        now = datetime.now()
        trade = TradeRecord(
            symbol=setup.symbol,
            entry_time=now,
            entry_price=setup.entry_price,
            shares=qty,
            signal_score=0,
            signal_rsi=0,
            signal_notional=0,
            direction=setup.direction,
            stop_price=setup.stop_loss,
            target_price=setup.target_1,
            risk_per_share=setup.risk_per_share,
            limit_order_id=order.id,
            order_submitted_at=now,
            pattern_date=setup.pattern_date,
            candle_range=setup.candle_range,
            pattern_strength=setup.pattern_strength,
            has_news=getattr(setup, 'has_news', False),
            article_count=getattr(setup, 'article_count', 0),
        )

        self._pending_limit_orders[setup.symbol] = trade
        self.traded_today.add(setup.symbol)

        logger.info(
            f"Limit order submitted: {setup.direction.upper()} {qty} {setup.symbol} "
            f"@ ${setup.entry_price:.2f} (stop=${setup.stop_loss:.2f}, "
            f"target=${setup.target_1:.2f}, risk/sh=${setup.risk_per_share:.2f})"
        )

        # Persist to DB immediately (so startup sync sees it)
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            trade.trade_db_id = log_trade_open(
                db_url=db_url,
                symbol=trade.symbol,
                entry_time=trade.entry_time,
                entry_price=trade.entry_price,
                shares=trade.shares,
                signal_score=0,
                signal_rsi=0,
                signal_notional=0,
                table_name=self.trades_table,
                direction=trade.direction,
                stop_price=trade.stop_price,
                target_price=trade.target_price,
                risk_per_share=trade.risk_per_share,
                limit_order_id=trade.limit_order_id,
                order_submitted_at=trade.order_submitted_at,
                pattern_date=trade.pattern_date,
                candle_range=trade.candle_range,
                pattern_strength=trade.pattern_strength,
                has_news=trade.has_news,
                article_count=trade.article_count,
            )

        return trade

    async def check_pending_orders(self, max_age_minutes: int = 30) -> Dict[str, list]:
        """
        Check fill status of pending limit orders.

        Moves filled orders to active_trades, cancels expired orders.

        Returns:
            {"filled": [TradeRecord, ...], "cancelled": [TradeRecord, ...]}
        """
        filled = []
        cancelled = []
        now = datetime.now()

        for symbol in list(self._pending_limit_orders.keys()):
            trade = self._pending_limit_orders[symbol]

            # Check order status
            order = await self.trader.get_order(trade.limit_order_id)
            if not order:
                logger.warning(f"Could not fetch order for {symbol}, keeping pending")
                continue

            if order.status.value == "filled":
                # Move to active trades with actual fill price
                trade.entry_price = order.filled_avg_price or trade.entry_price
                trade.shares = int(order.filled_qty) if order.filled_qty else trade.shares
                trade.entry_time = order.filled_at or now

                self.active_trades[symbol] = trade
                del self._pending_limit_orders[symbol]
                self.daily_stats.trades_entered += 1

                logger.info(
                    f"Limit order FILLED: {trade.direction.upper()} {trade.shares} "
                    f"{symbol} @ ${trade.entry_price:.2f}"
                )

                # Update dashboard
                if not self.skip_dashboard:
                    dashboard = self._get_dashboard()
                    if dashboard.enabled:
                        dashboard.update_position(
                            symbol, trade.entry_price, trade.entry_price,
                            "HOLDING", score=0,
                        )

                filled.append(trade)

            elif order.status.value in ("canceled", "rejected"):
                del self._pending_limit_orders[symbol]
                logger.info(f"Limit order {order.status.value}: {symbol}")
                cancelled.append(trade)

            else:
                # Still open — check age
                age_min = (now - trade.order_submitted_at).total_seconds() / 60
                if age_min >= max_age_minutes:
                    success = await self.trader.cancel_order(trade.limit_order_id)
                    if success:
                        del self._pending_limit_orders[symbol]
                        logger.info(f"Limit order EXPIRED ({age_min:.0f}min): {symbol}")
                        cancelled.append(trade)

                        # Mark as closed in DB
                        db_url = os.environ.get("DATABASE_URL")
                        if db_url and trade.trade_db_id:
                            log_trade_close(
                                db_url=db_url,
                                trade_db_id=trade.trade_db_id,
                                symbol=symbol,
                                exit_time=now,
                                exit_price=trade.entry_price,
                                pnl=0.0,
                                pnl_pct=0.0,
                                exit_reason="expired_unfilled",
                                table_name=self.trades_table,
                            )

        return {"filled": filled, "cancelled": cancelled}

    async def check_stops_and_targets(
        self,
        get_price_fn: Callable[[str], Awaitable[Optional[float]]],
    ) -> Dict[str, list]:
        """
        Check active trades with stop_price/target_price against current prices.

        Direction-aware: bullish stops below entry, bearish stops above entry.

        Args:
            get_price_fn: async function that returns current price for a symbol

        Returns:
            {"stopped": [symbol, ...], "target_hit": [symbol, ...]}
        """
        stopped = []
        target_hit = []

        for symbol in list(self.active_trades.keys()):
            trade = self.active_trades[symbol]

            # Only check trades that have stop/target set (Account B)
            if trade.stop_price is None and trade.target_price is None:
                continue

            price = await get_price_fn(symbol)
            if price is None:
                continue

            if trade.direction == "bullish":
                # Long: stop if price <= stop_price, target if price >= target_price
                if trade.stop_price and price <= trade.stop_price:
                    logger.warning(f"STOP HIT (bullish): {symbol} @ ${price:.2f} <= stop ${trade.stop_price:.2f}")
                    closed = await self.close_position(symbol, "stop")
                    if closed:
                        stopped.append(symbol)
                elif trade.target_price and price >= trade.target_price:
                    logger.info(f"TARGET HIT (bullish): {symbol} @ ${price:.2f} >= target ${trade.target_price:.2f}")
                    closed = await self.close_position(symbol, "target")
                    if closed:
                        target_hit.append(symbol)
            else:
                # Short: stop if price >= stop_price, target if price <= target_price
                if trade.stop_price and price >= trade.stop_price:
                    logger.warning(f"STOP HIT (bearish): {symbol} @ ${price:.2f} >= stop ${trade.stop_price:.2f}")
                    closed = await self.close_position(symbol, "stop")
                    if closed:
                        stopped.append(symbol)
                elif trade.target_price and price <= trade.target_price:
                    logger.info(f"TARGET HIT (bearish): {symbol} @ ${price:.2f} <= target ${trade.target_price:.2f}")
                    closed = await self.close_position(symbol, "target")
                    if closed:
                        target_hit.append(symbol)

        return {"stopped": stopped, "target_hit": target_hit}

    async def cancel_all_pending_limit_orders(self) -> List[TradeRecord]:
        """Cancel all pending limit orders (e.g., at EOD)."""
        cancelled = []
        for symbol in list(self._pending_limit_orders.keys()):
            trade = self._pending_limit_orders[symbol]
            success = await self.trader.cancel_order(trade.limit_order_id)
            if success:
                del self._pending_limit_orders[symbol]
                logger.info(f"EOD: cancelled pending limit order for {symbol}")

                db_url = os.environ.get("DATABASE_URL")
                if db_url and trade.trade_db_id:
                    log_trade_close(
                        db_url=db_url,
                        trade_db_id=trade.trade_db_id,
                        symbol=symbol,
                        exit_time=datetime.now(),
                        exit_price=trade.entry_price,
                        pnl=0.0,
                        pnl_pct=0.0,
                        exit_reason="eod_cancel",
                        table_name=self.trades_table,
                    )
                cancelled.append(trade)
        return cancelled

    def record_signal(self, passed_filter: bool):
        """Record a signal for stats."""
        self.daily_stats.signals_seen += 1
        if not passed_filter:
            self.daily_stats.signals_filtered += 1
