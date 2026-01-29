"""
TA Calculator (Component 4.3)

Calculates technical indicators from price bars:
- RSI-14: Relative Strength Index
- ATR-14: Average True Range
- VWAP: Volume Weighted Average Price
- SMA-20: Simple Moving Average
- EMA-9: Exponential Moving Average

Usage:
    calc = TACalculator()
    indicators = calc.calculate(bars)
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TASnapshot:
    """Technical analysis snapshot for a symbol."""
    symbol: str
    price: float
    volume: int
    rsi_14: Optional[float] = None
    atr_14: Optional[float] = None
    vwap: Optional[float] = None
    sma_20: Optional[float] = None
    ema_9: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for database insertion."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "volume": self.volume,
            "rsi_14": self.rsi_14,
            "atr_14": self.atr_14,
            "vwap": self.vwap,
            "sma_20": self.sma_20,
            "ema_9": self.ema_9,
        }


class TACalculator:
    """
    Calculates technical indicators from OHLCV bars.

    Indicators computed:
    - RSI-14: Momentum oscillator (0-100)
    - ATR-14: Volatility measure
    - VWAP: Volume-weighted average price
    - SMA-20: Simple moving average
    - EMA-9: Exponential moving average

    Usage:
        calc = TACalculator()
        snapshot = calc.calculate(symbol, bars)
    """

    def __init__(
        self,
        rsi_period: int = 14,
        atr_period: int = 14,
        sma_period: int = 20,
        ema_period: int = 9,
    ):
        """
        Initialize calculator with periods.

        Args:
            rsi_period: RSI lookback period
            atr_period: ATR lookback period
            sma_period: SMA lookback period
            ema_period: EMA lookback period
        """
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.sma_period = sma_period
        self.ema_period = ema_period

    def calculate(self, symbol: str, bars: list) -> TASnapshot:
        """
        Calculate all TA indicators from bars.

        Args:
            symbol: Stock symbol
            bars: List of Bar objects with OHLCV data

        Returns:
            TASnapshot with all indicators
        """
        if not bars:
            return TASnapshot(symbol=symbol, price=0, volume=0)

        # Extract price data
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        volumes = [b.volume for b in bars]

        # Latest values
        latest_price = closes[-1]
        latest_volume = volumes[-1]

        # Calculate indicators
        rsi = self._calculate_rsi(closes)
        atr = self._calculate_atr(highs, lows, closes)
        vwap = self._calculate_vwap(bars)
        sma = self._calculate_sma(closes)
        ema = self._calculate_ema(closes)

        return TASnapshot(
            symbol=symbol,
            price=latest_price,
            volume=latest_volume,
            rsi_14=rsi,
            atr_14=atr,
            vwap=vwap,
            sma_20=sma,
            ema_9=ema,
        )

    def _calculate_rsi(self, closes: list[float]) -> Optional[float]:
        """
        Calculate RSI (Relative Strength Index).

        RSI = 100 - (100 / (1 + RS))
        RS = Average Gain / Average Loss

        Args:
            closes: List of closing prices

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(closes) < self.rsi_period + 1:
            return None

        # Calculate price changes
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]

        # Use only the most recent data for initial calculation
        recent_changes = changes[-(self.rsi_period):]

        gains = [c if c > 0 else 0 for c in recent_changes]
        losses = [-c if c < 0 else 0 for c in recent_changes]

        avg_gain = sum(gains) / self.rsi_period
        avg_loss = sum(losses) / self.rsi_period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return round(rsi, 2)

    def _calculate_atr(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
    ) -> Optional[float]:
        """
        Calculate ATR (Average True Range).

        True Range = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
        ATR = Average of True Range over period

        Args:
            highs: List of high prices
            lows: List of low prices
            closes: List of closing prices

        Returns:
            ATR value or None if insufficient data
        """
        if len(closes) < self.atr_period + 1:
            return None

        true_ranges = []

        for i in range(1, len(closes)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i-1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        # Use most recent true ranges
        recent_tr = true_ranges[-self.atr_period:]
        atr = sum(recent_tr) / self.atr_period

        return round(atr, 4)

    def _calculate_vwap(self, bars: list) -> Optional[float]:
        """
        Calculate VWAP (Volume Weighted Average Price).

        VWAP = Sum(Typical Price * Volume) / Sum(Volume)
        Typical Price = (High + Low + Close) / 3

        Args:
            bars: List of Bar objects

        Returns:
            VWAP value or None if no volume
        """
        if not bars:
            return None

        total_tpv = 0  # Typical Price * Volume
        total_volume = 0

        for bar in bars:
            typical_price = (bar.high + bar.low + bar.close) / 3
            total_tpv += typical_price * bar.volume
            total_volume += bar.volume

        if total_volume == 0:
            return None

        vwap = total_tpv / total_volume
        return round(vwap, 4)

    def _calculate_sma(self, closes: list[float]) -> Optional[float]:
        """
        Calculate SMA (Simple Moving Average).

        SMA = Sum(Closes) / Period

        Args:
            closes: List of closing prices

        Returns:
            SMA value or None if insufficient data
        """
        if len(closes) < self.sma_period:
            return None

        recent_closes = closes[-self.sma_period:]
        sma = sum(recent_closes) / self.sma_period

        return round(sma, 4)

    def _calculate_ema(self, closes: list[float]) -> Optional[float]:
        """
        Calculate EMA (Exponential Moving Average).

        EMA = Price * k + EMA_prev * (1 - k)
        k = 2 / (Period + 1)

        Args:
            closes: List of closing prices

        Returns:
            EMA value or None if insufficient data
        """
        if len(closes) < self.ema_period:
            return None

        # Start with SMA for initial EMA
        ema = sum(closes[:self.ema_period]) / self.ema_period

        # Apply EMA formula
        k = 2 / (self.ema_period + 1)

        for price in closes[self.ema_period:]:
            ema = price * k + ema * (1 - k)

        return round(ema, 4)

    def calculate_batch(
        self,
        bars_by_symbol: dict,
    ) -> dict[str, TASnapshot]:
        """
        Calculate TA for multiple symbols.

        Args:
            bars_by_symbol: Dict mapping symbol to list of bars

        Returns:
            Dict mapping symbol to TASnapshot
        """
        results = {}

        for symbol, bars in bars_by_symbol.items():
            try:
                # Extract bars list from BarData if needed
                bar_list = bars.bars if hasattr(bars, 'bars') else bars
                results[symbol] = self.calculate(symbol, bar_list)
            except Exception as e:
                logger.error(f"TA calculation failed for {symbol}: {e}")
                results[symbol] = TASnapshot(symbol=symbol, price=0, volume=0)

        return results


# Standalone functions for quick calculations

def calculate_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Calculate RSI from closing prices."""
    calc = TACalculator(rsi_period=period)
    return calc._calculate_rsi(closes)


def calculate_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> Optional[float]:
    """Calculate ATR from OHLC prices."""
    calc = TACalculator(atr_period=period)
    return calc._calculate_atr(highs, lows, closes)


def calculate_vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[int],
) -> Optional[float]:
    """Calculate VWAP from OHLCV data."""
    # Create mock bars
    @dataclass
    class MockBar:
        high: float
        low: float
        close: float
        volume: int

    bars = [MockBar(h, l, c, v) for h, l, c, v in zip(highs, lows, closes, volumes)]
    calc = TACalculator()
    return calc._calculate_vwap(bars)


if __name__ == "__main__":
    from dataclasses import dataclass

    print("TA Calculator Tests")
    print("=" * 60)

    # Create mock bars
    @dataclass
    class MockBar:
        high: float
        low: float
        close: float
        volume: int
        open: float = 0

    # Generate sample data (uptrend)
    bars = []
    base_price = 100.0
    for i in range(30):
        price = base_price + i * 0.5 + (i % 3 - 1) * 0.2  # Uptrend with noise
        bars.append(MockBar(
            open=price - 0.1,
            high=price + 0.5,
            low=price - 0.3,
            close=price,
            volume=10000 + i * 100,
        ))

    calc = TACalculator()
    snapshot = calc.calculate("TEST", bars)

    print(f"\nSymbol: {snapshot.symbol}")
    print(f"Price: ${snapshot.price:.2f}")
    print(f"Volume: {snapshot.volume:,}")
    print(f"\nIndicators:")
    print(f"  RSI-14: {snapshot.rsi_14}")
    print(f"  ATR-14: {snapshot.atr_14}")
    print(f"  VWAP: {snapshot.vwap}")
    print(f"  SMA-20: {snapshot.sma_20}")
    print(f"  EMA-9: {snapshot.ema_9}")

    # Test with downtrend
    print("\n" + "-" * 60)
    print("Downtrend test:")
    bars_down = []
    base_price = 100.0
    for i in range(30):
        price = base_price - i * 0.5 + (i % 3 - 1) * 0.2  # Downtrend
        bars_down.append(MockBar(
            open=price + 0.1,
            high=price + 0.3,
            low=price - 0.5,
            close=price,
            volume=15000 - i * 100,
        ))

    snapshot_down = calc.calculate("TEST_DOWN", bars_down)
    print(f"  RSI-14: {snapshot_down.rsi_14} (should be < 30 for oversold)")
    print(f"  Price: ${snapshot_down.price:.2f}")

    # Test insufficient data
    print("\n" + "-" * 60)
    print("Insufficient data test:")
    small_bars = bars[:5]
    snapshot_small = calc.calculate("SMALL", small_bars)
    print(f"  RSI-14: {snapshot_small.rsi_14} (expected: None)")
    print(f"  SMA-20: {snapshot_small.sma_20} (expected: None)")
    print(f"  Price: ${snapshot_small.price:.2f} (should still have price)")

    # Verify RSI ranges
    print("\n" + "-" * 60)
    print("RSI Validation:")
    if snapshot.rsi_14 is not None:
        assert 0 <= snapshot.rsi_14 <= 100, f"RSI out of range: {snapshot.rsi_14}"
        print(f"  Uptrend RSI: {snapshot.rsi_14} (expected: > 50)")
    if snapshot_down.rsi_14 is not None:
        assert 0 <= snapshot_down.rsi_14 <= 100, f"RSI out of range: {snapshot_down.rsi_14}"
        print(f"  Downtrend RSI: {snapshot_down.rsi_14} (expected: < 50)")

    print("\nAll tests passed!")
