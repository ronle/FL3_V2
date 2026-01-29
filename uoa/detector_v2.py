"""
UOA Detector V2 (Component 3.3)

Detects unusual options activity by comparing rolling aggregates to baselines.
Features:
- Configurable thresholds
- Cooldown to prevent duplicate triggers
- Integration with BaselineManager
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_VOLUME_THRESHOLD = 3.0   # 3x baseline
DEFAULT_NOTIONAL_THRESHOLD = 3.0
DEFAULT_COOLDOWN_SECONDS = 300   # 5 minutes between triggers for same symbol


@dataclass
class UOATrigger:
    """UOA trigger event."""
    symbol: str
    trigger_ts: datetime
    trigger_type: str          # 'volume', 'notional', 'contracts'
    volume_ratio: float        # Actual / baseline
    notional: float            # Dollar value observed
    baseline_notional: float   # Expected baseline
    contracts: int             # Total contracts
    prints: int                # Number of trade prints
    bucket_start: dt_time      # Which 30-min bucket
    confidence: float          # Baseline confidence (0-1)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "symbol": self.symbol,
            "trigger_ts": self.trigger_ts.isoformat(),
            "trigger_type": self.trigger_type,
            "volume_ratio": self.volume_ratio,
            "notional": self.notional,
            "baseline_notional": self.baseline_notional,
            "contracts": self.contracts,
            "prints": self.prints,
            "bucket_start": self.bucket_start.strftime("%H:%M") if self.bucket_start else None,
            "confidence": self.confidence,
        }


class UOADetector:
    """
    Unusual Options Activity detector.

    Compares current rolling window stats against baselines
    and emits trigger events when thresholds are exceeded.

    Usage:
        detector = UOADetector(baseline_manager, on_trigger=handle_trigger)
        detector.check(symbol, window_stats)
    """

    def __init__(
        self,
        baseline_manager=None,
        on_trigger: Optional[Callable[[UOATrigger], None]] = None,
        volume_threshold: float = DEFAULT_VOLUME_THRESHOLD,
        notional_threshold: float = DEFAULT_NOTIONAL_THRESHOLD,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ):
        """
        Initialize detector.

        Args:
            baseline_manager: BaselineManager instance for baseline lookups
            on_trigger: Callback when UOA is detected
            volume_threshold: Multiplier threshold for volume
            notional_threshold: Multiplier threshold for notional
            cooldown_seconds: Minimum time between triggers for same symbol
        """
        self.baseline_manager = baseline_manager
        self.on_trigger = on_trigger
        self.volume_threshold = volume_threshold
        self.notional_threshold = notional_threshold
        self.cooldown_seconds = cooldown_seconds

        # Track recent triggers to enforce cooldown
        self._recent_triggers: dict[str, float] = {}  # symbol -> last trigger time
        self._trigger_count = 0
        self._check_count = 0

    def check(
        self,
        symbol: str,
        trade_count: int,
        total_notional: float,
        total_contracts: int,
        bucket_start: Optional[dt_time] = None,
        orats_daily_volume: Optional[int] = None,
    ) -> Optional[UOATrigger]:
        """
        Check if current activity exceeds baseline.

        Args:
            symbol: Underlying symbol
            trade_count: Number of trades in window
            total_notional: Total notional value
            total_contracts: Total contracts traded
            bucket_start: Current 30-min bucket start time
            orats_daily_volume: ORATS daily volume for fallback baseline

        Returns:
            UOATrigger if triggered, None otherwise
        """
        self._check_count += 1

        # Check cooldown
        if self._in_cooldown(symbol):
            return None

        # Get current bucket if not provided
        if bucket_start is None:
            now = datetime.now()
            bucket_start = dt_time(now.hour, (now.minute // 30) * 30)

        # Get baseline
        baseline_notional = self._get_baseline(symbol, bucket_start, orats_daily_volume)
        if baseline_notional <= 0:
            return None

        # Calculate ratio
        volume_ratio = total_notional / baseline_notional

        # Check threshold
        if volume_ratio >= self.volume_threshold:
            trigger = UOATrigger(
                symbol=symbol,
                trigger_ts=datetime.now(),
                trigger_type="notional",
                volume_ratio=volume_ratio,
                notional=total_notional,
                baseline_notional=baseline_notional,
                contracts=total_contracts,
                prints=trade_count,
                bucket_start=bucket_start,
                confidence=0.5 if orats_daily_volume else 0.8,  # Higher if from bucket history
            )

            self._record_trigger(symbol)
            self._trigger_count += 1

            if self.on_trigger:
                self.on_trigger(trigger)

            logger.info(
                f"UOA Trigger: {symbol} {volume_ratio:.1f}x baseline "
                f"(${total_notional:,.0f} vs ${baseline_notional:,.0f})"
            )

            return trigger

        return None

    def _get_baseline(
        self,
        symbol: str,
        bucket_start: dt_time,
        orats_daily_volume: Optional[int]
    ) -> float:
        """Get baseline notional for symbol and bucket."""
        # If we have a baseline manager with async support, use it
        if self.baseline_manager:
            baseline = self.baseline_manager.get_baseline_sync(
                symbol, bucket_start, orats_daily_volume
            )
            return baseline.expected_notional

        # Fallback: simple ORATS-based calculation
        if orats_daily_volume and orats_daily_volume > 0:
            # Simple time-of-day multiplier
            hour = bucket_start.hour
            if hour < 10 or hour >= 15:
                multiplier = 2.0  # Open/close
            elif 11 <= hour <= 13:
                multiplier = 0.6  # Midday
            else:
                multiplier = 1.0

            per_minute = orats_daily_volume / 390
            return per_minute * 30 * multiplier

        # Default baseline
        return 10000  # $10K default

    def _in_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown period."""
        if symbol not in self._recent_triggers:
            return False

        elapsed = time.time() - self._recent_triggers[symbol]
        return elapsed < self.cooldown_seconds

    def _record_trigger(self, symbol: str) -> None:
        """Record trigger time for cooldown tracking."""
        self._recent_triggers[symbol] = time.time()

    def clear_cooldowns(self) -> None:
        """Clear all cooldown records."""
        self._recent_triggers.clear()

    def get_metrics(self) -> dict:
        """Get detector metrics."""
        return {
            "total_checks": self._check_count,
            "total_triggers": self._trigger_count,
            "trigger_rate": self._trigger_count / self._check_count if self._check_count > 0 else 0,
            "symbols_in_cooldown": len(self._recent_triggers),
            "volume_threshold": self.volume_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


class AsyncUOADetector(UOADetector):
    """
    Async version of UOA detector for use with async baseline manager.
    """

    async def check_async(
        self,
        symbol: str,
        trade_count: int,
        total_notional: float,
        total_contracts: int,
        bucket_start: Optional[dt_time] = None,
    ) -> Optional[UOATrigger]:
        """
        Async check with baseline lookup.

        Args:
            symbol: Underlying symbol
            trade_count: Number of trades in window
            total_notional: Total notional value
            total_contracts: Total contracts traded
            bucket_start: Current 30-min bucket start time

        Returns:
            UOATrigger if triggered, None otherwise
        """
        self._check_count += 1

        if self._in_cooldown(symbol):
            return None

        if bucket_start is None:
            now = datetime.now()
            bucket_start = dt_time(now.hour, (now.minute // 30) * 30)

        # Async baseline lookup
        if self.baseline_manager:
            baseline = await self.baseline_manager.get_baseline(symbol, bucket_start)
            baseline_notional = baseline.expected_notional
            confidence = baseline.confidence
        else:
            baseline_notional = 10000
            confidence = 0.1

        if baseline_notional <= 0:
            return None

        volume_ratio = total_notional / baseline_notional

        if volume_ratio >= self.volume_threshold:
            trigger = UOATrigger(
                symbol=symbol,
                trigger_ts=datetime.now(),
                trigger_type="notional",
                volume_ratio=volume_ratio,
                notional=total_notional,
                baseline_notional=baseline_notional,
                contracts=total_contracts,
                prints=trade_count,
                bucket_start=bucket_start,
                confidence=confidence,
            )

            self._record_trigger(symbol)
            self._trigger_count += 1

            if self.on_trigger:
                self.on_trigger(trigger)

            return trigger

        return None


if __name__ == "__main__":
    print("UOA Detector Tests")
    print("=" * 60)

    triggers_received = []

    def handle_trigger(trigger: UOATrigger):
        triggers_received.append(trigger)
        print(f"  TRIGGER: {trigger.symbol} {trigger.volume_ratio:.1f}x")

    detector = UOADetector(
        on_trigger=handle_trigger,
        volume_threshold=3.0,
        cooldown_seconds=5,  # Short cooldown for testing
    )

    # Test 1: Below threshold - no trigger
    print("\nTest 1: Below threshold (2x)")
    result = detector.check(
        symbol="AAPL",
        trade_count=100,
        total_notional=20000,  # 2x default baseline of 10K
        total_contracts=500,
        orats_daily_volume=10000,
    )
    print(f"  Result: {'TRIGGER' if result else 'No trigger'}")

    # Test 2: Above threshold - should trigger
    print("\nTest 2: Above threshold (5x)")
    result = detector.check(
        symbol="TSLA",
        trade_count=500,
        total_notional=50000,  # 5x baseline
        total_contracts=2000,
        orats_daily_volume=10000,
    )
    print(f"  Result: {'TRIGGER' if result else 'No trigger'}")

    # Test 3: Same symbol in cooldown - no trigger
    print("\nTest 3: Same symbol in cooldown")
    result = detector.check(
        symbol="TSLA",
        trade_count=500,
        total_notional=50000,
        total_contracts=2000,
        orats_daily_volume=10000,
    )
    print(f"  Result: {'TRIGGER' if result else 'No trigger (cooldown)'}")

    # Test 4: Different symbol - should trigger
    print("\nTest 4: Different symbol (5x)")
    result = detector.check(
        symbol="NVDA",
        trade_count=300,
        total_notional=60000,
        total_contracts=1500,
        orats_daily_volume=10000,
    )
    print(f"  Result: {'TRIGGER' if result else 'No trigger'}")

    print(f"\nTotal triggers: {len(triggers_received)}")
    print(f"Metrics: {detector.get_metrics()}")
