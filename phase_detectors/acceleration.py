"""
Acceleration Phase Detector (Component 5.2)

Detects Phase 2 (Acceleration) of pump-and-dump pattern:
- Price breakout (price > 3x ATR move)
- Volume surge (sustained high volume)
- Positive GEX (dealer long gamma)
- RSI overbought (RSI > 70)
- VWAP deviation (price > VWAP)

Acceleration phase indicates the pump is in progress.

Usage:
    detector = AccelerationPhaseDetector()
    signal = detector.detect(symbol, ta_data, gex_data, volume_data)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AccelerationSignal:
    """Acceleration phase detection signal."""
    symbol: str
    timestamp: datetime
    phase: str = "ACCELERATION"
    score: float = 0.0  # 0-1 confidence score

    # Individual signal components
    price_breakout: bool = False
    atr_multiple: float = 0.0

    volume_surge: bool = False
    volume_ratio: float = 0.0

    gex_positive: bool = False
    net_gex: float = 0.0

    rsi_overbought: bool = False
    rsi: float = 0.0

    vwap_deviation: bool = False
    vwap_pct: float = 0.0

    # Contributing factors
    contributing_factors: list = None

    def __post_init__(self):
        if self.contributing_factors is None:
            self.contributing_factors = []

    @property
    def is_triggered(self) -> bool:
        """Returns True if acceleration phase criteria met."""
        return self.score >= 0.5

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "score": self.score,
            "price_breakout": self.price_breakout,
            "atr_multiple": self.atr_multiple,
            "volume_surge": self.volume_surge,
            "volume_ratio": self.volume_ratio,
            "gex_positive": self.gex_positive,
            "net_gex": self.net_gex,
            "rsi_overbought": self.rsi_overbought,
            "rsi": self.rsi,
            "vwap_deviation": self.vwap_deviation,
            "vwap_pct": self.vwap_pct,
            "contributing_factors": self.contributing_factors,
        }


class AccelerationPhaseDetector:
    """
    Detects Acceleration phase of P&D pattern.

    Acceleration phase characteristics:
    1. Price breaking out (large ATR move)
    2. Volume surging (sustained activity)
    3. Positive GEX (dealers hedging, adding fuel)
    4. RSI overbought (momentum confirmation)
    5. Trading above VWAP (bullish bias)

    Scoring weights:
    - Price breakout: 0.25
    - Volume surge: 0.20
    - Positive GEX: 0.20
    - RSI overbought: 0.20
    - VWAP deviation: 0.15

    Usage:
        detector = AccelerationPhaseDetector()
        signal = detector.detect(
            symbol="AAPL",
            ta_data={"rsi_14": 75, "atr_14": 2.5, "vwap": 150, "price": 160},
            gex_data={"net_gex": 1000000},
            volume_data={"volume_ratio": 3.0}
        )
    """

    def __init__(
        self,
        atr_breakout_threshold: float = 2.0,  # 2x ATR move
        volume_surge_threshold: float = 2.0,  # 2x normal volume
        rsi_overbought: float = 70.0,
        vwap_deviation_threshold: float = 0.02,  # 2% above VWAP
        breakout_weight: float = 0.25,
        volume_weight: float = 0.20,
        gex_weight: float = 0.20,
        rsi_weight: float = 0.20,
        vwap_weight: float = 0.15,
    ):
        """
        Initialize detector with thresholds.

        Args:
            atr_breakout_threshold: ATR multiple for breakout
            volume_surge_threshold: Volume ratio for surge
            rsi_overbought: RSI threshold for overbought
            vwap_deviation_threshold: Percentage above VWAP
            *_weight: Scoring weights for each signal
        """
        self.atr_breakout_threshold = atr_breakout_threshold
        self.volume_surge_threshold = volume_surge_threshold
        self.rsi_overbought = rsi_overbought
        self.vwap_deviation_threshold = vwap_deviation_threshold

        self.breakout_weight = breakout_weight
        self.volume_weight = volume_weight
        self.gex_weight = gex_weight
        self.rsi_weight = rsi_weight
        self.vwap_weight = vwap_weight

        # Metrics
        self._total_checks = 0
        self._triggers = 0

    def detect(
        self,
        symbol: str,
        ta_data: Optional[dict] = None,
        gex_data: Optional[dict] = None,
        volume_data: Optional[dict] = None,
        price_data: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> AccelerationSignal:
        """
        Detect acceleration phase for a symbol.

        Args:
            symbol: Stock symbol
            ta_data: TA indicators {rsi_14, atr_14, vwap, sma_20, ema_9, price}
            gex_data: GEX metrics {net_gex, net_dex, gamma_flip_level}
            volume_data: Volume data {volume_ratio, current_volume, avg_volume}
            price_data: Price data {price, open, high, low, prev_close}
            timestamp: Detection timestamp

        Returns:
            AccelerationSignal with detection results
        """
        self._total_checks += 1
        ts = timestamp or datetime.now()

        signal = AccelerationSignal(symbol=symbol, timestamp=ts)

        # Merge price_data into ta_data if provided
        if ta_data is None:
            ta_data = {}
        if price_data:
            ta_data.update(price_data)

        # Check each condition
        breakout_score = self._check_price_breakout(signal, ta_data)
        volume_score = self._check_volume_surge(signal, volume_data, ta_data)
        gex_score = self._check_gex_positive(signal, gex_data)
        rsi_score = self._check_rsi_overbought(signal, ta_data)
        vwap_score = self._check_vwap_deviation(signal, ta_data)

        # Calculate weighted score
        signal.score = (
            breakout_score * self.breakout_weight +
            volume_score * self.volume_weight +
            gex_score * self.gex_weight +
            rsi_score * self.rsi_weight +
            vwap_score * self.vwap_weight
        )
        signal.score = round(signal.score, 3)

        if signal.is_triggered:
            self._triggers += 1
            logger.info(
                f"ACCELERATION detected: {symbol} score={signal.score:.2f} "
                f"factors={signal.contributing_factors}"
            )

        return signal

    def _check_price_breakout(self, signal: AccelerationSignal, ta_data: Optional[dict]) -> float:
        """Check price breakout condition."""
        if not ta_data:
            return 0.0

        price = ta_data.get("price", ta_data.get("close", 0))
        atr = ta_data.get("atr_14", ta_data.get("atr", 0))
        prev_close = ta_data.get("prev_close", ta_data.get("sma_20", 0))

        if not (price and atr and prev_close):
            return 0.0

        # Calculate ATR multiple of the move
        price_move = abs(price - prev_close)
        if atr > 0:
            atr_multiple = price_move / atr
            signal.atr_multiple = round(atr_multiple, 2)

            if atr_multiple >= self.atr_breakout_threshold:
                signal.price_breakout = True
                signal.contributing_factors.append(f"Breakout {atr_multiple:.1f}x ATR")

                # Score based on ATR multiple
                score = min(1.0, atr_multiple / (self.atr_breakout_threshold * 2))
                return score

        return 0.0

    def _check_volume_surge(
        self,
        signal: AccelerationSignal,
        volume_data: Optional[dict],
        ta_data: Optional[dict],
    ) -> float:
        """Check volume surge condition."""
        # Try volume_data first
        if volume_data:
            volume_ratio = volume_data.get("volume_ratio", 0)
            if volume_ratio > 0:
                signal.volume_ratio = volume_ratio

                if volume_ratio >= self.volume_surge_threshold:
                    signal.volume_surge = True
                    signal.contributing_factors.append(f"Volume {volume_ratio:.1f}x")

                    score = min(1.0, volume_ratio / (self.volume_surge_threshold * 2))
                    return score

        # Fall back to ta_data volume if available
        if ta_data:
            volume = ta_data.get("volume", 0)
            avg_volume = ta_data.get("avg_volume", 0)

            if volume > 0 and avg_volume > 0:
                volume_ratio = volume / avg_volume
                signal.volume_ratio = round(volume_ratio, 2)

                if volume_ratio >= self.volume_surge_threshold:
                    signal.volume_surge = True
                    signal.contributing_factors.append(f"Volume {volume_ratio:.1f}x")

                    score = min(1.0, volume_ratio / (self.volume_surge_threshold * 2))
                    return score

        return 0.0

    def _check_gex_positive(self, signal: AccelerationSignal, gex_data: Optional[dict]) -> float:
        """Check positive GEX condition."""
        if not gex_data:
            return 0.0

        net_gex = gex_data.get("net_gex", 0)
        signal.net_gex = net_gex

        if net_gex > 0:
            signal.gex_positive = True
            signal.contributing_factors.append(f"GEX +${net_gex/1e6:.1f}M")

            # Score based on GEX magnitude (normalized)
            # Assume significant GEX is $10M+
            score = min(1.0, abs(net_gex) / 10_000_000)
            return score

        return 0.0

    def _check_rsi_overbought(self, signal: AccelerationSignal, ta_data: Optional[dict]) -> float:
        """Check RSI overbought condition."""
        if not ta_data:
            return 0.0

        rsi = ta_data.get("rsi_14", ta_data.get("rsi", 0))
        if rsi:
            signal.rsi = rsi

            if rsi >= self.rsi_overbought:
                signal.rsi_overbought = True
                signal.contributing_factors.append(f"RSI {rsi:.0f}")

                # Score: 70 = 0.7, 80 = 0.9, 90+ = 1.0
                score = min(1.0, rsi / 100)
                return score

            # Partial score for high RSI
            if rsi >= 60:
                return (rsi - 50) / 50

        return 0.0

    def _check_vwap_deviation(self, signal: AccelerationSignal, ta_data: Optional[dict]) -> float:
        """Check VWAP deviation condition."""
        if not ta_data:
            return 0.0

        price = ta_data.get("price", ta_data.get("close", 0))
        vwap = ta_data.get("vwap", 0)

        if price and vwap:
            vwap_pct = (price - vwap) / vwap
            signal.vwap_pct = round(vwap_pct, 4)

            if vwap_pct >= self.vwap_deviation_threshold:
                signal.vwap_deviation = True
                signal.contributing_factors.append(f"VWAP +{vwap_pct*100:.1f}%")

                # Score based on deviation
                score = min(1.0, vwap_pct / (self.vwap_deviation_threshold * 3))
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
    print("Acceleration Phase Detector Tests")
    print("=" * 60)

    detector = AccelerationPhaseDetector()

    # Test 1: Full acceleration signal
    print("\nTest 1: Full acceleration (all signals)")
    signal = detector.detect(
        symbol="ROCKET",
        ta_data={
            "price": 105,
            "prev_close": 100,
            "atr_14": 2.0,
            "rsi_14": 78,
            "vwap": 102,
        },
        gex_data={"net_gex": 5_000_000},
        volume_data={"volume_ratio": 3.5},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 2: Price breakout only
    print("\nTest 2: Price breakout only")
    signal = detector.detect(
        symbol="BREAK",
        ta_data={
            "price": 110,
            "prev_close": 100,
            "atr_14": 2.0,
            "rsi_14": 55,
            "vwap": 108,
        },
        gex_data={"net_gex": -1_000_000},
        volume_data={"volume_ratio": 1.2},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 3: RSI + Volume but no breakout
    print("\nTest 3: RSI + Volume (no breakout)")
    signal = detector.detect(
        symbol="MOMENTUM",
        ta_data={
            "price": 101,
            "prev_close": 100,
            "atr_14": 2.0,
            "rsi_14": 82,
            "vwap": 100,
        },
        gex_data={"net_gex": 2_000_000},
        volume_data={"volume_ratio": 4.0},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    # Test 4: Extreme acceleration
    print("\nTest 4: Extreme acceleration")
    signal = detector.detect(
        symbol="MOON",
        ta_data={
            "price": 120,
            "prev_close": 100,
            "atr_14": 3.0,
            "rsi_14": 92,
            "vwap": 105,
        },
        gex_data={"net_gex": 15_000_000},
        volume_data={"volume_ratio": 8.0},
    )
    print(f"  Score: {signal.score}")
    print(f"  Triggered: {signal.is_triggered}")
    print(f"  Factors: {signal.contributing_factors}")

    print(f"\nMetrics: {detector.get_metrics()}")
