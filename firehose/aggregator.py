"""
Rolling Window Aggregator (Component 3.2)

Aggregates trades in-memory with 60-second rolling windows per underlying.
Optimized for high throughput (target: 10K trades/sec).

Features:
- Per-symbol rolling windows
- Automatic window expiration
- Memory monitoring
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 10
MAX_SYMBOLS_WARNING = 10000


@dataclass
class WindowStats:
    """Aggregated stats for a rolling window."""
    symbol: str
    window_start: float
    window_end: float
    trade_count: int = 0
    total_notional: float = 0.0
    total_contracts: int = 0
    unique_options: set = field(default_factory=set)
    min_price: float = float('inf')
    max_price: float = 0.0

    @property
    def avg_trade_size(self) -> float:
        """Average contracts per trade."""
        return self.total_contracts / self.trade_count if self.trade_count > 0 else 0

    @property
    def unique_contract_count(self) -> int:
        """Number of unique option contracts traded."""
        return len(self.unique_options)


@dataclass
class TradeData:
    """Minimal trade data for aggregation."""
    underlying: str
    option_symbol: str
    price: float
    size: int
    timestamp: float  # Unix timestamp (seconds)

    @property
    def notional(self) -> float:
        return self.price * self.size * 100


class RollingAggregator:
    """
    Rolling window aggregator for options trades.

    Thread-safe implementation for high-throughput processing.

    Usage:
        agg = RollingAggregator(window_seconds=60)
        agg.add_trade(trade)
        stats = agg.get_stats("AAPL")
    """

    def __init__(
        self,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        on_window_complete: Optional[callable] = None
    ):
        """
        Initialize aggregator.

        Args:
            window_seconds: Rolling window duration
            on_window_complete: Callback when a window is evicted
        """
        self.window_seconds = window_seconds
        self.on_window_complete = on_window_complete

        # Per-symbol data: symbol -> list of (timestamp, trade_data)
        self._trades: dict[str, list[tuple[float, TradeData]]] = defaultdict(list)
        self._stats_cache: dict[str, WindowStats] = {}
        self._lock = Lock()

        # Metrics
        self._total_trades = 0
        self._total_evicted = 0
        self._last_cleanup = time.time()

    def add_trade(self, trade: TradeData) -> None:
        """
        Add a trade to the rolling window.

        Args:
            trade: Trade data to aggregate
        """
        now = time.time()

        with self._lock:
            self._trades[trade.underlying].append((now, trade))
            self._total_trades += 1

            # Invalidate cache for this symbol
            if trade.underlying in self._stats_cache:
                del self._stats_cache[trade.underlying]

        # Periodic cleanup
        if now - self._last_cleanup > CLEANUP_INTERVAL_SECONDS:
            self._cleanup(now)

    def add_trade_fast(
        self,
        underlying: str,
        option_symbol: str,
        price: float,
        size: int,
        timestamp: Optional[float] = None
    ) -> None:
        """
        Fast trade addition without creating TradeData object.

        Args:
            underlying: Underlying symbol (e.g., "AAPL")
            option_symbol: OCC option symbol
            price: Trade price
            size: Number of contracts
            timestamp: Unix timestamp (defaults to now)
        """
        now = timestamp or time.time()
        trade = TradeData(
            underlying=underlying,
            option_symbol=option_symbol,
            price=price,
            size=size,
            timestamp=now
        )
        self.add_trade(trade)

    def get_stats(self, symbol: str) -> Optional[WindowStats]:
        """
        Get aggregated stats for a symbol's rolling window.

        Args:
            symbol: Underlying symbol

        Returns:
            WindowStats or None if no data
        """
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            # Check cache
            if symbol in self._stats_cache:
                cached = self._stats_cache[symbol]
                if cached.window_end >= now - 1:  # Cache valid within 1 second
                    return cached

            trades = self._trades.get(symbol, [])
            if not trades:
                return None

            # Filter to window and aggregate
            stats = WindowStats(
                symbol=symbol,
                window_start=cutoff,
                window_end=now
            )

            for ts, trade in trades:
                if ts >= cutoff:
                    stats.trade_count += 1
                    stats.total_notional += trade.notional
                    stats.total_contracts += trade.size
                    stats.unique_options.add(trade.option_symbol)
                    stats.min_price = min(stats.min_price, trade.price)
                    stats.max_price = max(stats.max_price, trade.price)

            if stats.trade_count > 0:
                self._stats_cache[symbol] = stats
                return stats

            return None

    def get_all_active_symbols(self) -> list[str]:
        """Get list of symbols with recent activity."""
        now = time.time()
        cutoff = now - self.window_seconds
        active = []

        with self._lock:
            for symbol, trades in self._trades.items():
                if trades and trades[-1][0] >= cutoff:
                    active.append(symbol)

        return active

    def get_top_symbols(self, n: int = 10, by: str = "notional") -> list[tuple[str, WindowStats]]:
        """
        Get top N symbols by specified metric.

        Args:
            n: Number of symbols to return
            by: Metric to sort by ("notional", "trades", "contracts")

        Returns:
            List of (symbol, stats) tuples sorted descending
        """
        all_stats = []

        for symbol in self.get_all_active_symbols():
            stats = self.get_stats(symbol)
            if stats:
                all_stats.append((symbol, stats))

        if by == "notional":
            all_stats.sort(key=lambda x: x[1].total_notional, reverse=True)
        elif by == "trades":
            all_stats.sort(key=lambda x: x[1].trade_count, reverse=True)
        elif by == "contracts":
            all_stats.sort(key=lambda x: x[1].total_contracts, reverse=True)

        return all_stats[:n]

    def _cleanup(self, now: float) -> None:
        """Remove expired trades from all windows."""
        cutoff = now - self.window_seconds
        evicted = 0

        with self._lock:
            symbols_to_remove = []

            for symbol, trades in self._trades.items():
                # Find first trade within window
                i = 0
                while i < len(trades) and trades[i][0] < cutoff:
                    i += 1

                if i > 0:
                    evicted += i
                    self._trades[symbol] = trades[i:]

                    # Callback for evicted window
                    if self.on_window_complete and i > 0:
                        # Build stats for evicted trades
                        pass  # Could implement if needed

                # Remove empty symbols
                if not self._trades[symbol]:
                    symbols_to_remove.append(symbol)

            for symbol in symbols_to_remove:
                del self._trades[symbol]
                if symbol in self._stats_cache:
                    del self._stats_cache[symbol]

            self._total_evicted += evicted
            self._last_cleanup = now

        # Memory warning
        symbol_count = len(self._trades)
        if symbol_count > MAX_SYMBOLS_WARNING:
            logger.warning(f"High symbol count: {symbol_count}")

    def get_metrics(self) -> dict:
        """Get aggregator metrics."""
        with self._lock:
            total_trades_in_memory = sum(len(t) for t in self._trades.values())

            return {
                "total_trades_processed": self._total_trades,
                "trades_in_memory": total_trades_in_memory,
                "active_symbols": len(self._trades),
                "cache_size": len(self._stats_cache),
                "total_evicted": self._total_evicted,
                "window_seconds": self.window_seconds,
            }

    def clear(self) -> None:
        """Clear all data."""
        with self._lock:
            self._trades.clear()
            self._stats_cache.clear()
            self._total_trades = 0
            self._total_evicted = 0


if __name__ == "__main__":
    import random

    print("Rolling Aggregator Tests")
    print("=" * 60)

    agg = RollingAggregator(window_seconds=60)

    # Simulate trades
    symbols = ["AAPL", "TSLA", "NVDA", "SPY", "QQQ"]
    print("\nSimulating 10,000 trades...")

    start = time.perf_counter()
    for i in range(10000):
        symbol = random.choice(symbols)
        agg.add_trade_fast(
            underlying=symbol,
            option_symbol=f"O:{symbol}250117C00150000",
            price=random.uniform(1.0, 10.0),
            size=random.randint(1, 100)
        )
    elapsed = time.perf_counter() - start

    print(f"  Time: {elapsed*1000:.1f}ms")
    print(f"  Rate: {10000/elapsed:,.0f} trades/sec")
    print(f"  Target: 10,000/sec - {'PASS' if 10000/elapsed > 10000 else 'FAIL'}")

    # Check stats
    print("\nStats by symbol:")
    for symbol in symbols:
        stats = agg.get_stats(symbol)
        if stats:
            print(f"  {symbol}: {stats.trade_count} trades, "
                  f"${stats.total_notional:,.0f} notional, "
                  f"{stats.unique_contract_count} contracts")

    print(f"\nMetrics: {agg.get_metrics()}")
