"""
Consolidation Breakout Detector (Gap & Go Entry)

Cameron's Gap & Go entry after the initial spike:
  1. Stock gaps up >= 4% pre-market
  2. First 5-15 minutes: initial spike then pullback
  3. Consolidation forms: 3-8 candles, tight range near highs
  4. Entry on break above consolidation high
  5. Stop below consolidation low

Interface: detect_consolidation_breakout(symbol, df, interval, ...) -> ConsolidationBreakout | None
Input df columns: ts, open, high, low, close, volume (sorted ascending)
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

MIN_CANDLES = 6  # at least 2 for spike + 3 for consolidation + 1 buffer


@dataclass
class ConsolidationBreakout:
    symbol: str
    pattern_type: str           # "consolidation_breakout"
    interval: str
    date: str
    # Day context
    gap_pct: float              # pre-market gap (passed in by caller)
    day_high: float             # highest price so far today
    # Consolidation zone
    consol_high: float          # breakout level
    consol_low: float           # stop level
    consol_candles: int
    consol_range_pct: float     # range as % of price
    near_highs: bool            # consolidating in top 30% of day's range
    # Quality
    volume_drying_up: bool      # volume decreasing during consolidation
    tight_range: bool           # range < 2% of price
    volume_confirmed: bool      # spike volume above average
    pattern_strength: str       # "strong", "moderate", "weak"
    # Levels
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float


def detect_consolidation_breakout(
    symbol: str,
    df: pd.DataFrame,
    interval: str,
    gap_pct: float = 0.0,
    min_consol_candles: int = 3,
    max_consol_candles: int = 8,
    max_range_pct: float = 0.04,
) -> Optional[ConsolidationBreakout]:
    """
    Detect consolidation near highs after a gap-up or initial spike.

    Must be called after market open, once enough candles have formed.
    Typically useful starting 9:45 AM ET (15 min after open on 5-min chart).
    """
    if len(df) < MIN_CANDLES:
        return None

    window = df.iloc[-25:].copy() if len(df) > 25 else df.copy()
    n = len(window)

    # Overall day high/low for context
    day_high = float(window["high"].max())
    day_low = float(window["low"].min())
    day_range = day_high - day_low

    if day_high <= 0 or day_range <= 0:
        return None

    # Try consolidation zones of varying length at the end
    for consol_len in range(min_consol_candles, min(max_consol_candles + 1, n - 1)):
        consol_slice = window.iloc[n - consol_len:]
        c_high = float(consol_slice["high"].max())
        c_low = float(consol_slice["low"].min())
        c_range = c_high - c_low

        if c_high <= 0:
            continue

        c_range_pct = c_range / c_high

        # Consolidation must be tight
        if c_range_pct > max_range_pct:
            continue

        # Must be near day's highs (top 30% of day range)
        c_midpoint = (c_high + c_low) / 2
        near_highs = (c_midpoint - day_low) / day_range >= 0.70

        if not near_highs:
            continue

        # There must be a preceding spike (candles before consolidation should show upward move)
        pre_consol_idx = n - consol_len - 1
        if pre_consol_idx < 0:
            continue

        # Check that price ran up before this consolidation
        pre_price = float(window.iloc[max(0, pre_consol_idx - 3)]["low"])
        spike_pct = (c_high - pre_price) / pre_price if pre_price > 0 else 0

        if spike_pct < 0.02:  # need at least 2% run-up before consolidation
            continue

        # Volume analysis: should be drying up during consolidation
        consol_volumes = consol_slice["volume"].values
        vol_drying = True
        if len(consol_volumes) >= 3:
            first_half = float(consol_volumes[: len(consol_volumes) // 2].mean())
            second_half = float(consol_volumes[len(consol_volumes) // 2 :].mean())
            vol_drying = second_half <= first_half * 1.1  # allow 10% tolerance

        # Volume confirmation: pre-consolidation bars had above-avg volume
        avg_vol = float(window["volume"].mean())
        pre_consol_vol = float(window.iloc[max(0, pre_consol_idx - 2) : pre_consol_idx + 1]["volume"].mean())
        vol_confirmed = pre_consol_vol > avg_vol

        tight = c_range_pct < 0.02

        # Strength
        conditions = sum([near_highs, vol_drying, tight, vol_confirmed])
        if conditions >= 4:
            strength = "strong"
        elif conditions >= 3:
            strength = "moderate"
        else:
            strength = "weak"

        # Levels
        entry = c_high + 0.01
        stop = c_low - 0.01
        risk = entry - stop
        target_1 = entry + risk
        target_2 = entry + 2 * risk

        last_candle = consol_slice.iloc[-1]

        return ConsolidationBreakout(
            symbol=symbol,
            pattern_type="consolidation_breakout",
            interval=interval,
            date=str(last_candle["ts"]) if "ts" in last_candle.index else str(last_candle.name),
            gap_pct=gap_pct,
            day_high=day_high,
            consol_high=c_high,
            consol_low=c_low,
            consol_candles=consol_len,
            consol_range_pct=round(c_range_pct, 4),
            near_highs=near_highs,
            volume_drying_up=vol_drying,
            tight_range=tight,
            volume_confirmed=vol_confirmed,
            pattern_strength=strength,
            entry_price=round(entry, 2),
            stop_loss=round(stop, 2),
            target_1=round(target_1, 2),
            target_2=round(target_2, 2),
        )

    return None
