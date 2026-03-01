"""
Cameron Scanner Filter Chain — Universal Day Trader Stock Selection Criteria

Applies the 7 universal criteria identified across 6 elite day traders
(Ross Cameron, Tim Sykes, Andrew Aziz, Humbled Trader, Steven Dux, Tim Grittani).

Core filters: gap %, relative volume (RVOL), price range, float/market_cap proxy.
Grades signals A+ through C based on filter strength.

Usage:
    from scripts.cameron_filter import CameronConfig, apply_cameron_filters, grade_signal
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class GapGrade(str, Enum):
    A_PLUS = "A+"   # >= 10% gap, rvol >= 5x, low float/mcap
    A = "A"         # >= 10% gap, rvol >= 5x
    B = "B"         # >= 4% gap, rvol >= 3x
    C = "C"         # meets minimum thresholds


@dataclass
class CameronConfig:
    """All thresholds adjustable for backtesting sweep."""
    gap_pct_min: float = 0.04           # >= 4% gap (Cameron minimum)
    gap_pct_strong: float = 0.10        # >= 10% gap (preferred)
    rvol_min: float = 5.0               # >= 5x relative volume
    price_min: float = 1.0              # $1 floor
    price_max: float = 20.0             # $20 ceiling
    mcap_max: Optional[float] = None    # market cap ceiling (float proxy)
    mcap_preferred: float = 100_000_000 # < $100M sweet spot
    adv_min: float = 0.0                # minimum avg daily volume
    # Momentum overlay (for Strategy D)
    require_momentum: bool = False
    momentum_max: float = -0.10         # momentum < -10% (most beaten-down)

    def label(self) -> str:
        """Short human-readable label for this config."""
        parts = [f"gap>={self.gap_pct_min:.0%}"]
        parts.append(f"rvol>={self.rvol_min:.0f}x")
        parts.append(f"${self.price_min:.0f}-${self.price_max:.0f}")
        if self.mcap_max:
            parts.append(f"mcap<{self.mcap_max/1e6:.0f}M")
        if self.require_momentum:
            parts.append(f"mom<{self.momentum_max:.0%}")
        return ", ".join(parts)


@dataclass
class CameronSignal:
    trade_date: str
    symbol: str
    gap_pct: float
    rvol: float
    price: float
    market_cap: Optional[float]
    grade: GapGrade
    open_price: float
    close_price: float
    prev_close: float
    next_day_close: Optional[float]
    next_day_open: Optional[float]
    next_day_high: Optional[float]
    next_day_low: Optional[float]
    intraday_high: float
    intraday_low: float
    daily_volume: int
    avg_30d_volume: Optional[float]
    momentum_20d: Optional[float] = None


def grade_signal(
    gap_pct: float,
    rvol: float,
    market_cap: Optional[float],
    mcap_preferred: float = 100_000_000,
) -> GapGrade:
    """Assign A+/A/B/C grade based on signal strength."""
    is_low_mcap = market_cap is not None and market_cap < mcap_preferred
    if gap_pct >= 0.10 and rvol >= 5.0 and is_low_mcap:
        return GapGrade.A_PLUS
    if gap_pct >= 0.10 and rvol >= 5.0:
        return GapGrade.A
    if gap_pct >= 0.04 and rvol >= 3.0:
        return GapGrade.B
    return GapGrade.C


def apply_cameron_filters(
    row: dict,
    config: CameronConfig,
) -> Tuple[bool, str, Optional[CameronSignal]]:
    """
    Apply Cameron filter chain to a single universe row.

    Returns:
        (passed, rejection_reason, signal_or_None)
    """
    gap_pct = row.get("gap_pct")
    rvol = row.get("rvol")
    price = row.get("close_price", 0)
    mcap = row.get("market_cap")

    # --- Filter 1: Gap % ---
    if gap_pct is None or gap_pct < config.gap_pct_min:
        return False, f"gap {gap_pct:.2%} < {config.gap_pct_min:.0%}", None

    # --- Filter 2: Relative Volume ---
    if rvol is None or rvol < config.rvol_min:
        return False, f"rvol {rvol:.1f}x < {config.rvol_min:.0f}x", None

    # Sanity: extreme RVOL is likely data error
    if rvol > 1_000_000:
        return False, f"rvol {rvol:.0f}x suspiciously high", None

    # --- Filter 3: Price range ---
    if price < config.price_min:
        return False, f"price ${price:.2f} < ${config.price_min:.2f}", None
    if price > config.price_max:
        return False, f"price ${price:.2f} > ${config.price_max:.2f}", None

    # --- Filter 4: Market cap (float proxy) ---
    if config.mcap_max and mcap is not None and mcap > config.mcap_max:
        return False, f"mcap ${mcap/1e6:.0f}M > ${config.mcap_max/1e6:.0f}M", None

    # --- Filter 5: ADV ---
    adv = row.get("avg_30d_volume", 0) or 0
    if adv < config.adv_min:
        return False, f"adv {adv:.0f} < {config.adv_min:.0f}", None

    # --- Filter 6: Momentum overlay (Strategy D only) ---
    if config.require_momentum:
        mom = row.get("momentum_20d")
        if mom is None or mom > config.momentum_max:
            return False, f"momentum {mom} > {config.momentum_max:.0%}", None

    # --- PASSED: grade and build signal ---
    grade = grade_signal(gap_pct, rvol, mcap, config.mcap_preferred)

    signal = CameronSignal(
        trade_date=str(row["trade_date"]),
        symbol=row["symbol"],
        gap_pct=gap_pct,
        rvol=rvol,
        price=price,
        market_cap=mcap,
        grade=grade,
        open_price=row["open_price"],
        close_price=row["close_price"],
        prev_close=row["prev_close"],
        next_day_close=row.get("next_day_close"),
        next_day_open=row.get("next_day_open"),
        next_day_high=row.get("next_day_high"),
        next_day_low=row.get("next_day_low"),
        intraday_high=row["intraday_high"],
        intraday_low=row["intraday_low"],
        daily_volume=int(row["daily_volume"]),
        avg_30d_volume=row.get("avg_30d_volume"),
        momentum_20d=row.get("momentum_20d"),
    )

    return True, "PASS", signal
