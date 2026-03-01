"""
Bull Flag / Consolidation Pattern Detector

Cameron's primary intraday pattern:
  1. Flagpole: Sharp up-move (>= min_pole_gain_pct in <= max_pole_candles)
  2. Flag: 3-7 candles of consolidation (range <= max_flag_retrace of pole)
  3. Breakout: Price ready to break above flag high (tracked by caller)

Interface: detect_bull_flag(symbol, df, interval) -> BullFlagPattern | None
Input df columns: ts, open, high, low, close, volume (sorted ascending)
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

MIN_CANDLES = 8  # minimum: 1 pole start + up to 5 pole + 3 flag


@dataclass
class BullFlagPattern:
    symbol: str
    pattern_type: str           # "bull_flag"
    interval: str
    date: str                   # ts of last flag candle (detection point)
    # Flagpole
    pole_start_price: float
    pole_end_price: float
    pole_gain_pct: float
    pole_candles: int
    pole_volume: int            # total volume during pole
    # Flag (consolidation)
    flag_high: float            # breakout level
    flag_low: float             # stop reference
    flag_candles: int
    flag_range_pct: float       # flag range as % of price
    flag_retrace_pct: float     # how much of the pole was retraced
    # Quality
    volume_declining: bool      # volume decreases during flag
    higher_lows: bool           # flag has ascending lows
    volume_confirmed: bool      # pole volume > avg volume
    pattern_strength: str       # "strong", "moderate", "weak"
    # Levels
    entry_price: float          # flag_high + 0.01
    stop_loss: float            # flag_low - buffer
    target_1: float             # entry + risk (1:1)
    target_2: float             # entry + 2*risk (2:1)


def detect_bull_flag(
    symbol: str,
    df: pd.DataFrame,
    interval: str,
    min_pole_gain_pct: float = 0.03,
    max_pole_candles: int = 5,
    min_flag_candles: int = 3,
    max_flag_candles: int = 7,
    max_flag_retrace_pct: float = 0.50,
) -> Optional[BullFlagPattern]:
    """
    Detect bull flag on the most recent candles.

    Scans backward from the end of df to find:
    1. A consolidation zone (the flag) in the most recent candles
    2. A sharp up-move (the pole) immediately preceding it

    Returns BullFlagPattern if found, None otherwise.
    """
    if len(df) < MIN_CANDLES:
        return None

    # Work with the last 20 candles max
    window = df.iloc[-20:].copy() if len(df) > 20 else df.copy()
    n = len(window)

    # Step 1: Identify potential flag (consolidation at the end)
    # Look for a cluster of candles with compressed range
    best_flag = None
    for flag_len in range(min_flag_candles, min(max_flag_candles + 1, n - 2)):
        flag_slice = window.iloc[n - flag_len:]
        flag_high = float(flag_slice["high"].max())
        flag_low = float(flag_slice["low"].min())
        flag_range = flag_high - flag_low

        if flag_high <= 0:
            continue

        flag_range_pct = flag_range / flag_high

        # Flag should be relatively tight (< 5% range for small caps)
        if flag_range_pct > 0.08:
            continue

        # Check if volume is declining during flag
        flag_volumes = flag_slice["volume"].values
        vol_declining = True
        if len(flag_volumes) >= 3:
            first_half_avg = float(flag_volumes[: len(flag_volumes) // 2].mean())
            second_half_avg = float(flag_volumes[len(flag_volumes) // 2 :].mean())
            vol_declining = second_half_avg < first_half_avg

        # Check for higher lows in flag
        flag_lows = flag_slice["low"].values
        h_lows = all(flag_lows[i] >= flag_lows[i - 1] - 0.005 for i in range(1, len(flag_lows)))

        best_flag = {
            "flag_len": flag_len,
            "flag_high": flag_high,
            "flag_low": flag_low,
            "flag_range_pct": flag_range_pct,
            "vol_declining": vol_declining,
            "higher_lows": h_lows,
        }
        break  # take shortest valid flag

    if best_flag is None:
        return None

    flag_len = best_flag["flag_len"]

    # Step 2: Look for flagpole immediately before the flag
    pole_end_idx = n - flag_len - 1
    if pole_end_idx < 1:
        return None

    pole_end_candle = window.iloc[pole_end_idx]
    pole_end_price = float(pole_end_candle["high"])

    # Scan backward for pole start
    best_pole = None
    for pole_len in range(1, min(max_pole_candles + 1, pole_end_idx + 1)):
        pole_start_idx = pole_end_idx - pole_len
        if pole_start_idx < 0:
            break
        pole_start_candle = window.iloc[pole_start_idx]
        pole_start_price = float(pole_start_candle["low"])

        if pole_start_price <= 0:
            continue

        pole_gain_pct = (pole_end_price - pole_start_price) / pole_start_price

        if pole_gain_pct >= min_pole_gain_pct:
            pole_slice = window.iloc[pole_start_idx : pole_end_idx + 1]
            pole_volume = int(pole_slice["volume"].sum())
            best_pole = {
                "pole_start_price": pole_start_price,
                "pole_end_price": pole_end_price,
                "pole_gain_pct": pole_gain_pct,
                "pole_candles": pole_len,
                "pole_volume": pole_volume,
            }
            break  # take shortest valid pole

    if best_pole is None:
        return None

    # Step 3: Check flag retrace isn't too deep
    pole_range = best_pole["pole_end_price"] - best_pole["pole_start_price"]
    flag_retrace = best_pole["pole_end_price"] - best_flag["flag_low"]
    retrace_pct = flag_retrace / pole_range if pole_range > 0 else 1.0

    if retrace_pct > max_flag_retrace_pct:
        return None

    # Step 4: Strength scoring
    conditions_met = sum([
        best_flag["vol_declining"],
        best_flag["higher_lows"],
        best_pole["pole_gain_pct"] >= 0.05,  # strong pole (5%+)
    ])
    if conditions_met >= 3:
        strength = "strong"
    elif conditions_met >= 2:
        strength = "moderate"
    else:
        strength = "weak"

    # Volume confirmation: pole volume above average
    avg_vol = float(window["volume"].mean())
    vol_confirmed = best_pole["pole_volume"] / max(best_pole["pole_candles"], 1) > avg_vol

    # Compute entry/stop/targets
    entry = best_flag["flag_high"] + 0.01
    stop = best_flag["flag_low"] - 0.01
    risk = entry - stop
    target_1 = entry + risk
    target_2 = entry + 2 * risk

    last_candle = window.iloc[-1]

    return BullFlagPattern(
        symbol=symbol,
        pattern_type="bull_flag",
        interval=interval,
        date=str(last_candle["ts"]) if "ts" in last_candle.index else str(last_candle.name),
        pole_start_price=best_pole["pole_start_price"],
        pole_end_price=best_pole["pole_end_price"],
        pole_gain_pct=round(best_pole["pole_gain_pct"], 4),
        pole_candles=best_pole["pole_candles"],
        pole_volume=best_pole["pole_volume"],
        flag_high=best_flag["flag_high"],
        flag_low=best_flag["flag_low"],
        flag_candles=flag_len,
        flag_range_pct=round(best_flag["flag_range_pct"], 4),
        flag_retrace_pct=round(retrace_pct, 4),
        volume_declining=best_flag["vol_declining"],
        higher_lows=best_flag["higher_lows"],
        volume_confirmed=vol_confirmed,
        pattern_strength=strength,
        entry_price=round(entry, 2),
        stop_loss=round(stop, 2),
        target_1=round(target_1, 2),
        target_2=round(target_2, 2),
    )
