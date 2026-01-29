"""
Setup Phase Detector (Component 5.1)

Detects Phase 1 (Setup) of pump-and-dump pattern:
- UOA trigger (volume > 3x baseline)
- IV elevation (iv_rank > 50)
- OI building (call OI increasing)

Setup phase indicates smart money accumulation before the move.

Usage:
    detector = SetupPhaseDetector()
    signal = detector.detect(symbol, uoa_data, orats_data, snapshot_data)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SetupSignal:
    """Setup phase detection signal."""
    symbol: str
    timestamp: datetime
    phase: str = "SETUP"
    score: float = 0.0  # 0-1 confidence score

    # Individual signal components
    uoa_triggered: bool = False
    uoa_volume_ratio: float = 0.0

    iv_elevated: bool = False
    iv_rank: float = 0.0

    oi_building: bool = False
    call_oi_change: float = 0.0

    # Contributing factors for logging/analysis
    contributing_factors: list = None

    def __post_init__(self):
        if self.contributing_factors is None:
            self.contributing_factors = []

    @property
    def is_triggered(self) -> bool:
        """Returns True if setup phase criteria met."""
        return self.score >= 0.5

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "score": self.score,
            "uoa_triggered": self.uoa_triggered,
            "uoa_volume_ratio": self.uoa_volume_ratio,
            "iv_elevated": self.iv_elevated,
            "iv_rank": self.iv_rank,
            "oi_building": self.oi_building,
            "call_oi_change": self.call_oi_change,
            "contributing_factors": self.contributing_factors,
        }


class SetupPhaseDetector:
    """
    Detects Setup phase of P&D pattern.

    Setup phase characteristics:
    1. Unusual options activity (volume spike)
    2. Elevated implied volatility (anticipation)
    3. Open interest building in calls (accumulation)

    Scoring:
    - UOA trigger: 0.4 weight (required)
    - IV elevation: 0.3 weight
    - OI building: 0.3 weight

    Usage:
        detector = SetupPhaseDetector()
        signal = detector.detect(
            symbol="AAPL",
            uoa_data={"volume_ratio": 5.0, "triggered": True},
            orats_data={"iv_rank": 65},
            snapshot_data={"call_oi_change_pct": 0.15}
        )
    """

    def __init__(
        self,
        uoa_threshold: float = 3.0,
        iv_rank_threshold: float = 50.0,
        oi_change_threshold: float = 0.10,  # 10% increase
        uoa_weight: float = 0.4,
        iv_weight: float = 0.3,
        oi_weight: float = 0.3,
    ):
        """
        Initialize detector with thresholds.

        Args:
            uoa_threshold: Volume ratio threshold for UOA
            iv_rank_threshold: IV rank threshold (0-100)
            oi_change_threshold: Min OI change percentage
            uoa_weight: Weight for UOA in score
            iv_weight: Weight for IV in score
            oi_weight: Weight for OI in score
        """
        self.uoa_threshold = uoa_threshold
        self.iv_rank_threshold = iv_rank_threshold
        self.oi_change_threshold = oi_change_threshold

        self.uoa_weight = uoa_weight
        self.iv_weight = iv_weight
        self.oi_weight = oi_weight

        # Metrics
        self._total_checks = 0
        self._triggers = 0

    def detect(
        self,
        symbol: str,
        uoa_data: Optional[dict] = None,
        orats_data: Optional[dict] = None,
        snapshot_data: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> SetupSignal:
        """
        Detect setup phase for a symbol.

        Args:
            symbol: Stock symbol
            uoa_data: UOA trigger data {volume_ratio, triggered, notional, ...}
            orats_data: ORATS data {iv_rank, iv_mean, ...}
            snapshot_data: Option chain snapshot {call_oi_change_pct, ...}
            timestamp: Detection timestamp

        Returns:
            SetupSignal with detection results
        """
        self._total_checks += 1
        ts = timestamp or datetime.now()

        signal = SetupSignal(symbol=symbol, timestamp=ts)

        # Check UOA trigger (required for setup)
        uoa_score = self._check_uoa(signal, uoa_data)

        # Check IV elevation
        iv_score = self._check_iv(signal, orats_data)

        # Check OI building
        oi_score = self._check_oi(signal, snapshot_data)

        # Calculate weighted score
        # UOA is required - if not triggered, cap score at 0.3
        if signal.uoa_triggered:
            signal.score = (
                uoa_score * self.uoa_weight +
                iv_score * self.iv_weight +
                oi_score * self.oi_weight
            )
        else:
            # Without UOA, max score is from IV + OI only
            signal.score = (iv_score * self.iv_weight + oi_score * self.oi_weight) * 0.5

        signal.score = round(signal.score, 3)

        if signal.is_triggered:
            self._triggers += 1
            logger.info(
                f"SETUP detected: {symbol} score={signal.score:.2f} "
                f"factors={signal.contributing_factors}"
            )

        return signal

    def _check_uoa(self, signal: SetupSignal, uoa_data: Optional[dict]) -> float:
        """Check UOA trigger condition."""
        if not uoa_data:
            return 0.0

        volume_ratio = uoa_data.get("volume_ratio", 0)
        triggered = uoa_data.get("triggered", False)

        signal.uoa_volume_ratio = volume_ratio

        if triggered or volume_ratio >= self.uoa_threshold:
            signal.uoa_triggered = True
            signal.contributing_factors.append(f"UOA {volume_ratio:.1f}x")

            # Score based on how much over threshold
            # 3x = 1.0, 6x = 1.5 (capped at 1.0)
            score = min(1.0, volume_ratio / self.uoa_threshold)
            return score

        return 0.0

    def _check_iv(self, signal: SetupSignal, orats_data: Optional[dict]) -> float:
        """Check IV elevation condition."""
        if not orats_data:
            return 0.0

        iv_rank = orats_data.get("iv_rank", orats_data.get("ivRank", 0))
        signal.iv_rank = iv_rank

        if iv_rank >= self.iv_rank_threshold:
            signal.iv_elevated = True
            signal.contributing_factors.append(f"IV rank {iv_rank:.0f}")

            # Score: 50 = 0.5, 100 = 1.0
            score = min(1.0, iv_rank / 100)
            return score

        # Partial score for elevated but below threshold
        if iv_rank >= 30:
            return iv_rank / 100 * 0.5

        return 0.0

    def _check_oi(self, signal: SetupSignal, snapshot_data: Optional[dict]) -> float:
        """Check OI building condition."""
        if not snapshot_data:
            return 0.0

        # Check for call OI increase
        call_oi_change = snapshot_data.get("call_oi_change_pct", 0)
        signal.call_oi_change = call_oi_change

        if call_oi_change >= self.oi_change_threshold:
            signal.oi_building = True
            signal.contributing_factors.append(f"Call OI +{call_oi_change*100:.0f}%")

            # Score: 10% = 1.0, 20%+ = 1.0 (capped)
            score = min(1.0, call_oi_change / self.oi_change_threshold)
            return score

        # Check absolute OI if percentage not available
        call_oi = snapshot_data.get("total_call_oi", 0)
        put_oi = snapshot_data.get("total_put_oi", 0)

        if call_oi > 0 and put_oi > 0:
            # Call-heavy OI ratio suggests bullish accumulation
            ratio = call_oi / (call_oi + put_oi)
            if ratio > 0.6:  # More than 60% calls
                signal.contributing_factors.append(f"Call ratio {ratio*100:.0f}%")
                return 0.5

        return 0.0

    def get_metrics(self) -> dict:
        """Get detector metrics."""
        return {
            "total_checks": self._total_checks,
            "triggers": self._triggers,
            "trigger_rate": self._triggers / self._total_checks if self._total_checks > 0 else 0,
        }


if __name__ == "__main__":
    print("Setup Phase Detector Tests")
    print("=" * 60)

    detector = SetupPhaseDetector()

    # Test 1: Full setup signal
    print("\nTest 1: Full setup (UOA + IV + OI)")
    signal = detector.detect(
        symbol="PUMP",
        uoa_data={"volume_ratio": 5.0, "triggered": True},
        orats_data={"iv_rank": 75},
        snapshot_data={"call_oi_change_pct": 0.15},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 2: UOA only
    print("\nTest 2: UOA only")
    signal = detector.detect(
        symbol="PARTIAL",
        uoa_data={"volume_ratio": 4.0, "triggered": True},
        orats_data={"iv_rank": 30},
        snapshot_data={},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 3: No UOA (should not trigger)
    print("\nTest 3: IV + OI but no UOA")
    signal = detector.detect(
        symbol="NOPUMP",
        uoa_data={"volume_ratio": 1.5},
        orats_data={"iv_rank": 80},
        snapshot_data={"call_oi_change_pct": 0.20},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered} (expected: False - no UOA)")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 4: Strong UOA spike
    print("\nTest 4: Very strong UOA (10x)")
    signal = detector.detect(
        symbol="SPIKE",
        uoa_data={"volume_ratio": 10.0, "triggered": True},
        orats_data={"iv_rank": 90},
        snapshot_data={"call_oi_change_pct": 0.25},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    print(f"\nMetrics: {detector.get_metrics()}")
