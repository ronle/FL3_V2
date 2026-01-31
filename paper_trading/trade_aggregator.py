"""
Trade Aggregator for Paper Trading

Bridges firehose trades with the rolling aggregator and adds
scoring logic for signal detection.
"""

import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List

from firehose.client import Trade
from firehose.aggregator import RollingAggregator, TradeData

logger = logging.getLogger(__name__)

# OCC symbol regex: O:UNDERLYING{YYMMDD}{C/P}{STRIKE}
OCC_REGEX = re.compile(r"O:([A-Z]+)(\d{6})([CP])(\d{8})")

# UOA detection thresholds
BASELINE_MULTIPLIER = 3.0
MIN_NOTIONAL_FOR_TRIGGER = 25_000


@dataclass
class SymbolState:
    """Tracks state for a symbol for scoring."""
    symbol: str
    last_baseline_notional: float = 50_000  # Default baseline
    trigger_count_today: int = 0
    last_trigger_time: Optional[float] = None
    cooldown_until: Optional[float] = None

    # Scoring components
    call_notional: float = 0
    put_notional: float = 0
    sweep_notional: float = 0
    total_contracts: int = 0
    unique_strikes: set = field(default_factory=set)


class TradeAggregator:
    """
    Aggregates trades from firehose and detects signals.

    Combines:
    - Rolling aggregation (60s windows)
    - UOA scoring
    - Trigger detection
    """

    def __init__(
        self,
        window_seconds: int = 60,
        baseline_multiplier: float = BASELINE_MULTIPLIER,
        cooldown_seconds: int = 300,
    ):
        self._aggregator = RollingAggregator(window_seconds=window_seconds)
        self.baseline_multiplier = baseline_multiplier
        self.cooldown_seconds = cooldown_seconds

        # Per-symbol state
        self._symbol_state: Dict[str, SymbolState] = defaultdict(lambda: SymbolState(""))

        # Baselines (could be loaded from database)
        self._baselines: Dict[str, float] = {}

        # Triggered symbols this check
        self._triggered: Dict[str, dict] = {}

    def add_trade(self, trade: Trade):
        """
        Add a trade from firehose.

        Parses OCC symbol and aggregates by underlying.
        """
        # Parse OCC symbol
        match = OCC_REGEX.match(trade.symbol)
        if not match:
            return

        underlying = match.group(1)
        right = "call" if match.group(3) == "C" else "put"
        strike = int(match.group(4)) / 1000

        # Convert to TradeData
        trade_data = TradeData(
            underlying=underlying,
            option_symbol=trade.symbol,
            price=trade.price,
            size=trade.size,
            timestamp=trade.timestamp / 1000  # Convert ms to seconds
        )

        # Add to rolling aggregator
        self._aggregator.add_trade(trade_data)

        # Update symbol state for scoring
        state = self._symbol_state[underlying]
        state.symbol = underlying
        state.total_contracts += trade.size

        notional = trade.price * trade.size * 100
        if right == "call":
            state.call_notional += notional
        else:
            state.put_notional += notional

        # Track sweeps (condition 209)
        if 209 in trade.conditions:
            state.sweep_notional += notional

        # Track strike concentration
        state.unique_strikes.add(strike)

    def get_baseline(self, symbol: str) -> float:
        """Get baseline notional for a symbol."""
        return self._baselines.get(symbol, 50_000)

    def set_baseline(self, symbol: str, baseline: float):
        """Set baseline for a symbol."""
        self._baselines[symbol] = baseline

    def load_baselines(self, baselines: Dict[str, float]):
        """Load baselines from external source."""
        self._baselines.update(baselines)

    def calculate_score(self, symbol: str) -> int:
        """
        Calculate UOA score for a symbol.

        Score components (0-20+):
        - Volume ratio (0-5): notional / baseline
        - Call concentration (0-3): call% > 70%
        - Sweep activity (0-3): sweep% > 30%
        - Strike concentration (0-3): few strikes = concentrated
        - Size (0-3): large notional
        """
        breakdown = self.calculate_score_breakdown(symbol)
        return breakdown.get("total", 0)

    def calculate_score_breakdown(self, symbol: str) -> dict:
        """
        Calculate UOA score with full breakdown for logging.

        Returns dict with individual component scores and metrics.
        """
        stats = self._aggregator.get_stats(symbol)
        if not stats:
            return {"total": 0}

        state = self._symbol_state[symbol]
        baseline = self.get_baseline(symbol)

        breakdown = {
            "score_volume": 0,
            "score_call_pct": 0,
            "score_sweep": 0,
            "score_strikes": 0,
            "score_notional": 0,
            "total": 0,
            # Raw metrics
            "ratio": 0,
            "call_pct": 0,
            "sweep_pct": 0,
            "num_strikes": 0,
        }

        # Volume ratio score (0-5)
        ratio = stats.total_notional / baseline if baseline > 0 else 0
        breakdown["ratio"] = round(ratio, 2)
        if ratio >= 5:
            breakdown["score_volume"] = 5
        elif ratio >= 4:
            breakdown["score_volume"] = 4
        elif ratio >= 3:
            breakdown["score_volume"] = 3
        elif ratio >= 2:
            breakdown["score_volume"] = 2
        elif ratio >= 1.5:
            breakdown["score_volume"] = 1

        # Call concentration (0-3)
        total_directional = state.call_notional + state.put_notional
        if total_directional > 0:
            call_pct = state.call_notional / total_directional
            breakdown["call_pct"] = round(call_pct, 4)
            if call_pct >= 0.85:
                breakdown["score_call_pct"] = 3
            elif call_pct >= 0.75:
                breakdown["score_call_pct"] = 2
            elif call_pct >= 0.65:
                breakdown["score_call_pct"] = 1

        # Sweep activity (0-3)
        if stats.total_notional > 0:
            sweep_pct = state.sweep_notional / stats.total_notional
            breakdown["sweep_pct"] = round(sweep_pct, 4)
            if sweep_pct >= 0.5:
                breakdown["score_sweep"] = 3
            elif sweep_pct >= 0.3:
                breakdown["score_sweep"] = 2
            elif sweep_pct >= 0.15:
                breakdown["score_sweep"] = 1

        # Strike concentration (0-3)
        n_strikes = len(state.unique_strikes)
        breakdown["num_strikes"] = n_strikes
        if n_strikes <= 2:
            breakdown["score_strikes"] = 3
        elif n_strikes <= 4:
            breakdown["score_strikes"] = 2
        elif n_strikes <= 6:
            breakdown["score_strikes"] = 1

        # Size score (0-3)
        if stats.total_notional >= 200_000:
            breakdown["score_notional"] = 3
        elif stats.total_notional >= 100_000:
            breakdown["score_notional"] = 2
        elif stats.total_notional >= 50_000:
            breakdown["score_notional"] = 1

        breakdown["total"] = (
            breakdown["score_volume"] +
            breakdown["score_call_pct"] +
            breakdown["score_sweep"] +
            breakdown["score_strikes"] +
            breakdown["score_notional"]
        )

        return breakdown

    def check_triggers(self) -> Dict[str, dict]:
        """
        Check all active symbols for trigger conditions.

        Returns dict of triggered symbols with their stats.
        """
        self._triggered.clear()
        now = time.time()

        for symbol in self._aggregator.get_all_active_symbols():
            state = self._symbol_state[symbol]

            # Skip if in cooldown
            if state.cooldown_until and now < state.cooldown_until:
                continue

            stats = self._aggregator.get_stats(symbol)
            if not stats:
                continue

            # Check notional threshold
            if stats.total_notional < MIN_NOTIONAL_FOR_TRIGGER:
                continue

            # Check volume ratio
            baseline = self.get_baseline(symbol)
            ratio = stats.total_notional / baseline if baseline > 0 else 0

            if ratio < self.baseline_multiplier:
                continue

            # Calculate score with breakdown
            breakdown = self.calculate_score_breakdown(symbol)

            # This is a trigger
            total_directional = state.call_notional + state.put_notional
            call_pct = state.call_notional / total_directional if total_directional > 0 else 0.5
            sweep_pct = state.sweep_notional / stats.total_notional if stats.total_notional > 0 else 0

            self._triggered[symbol] = {
                "score": breakdown["total"],
                "notional": stats.total_notional,
                "contracts": stats.total_contracts,
                "ratio": ratio,
                "call_pct": call_pct,
                "sweep_pct": sweep_pct,
                "num_strikes": len(state.unique_strikes),
                "trade_count": stats.trade_count,
                "price": 0,  # Would need current price
                "trend": 0,  # Would need trend calculation
                # Score breakdown
                "score_volume": breakdown["score_volume"],
                "score_call_pct": breakdown["score_call_pct"],
                "score_sweep": breakdown["score_sweep"],
                "score_strikes": breakdown["score_strikes"],
                "score_notional": breakdown["score_notional"],
            }

            # Set cooldown
            state.cooldown_until = now + self.cooldown_seconds
            state.trigger_count_today += 1
            state.last_trigger_time = now

            logger.debug(f"Trigger: {symbol} score={breakdown['total']} notional=${stats.total_notional:,.0f}")

        return self._triggered

    def get_triggered_symbols(self) -> Dict[str, dict]:
        """Get currently triggered symbols."""
        return self.check_triggers()

    def reset_daily(self):
        """Reset daily counters."""
        self._symbol_state.clear()
        self._triggered.clear()

    def get_metrics(self) -> dict:
        """Get aggregator metrics."""
        return {
            **self._aggregator.get_metrics(),
            "symbols_tracked": len(self._symbol_state),
            "current_triggers": len(self._triggered),
        }


if __name__ == "__main__":
    print("Trade Aggregator Test")
    print("=" * 60)

    agg = TradeAggregator(window_seconds=60)

    # Set some baselines
    agg.set_baseline("AAPL", 100_000)
    agg.set_baseline("TSLA", 150_000)

    # Simulate trades
    import random

    class MockTrade:
        def __init__(self, symbol, price, size, conditions=None):
            self.symbol = symbol
            self.price = price
            self.size = size
            self.timestamp = int(time.time() * 1000)
            self.conditions = conditions or []
            self.exchange = 1

    symbols = [
        "O:AAPL250117C00200000",
        "O:AAPL250117C00205000",
        "O:TSLA250117C00300000",
        "O:NVDA250117C00500000",
    ]

    print("\nSimulating trades...")
    for i in range(100):
        trade = MockTrade(
            symbol=random.choice(symbols),
            price=random.uniform(1.0, 10.0),
            size=random.randint(10, 500),
            conditions=[209] if random.random() > 0.7 else [],
        )
        agg.add_trade(trade)

    # Check triggers
    print("\nTriggered symbols:")
    triggered = agg.get_triggered_symbols()
    for symbol, stats in triggered.items():
        print(f"  {symbol}: score={stats['score']}, "
              f"notional=${stats['notional']:,.0f}, "
              f"call%={stats['call_pct']*100:.0f}%")

    print(f"\nMetrics: {agg.get_metrics()}")
