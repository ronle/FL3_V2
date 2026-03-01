"""
VWAP Reclaim Detector

Humbled Trader's A+ setup + Aziz's VWAP strategies:
  1. Stock trades below VWAP after initial sell-off
  2. Price reclaims VWAP from below on increasing volume
  3. Entry on the reclaim candle close above VWAP
  4. Stop below swing low pre-reclaim (or VWAP itself)

VWAP = cumulative(price * volume) / cumulative(volume) from market open.

Interface: detect_vwap_reclaim(symbol, df, interval, ...) -> VWAPReclaim | None
Input df columns: ts, open, high, low, close, volume (sorted ascending)
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd

MIN_CANDLES = 5  # need some history below VWAP + the reclaim candle


@dataclass
class VWAPReclaim:
    symbol: str
    pattern_type: str           # "vwap_reclaim"
    interval: str
    date: str
    # VWAP data
    vwap: float                 # VWAP at detection time
    reclaim_price: float        # close of reclaim candle
    vwap_distance_pct: float    # how far above VWAP the reclaim closed
    # Context
    candles_below_vwap: int     # how many candles were below before reclaim
    low_before_reclaim: float   # swing low (stop reference)
    # Quality
    volume_on_reclaim: str      # "increasing" or "declining"
    clean_reclaim: bool         # closed decisively above (not just wick)
    volume_confirmed: bool      # reclaim volume above average
    pattern_strength: str       # "strong", "moderate", "weak"
    # Levels
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute running VWAP from 1-min or aggregated bars.
    VWAP = cumsum(typical_price * volume) / cumsum(volume)
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical * df["volume"]).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, float("nan"))
    return vwap


def detect_vwap_reclaim(
    symbol: str,
    df: pd.DataFrame,
    interval: str,
    vwap_series: Optional[pd.Series] = None,
    min_candles_below: int = 3,
) -> Optional[VWAPReclaim]:
    """
    Detect a VWAP reclaim pattern.

    If vwap_series is not provided, computes VWAP from the input df
    (assumes df contains all bars from market open).
    """
    if len(df) < MIN_CANDLES:
        return None

    # Compute VWAP if not provided
    if vwap_series is None:
        vwap_series = compute_vwap(df)

    window = df.iloc[-20:].copy() if len(df) > 20 else df.copy()
    vwap_window = vwap_series.iloc[-20:] if len(vwap_series) > 20 else vwap_series
    n = len(window)

    if n < MIN_CANDLES:
        return None

    # Current candle must close ABOVE VWAP
    last = window.iloc[-1]
    current_vwap = float(vwap_window.iloc[-1])

    if pd.isna(current_vwap) or current_vwap <= 0:
        return None

    last_close = float(last["close"])
    last_open = float(last["open"])

    if last_close <= current_vwap:
        return None  # not reclaiming

    # Previous candle(s) must have been BELOW VWAP
    candles_below = 0
    swing_low = float("inf")

    for i in range(n - 2, max(n - 15, -1), -1):
        if i < 0:
            break
        candle = window.iloc[i]
        candle_vwap = float(vwap_window.iloc[i])
        if pd.isna(candle_vwap):
            break

        if float(candle["close"]) < candle_vwap:
            candles_below += 1
            swing_low = min(swing_low, float(candle["low"]))
        else:
            break  # stop once we find a candle that was above VWAP

    if candles_below < min_candles_below:
        return None

    if swing_low == float("inf"):
        return None

    # Quality checks
    # Volume on reclaim candle vs preceding candle
    last_vol = float(last["volume"])
    prev_vol = float(window.iloc[-2]["volume"])
    vol_increasing = last_vol > prev_vol
    vol_label = "increasing" if vol_increasing else "declining"

    # Clean reclaim: body crosses VWAP (not just a wick)
    body_low = min(last_close, last_open)
    clean = body_low >= current_vwap * 0.998  # allow 0.2% tolerance

    # Volume confirmation: reclaim volume above average
    avg_vol = float(window["volume"].mean())
    vol_confirmed = last_vol > avg_vol

    # VWAP distance
    vwap_dist_pct = (last_close - current_vwap) / current_vwap

    # Strength
    conditions = sum([vol_increasing, clean, vol_confirmed, candles_below >= 5])
    if conditions >= 4:
        strength = "strong"
    elif conditions >= 3:
        strength = "moderate"
    else:
        strength = "weak"

    # Levels
    entry = last_close
    stop = swing_low - 0.01
    risk = entry - stop
    if risk <= 0:
        return None
    target_1 = entry + risk
    target_2 = entry + 2 * risk

    return VWAPReclaim(
        symbol=symbol,
        pattern_type="vwap_reclaim",
        interval=interval,
        date=str(last["ts"]) if "ts" in last.index else str(last.name),
        vwap=round(current_vwap, 4),
        reclaim_price=round(last_close, 4),
        vwap_distance_pct=round(vwap_dist_pct, 4),
        candles_below_vwap=candles_below,
        low_before_reclaim=round(swing_low, 4),
        volume_on_reclaim=vol_label,
        clean_reclaim=clean,
        volume_confirmed=vol_confirmed,
        pattern_strength=strength,
        entry_price=round(entry, 2),
        stop_loss=round(stop, 2),
        target_1=round(target_1, 2),
        target_2=round(target_2, 2),
    )
