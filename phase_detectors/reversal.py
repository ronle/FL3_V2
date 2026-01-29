"""
Reversal Phase Detector (Component 5.3)

Detects Phase 3 (Reversal) of pump-and-dump pattern:
- Vanna flip (net_vex sign change)
- Negative GEX (dealer short gamma)
- RSI divergence (price up, RSI down)
- Volume climax (spike then drop)
- IV crush (iv_rank dropping)

Reversal phase indicates the dump is imminent or in progress.

Usage:
    detector = ReversalPhaseDetector()
    signal = detector.detect(symbol, ta_data, gex_data, orats_data, history)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ReversalSignal:
    """Reversal phase detection signal."""
    symbol: str
    timestamp: datetime
    phase: str = "REVERSAL"
    score: float = 0.0  # 0-1 confidence score

    # Individual signal components
    vanna_flip: bool = False
    prev_vex: float = 0.0
    current_vex: float = 0.0

    gex_negative: bool = False
    net_gex: float = 0.0

    rsi_divergence: bool = False
    price_change_pct: float = 0.0
    rsi_change: float = 0.0

    volume_climax: bool = False
    volume_drop_pct: float = 0.0

    iv_crush: bool = False
    iv_rank_change: float = 0.0

    # Contributing factors
    contributing_factors: list = None

    def __post_init__(self):
        if self.contributing_factors is None:
            self.contributing_factors = []

    @property
    def is_triggered(self) -> bool:
        """Returns True if reversal phase criteria met."""
        return self.score >= 0.5

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "score": self.score,
            "vanna_flip": self.vanna_flip,
            "gex_negative": self.gex_negative,
            "net_gex": self.net_gex,
            "rsi_divergence": self.rsi_divergence,
            "volume_climax": self.volume_climax,
            "iv_crush": self.iv_crush,
            "contributing_factors": self.contributing_factors,
        }


class ReversalPhaseDetector:
    """
    Detects Reversal phase of P&D pattern.

    Reversal phase characteristics:
    1. Vanna flip (vega-gamma exposure sign change)
    2. Negative GEX (dealers short gamma, will amplify move)
    3. RSI divergence (bearish divergence)
    4. Volume climax (peak volume followed by drop)
    5. IV crush (implied volatility collapsing)

    These signals indicate the "smart money" is exiting
    and the dump phase is beginning.

    Scoring weights:
    - Vanna flip: 0.25
    - Negative GEX: 0.25
    - RSI divergence: 0.20
    - Volume climax: 0.15
    - IV crush: 0.15

    Usage:
        detector = ReversalPhaseDetector()
        signal = detector.detect(
            symbol="AAPL",
            ta_data={"rsi_14": 65, "prev_rsi": 78, "price": 110, "prev_price": 105},
            gex_data={"net_gex": -5000000, "net_vex": -100000, "prev_vex": 50000},
            orats_data={"iv_rank": 40, "prev_iv_rank": 75},
            volume_data={"volume_ratio": 0.5, "prev_volume_ratio": 5.0}
        )
    """

    def __init__(
        self,
        rsi_divergence_threshold: float = 5.0,  # RSI drop while price rises
        volume_drop_threshold: float = 0.5,  # 50% volume drop from peak
        iv_crush_threshold: float = 15.0,  # 15 point IV rank drop
        vanna_weight: float = 0.25,
        gex_weight: float = 0.25,
        rsi_weight: float = 0.20,
        volume_weight: float = 0.15,
        iv_weight: float = 0.15,
    ):
        """
        Initialize detector with thresholds.

        Args:
            rsi_divergence_threshold: Min RSI drop for divergence
            volume_drop_threshold: Volume drop ratio from peak
            iv_crush_threshold: IV rank point drop threshold
            *_weight: Scoring weights for each signal
        """
        self.rsi_divergence_threshold = rsi_divergence_threshold
        self.volume_drop_threshold = volume_drop_threshold
        self.iv_crush_threshold = iv_crush_threshold

        self.vanna_weight = vanna_weight
        self.gex_weight = gex_weight
        self.rsi_weight = rsi_weight
        self.volume_weight = volume_weight
        self.iv_weight = iv_weight

        # Metrics
        self._total_checks = 0
        self._triggers = 0

    def detect(
        self,
        symbol: str,
        ta_data: Optional[dict] = None,
        gex_data: Optional[dict] = None,
        orats_data: Optional[dict] = None,
        volume_data: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> ReversalSignal:
        """
        Detect reversal phase for a symbol.

        Args:
            symbol: Stock symbol
            ta_data: TA indicators with current and previous values
            gex_data: GEX metrics with vanna data
            orats_data: ORATS data with IV rank changes
            volume_data: Volume data with historical comparison
            timestamp: Detection timestamp

        Returns:
            ReversalSignal with detection results
        """
        self._total_checks += 1
        ts = timestamp or datetime.now()

        signal = ReversalSignal(symbol=symbol, timestamp=ts)

        # Check each reversal condition
        vanna_score = self._check_vanna_flip(signal, gex_data)
        gex_score = self._check_gex_negative(signal, gex_data)
        rsi_score = self._check_rsi_divergence(signal, ta_data)
        volume_score = self._check_volume_climax(signal, volume_data)
        iv_score = self._check_iv_crush(signal, orats_data)

        # Calculate weighted score
        signal.score = (
            vanna_score * self.vanna_weight +
            gex_score * self.gex_weight +
            rsi_score * self.rsi_weight +
            volume_score * self.volume_weight +
            iv_score * self.iv_weight
        )
        signal.score = round(signal.score, 3)

        if signal.is_triggered:
            self._triggers += 1
            logger.warning(
                f"REVERSAL detected: {symbol} score={signal.score:.2f} "
                f"factors={signal.contributing_factors}"
            )

        return signal

    def _check_vanna_flip(self, signal: ReversalSignal, gex_data: Optional[dict]) -> float:
        """Check Vanna/VEX flip condition."""
        if not gex_data:
            return 0.0

        current_vex = gex_data.get("net_vex", 0)
        prev_vex = gex_data.get("prev_vex", gex_data.get("prev_net_vex", 0))

        signal.current_vex = current_vex
        signal.prev_vex = prev_vex

        # Vanna flip: was positive, now negative
        if prev_vex > 0 and current_vex < 0:
            signal.vanna_flip = True
            signal.contributing_factors.append("Vanna flip (+ to -)")
            return 1.0

        # Vanna turning more negative
        if current_vex < 0 and (prev_vex == 0 or current_vex < prev_vex):
            signal.contributing_factors.append("Vanna increasingly negative")
            return 0.6

        return 0.0

    def _check_gex_negative(self, signal: ReversalSignal, gex_data: Optional[dict]) -> float:
        """Check negative GEX condition."""
        if not gex_data:
            return 0.0

        net_gex = gex_data.get("net_gex", 0)
        signal.net_gex = net_gex

        if net_gex < 0:
            signal.gex_negative = True
            signal.contributing_factors.append(f"GEX -${abs(net_gex)/1e6:.1f}M")

            # Score based on magnitude
            score = min(1.0, abs(net_gex) / 10_000_000)
            return score

        return 0.0

    def _check_rsi_divergence(self, signal: ReversalSignal, ta_data: Optional[dict]) -> float:
        """Check RSI bearish divergence condition."""
        if not ta_data:
            return 0.0

        rsi = ta_data.get("rsi_14", ta_data.get("rsi", 0))
        prev_rsi = ta_data.get("prev_rsi", ta_data.get("prev_rsi_14", 0))
        price = ta_data.get("price", ta_data.get("close", 0))
        prev_price = ta_data.get("prev_price", ta_data.get("prev_close", 0))

        if not (rsi and prev_rsi and price and prev_price):
            return 0.0

        price_change_pct = (price - prev_price) / prev_price * 100
        rsi_change = rsi - prev_rsi

        signal.price_change_pct = round(price_change_pct, 2)
        signal.rsi_change = round(rsi_change, 2)

        # Bearish divergence: price up but RSI down
        if price_change_pct > 0 and rsi_change < -self.rsi_divergence_threshold:
            signal.rsi_divergence = True
            signal.contributing_factors.append(
                f"RSI divergence (price +{price_change_pct:.1f}%, RSI {rsi_change:.0f})"
            )

            # Score based on divergence magnitude
            score = min(1.0, abs(rsi_change) / 20)
            return score

        # RSI dropping from overbought
        if prev_rsi >= 70 and rsi < prev_rsi - 10:
            signal.contributing_factors.append(f"RSI dropping from overbought ({prev_rsi:.0f} -> {rsi:.0f})")
            return 0.5

        return 0.0

    def _check_volume_climax(self, signal: ReversalSignal, volume_data: Optional[dict]) -> float:
        """Check volume climax condition (spike then drop)."""
        if not volume_data:
            return 0.0

        current_ratio = volume_data.get("volume_ratio", 0)
        peak_ratio = volume_data.get("peak_volume_ratio", volume_data.get("prev_volume_ratio", 0))

        if not (current_ratio and peak_ratio):
            return 0.0

        # Volume climax: peak was high, now dropping
        if peak_ratio >= 3.0:  # Had significant volume
            volume_drop = 1 - (current_ratio / peak_ratio) if peak_ratio > 0 else 0
            signal.volume_drop_pct = round(volume_drop, 2)

            if volume_drop >= self.volume_drop_threshold:
                signal.volume_climax = True
                signal.contributing_factors.append(
                    f"Volume climax ({peak_ratio:.1f}x -> {current_ratio:.1f}x)"
                )

                score = min(1.0, volume_drop / 0.7)
                return score

        return 0.0

    def _check_iv_crush(self, signal: ReversalSignal, orats_data: Optional[dict]) -> float:
        """Check IV crush condition."""
        if not orats_data:
            return 0.0

        iv_rank = orats_data.get("iv_rank", orats_data.get("ivRank", 0))
        prev_iv_rank = orats_data.get("prev_iv_rank", 0)

        if not (iv_rank is not None and prev_iv_rank):
            return 0.0

        iv_drop = prev_iv_rank - iv_rank
        signal.iv_rank_change = round(iv_drop, 1)

        if iv_drop >= self.iv_crush_threshold:
            signal.iv_crush = True
            signal.contributing_factors.append(f"IV crush ({prev_iv_rank:.0f} -> {iv_rank:.0f})")

            # Score based on magnitude of crush
            score = min(1.0, iv_drop / 30)
            return score

        return 0.0

    def get_metrics(self) -> dict:
        """Get detector metrics."""
        return {
            "total_checks": self._total_checks,
            "triggers": self._triggers,
            "trigger_rate": self._triggers / self._total_checks if self._total_checks > 0 else 0,
        }


if __name__ == "__main__":
    print("Reversal Phase Detector Tests")
    print("=" * 60)

    detector = ReversalPhaseDetector()

    # Test 1: Full reversal signal
    print("\nTest 1: Full reversal (all signals)")
    signal = detector.detect(
        symbol="DUMP",
        ta_data={
            "rsi_14": 55,
            "prev_rsi": 78,
            "price": 102,
            "prev_price": 100,
        },
        gex_data={
            "net_gex": -8_000_000,
            "net_vex": -200_000,
            "prev_vex": 100_000,
        },
        orats_data={
            "iv_rank": 35,
            "prev_iv_rank": 75,
        },
        volume_data={
            "volume_ratio": 1.5,
            "peak_volume_ratio": 6.0,
        },
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 2: GEX negative only
    print("\nTest 2: GEX negative only")
    signal = detector.detect(
        symbol="PARTIAL",
        ta_data={"rsi_14": 65, "prev_rsi": 68},
        gex_data={"net_gex": -5_000_000},
        orats_data={"iv_rank": 50, "prev_iv_rank": 55},
        volume_data={"volume_ratio": 2.0},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 3: Vanna flip + IV crush
    print("\nTest 3: Vanna flip + IV crush")
    signal = detector.detect(
        symbol="FLIP",
        gex_data={
            "net_gex": -2_000_000,
            "net_vex": -150_000,
            "prev_vex": 200_000,
        },
        orats_data={
            "iv_rank": 30,
            "prev_iv_rank": 60,
        },
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 4: No reversal signals
    print("\nTest 4: No reversal (still bullish)")
    signal = detector.detect(
        symbol="BULL",
        ta_data={"rsi_14": 72, "prev_rsi": 68, "price": 110, "prev_price": 105},
        gex_data={"net_gex": 5_000_000, "net_vex": 100_000},
        orats_data={"iv_rank": 65, "prev_iv_rank": 60},
        volume_data={"volume_ratio": 3.0},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered} (expected: False)")
    print(f"  Factors: {signal.contributing_factors}")

    print(f"\nMetrics: {detector.get_metrics()}")
