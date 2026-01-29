"""
Signal Direction Classifier (Component 5.6)

Classifies UOA signals as BULLISH (pump) or BEARISH (dump) based on
options flow direction and price trend. Enables playing both long and short sides.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional, Tuple
import logging

import asyncpg

logger = logging.getLogger(__name__)


class SignalDirection(Enum):
    """Direction classification for UOA signals."""
    BULLISH = "BULLISH"   # Call-heavy, price flat/up -> LONG entry
    BEARISH = "BEARISH"   # Put-heavy, price declining -> SHORT entry
    NEUTRAL = "NEUTRAL"   # Mixed signals -> smaller position or skip


class EntrySide(Enum):
    """Recommended entry side based on signal direction."""
    LONG = "LONG"
    SHORT = "SHORT"
    EITHER = "EITHER"


@dataclass
class DirectionSignal:
    """Complete direction classification result."""
    direction: SignalDirection
    entry_side: EntrySide
    confidence: float           # 0.0-1.0
    put_call_ratio: float       # P/C ratio from ORATS
    price_trend_pct: float      # 5-day price change %
    size_modifier: float        # Position size multiplier (0.5 for NEUTRAL, 1.0 for directional)
    reasoning: str              # Human-readable explanation


# Classification thresholds
CALL_HEAVY_THRESHOLD = 0.5      # P/C < 0.5 = call-heavy
PUT_HEAVY_THRESHOLD = 2.0       # P/C > 2.0 = put-heavy
UPTREND_THRESHOLD = 2.0         # > 2% = uptrend
DOWNTREND_THRESHOLD = -2.0      # < -2% = downtrend


async def get_price_trend(
    symbol: str,
    pool: asyncpg.Pool,
    lookback_days: int = 5
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculate price trend over lookback period.

    Args:
        symbol: Stock symbol
        pool: Database connection pool
        lookback_days: Number of days to look back (default 5)

    Returns:
        Tuple of (pct_change, current_price, prior_price)
        Returns (None, None, None) if insufficient data
    """
    # spot_prices table uses: ticker, underlying (as price), trade_date
    query = """
        SELECT underlying as price, trade_date
        FROM spot_prices
        WHERE ticker = $1
        ORDER BY trade_date DESC
        LIMIT 1
    """

    query_prior = """
        SELECT underlying as price, trade_date
        FROM spot_prices
        WHERE ticker = $1
          AND trade_date <= CURRENT_DATE - make_interval(days => $2)
        ORDER BY trade_date DESC
        LIMIT 1
    """

    try:
        # Get current price
        current = await pool.fetchrow(query, symbol)
        if not current:
            return (None, None, None)

        current_price = float(current['price'])

        # Get prior price
        prior = await pool.fetchrow(query_prior, symbol, lookback_days)
        if not prior:
            return (None, current_price, None)

        prior_price = float(prior['price'])

        # Calculate percentage change
        if prior_price > 0:
            pct_change = ((current_price - prior_price) / prior_price) * 100
            return (pct_change, current_price, prior_price)

        return (None, current_price, prior_price)

    except Exception as e:
        logger.warning(f"Price trend lookup failed for {symbol}: {e}")
        return (None, None, None)


def classify_direction(
    put_call_ratio: float,
    price_trend_pct: Optional[float]
) -> DirectionSignal:
    """
    Classify signal direction based on put/call ratio and price trend.

    Classification Logic:
    - Call-heavy (P/C < 0.5) + flat/up trend = BULLISH -> LONG
    - Put-heavy (P/C > 2.0) + declining trend = BEARISH -> SHORT
    - Mixed signals = NEUTRAL -> EITHER (smaller size)

    Args:
        put_call_ratio: Put/Call volume ratio from ORATS
        price_trend_pct: 5-day price change percentage (or None if unavailable)

    Returns:
        DirectionSignal with classification and recommendations
    """
    # Default values for missing data
    if price_trend_pct is None:
        price_trend_pct = 0.0

    # Determine option flow direction
    is_call_heavy = put_call_ratio < CALL_HEAVY_THRESHOLD
    is_put_heavy = put_call_ratio > PUT_HEAVY_THRESHOLD

    # Determine price trend
    is_uptrend = price_trend_pct >= UPTREND_THRESHOLD
    is_downtrend = price_trend_pct <= DOWNTREND_THRESHOLD
    is_flat = not is_uptrend and not is_downtrend

    # Classification logic
    if is_call_heavy and (is_uptrend or is_flat):
        # Strong bullish signal
        confidence = 0.8 if is_uptrend else 0.6
        return DirectionSignal(
            direction=SignalDirection.BULLISH,
            entry_side=EntrySide.LONG,
            confidence=confidence,
            put_call_ratio=put_call_ratio,
            price_trend_pct=price_trend_pct,
            size_modifier=1.0,
            reasoning=f"Call-heavy flow (P/C={put_call_ratio:.2f}) with {'uptrend' if is_uptrend else 'flat'} price ({price_trend_pct:+.1f}%)"
        )

    elif is_put_heavy and is_downtrend:
        # Strong bearish signal
        return DirectionSignal(
            direction=SignalDirection.BEARISH,
            entry_side=EntrySide.SHORT,
            confidence=0.7,
            put_call_ratio=put_call_ratio,
            price_trend_pct=price_trend_pct,
            size_modifier=1.0,
            reasoning=f"Put-heavy flow (P/C={put_call_ratio:.2f}) with downtrend price ({price_trend_pct:+.1f}%)"
        )

    elif is_put_heavy and (is_uptrend or is_flat):
        # Put buying into strength - could be hedging or reversal setup
        return DirectionSignal(
            direction=SignalDirection.NEUTRAL,
            entry_side=EntrySide.EITHER,
            confidence=0.4,
            put_call_ratio=put_call_ratio,
            price_trend_pct=price_trend_pct,
            size_modifier=0.5,
            reasoning=f"Put-heavy flow (P/C={put_call_ratio:.2f}) but price {'up' if is_uptrend else 'flat'} - possible hedging"
        )

    elif is_call_heavy and is_downtrend:
        # Call buying into weakness - could be bottom fishing
        return DirectionSignal(
            direction=SignalDirection.NEUTRAL,
            entry_side=EntrySide.EITHER,
            confidence=0.4,
            put_call_ratio=put_call_ratio,
            price_trend_pct=price_trend_pct,
            size_modifier=0.5,
            reasoning=f"Call-heavy flow (P/C={put_call_ratio:.2f}) but price down ({price_trend_pct:+.1f}%) - bottom fishing?"
        )

    else:
        # Balanced flow
        return DirectionSignal(
            direction=SignalDirection.NEUTRAL,
            entry_side=EntrySide.EITHER,
            confidence=0.3,
            put_call_ratio=put_call_ratio,
            price_trend_pct=price_trend_pct,
            size_modifier=0.5,
            reasoning=f"Balanced flow (P/C={put_call_ratio:.2f}) with mixed trend ({price_trend_pct:+.1f}%)"
        )


async def classify_candidate(
    symbol: str,
    put_call_ratio: float,
    pool: asyncpg.Pool,
    lookback_days: int = 5
) -> DirectionSignal:
    """
    Full classification for a UOA candidate.

    Args:
        symbol: Stock symbol
        put_call_ratio: Put/Call ratio from ORATS data
        pool: Database connection pool
        lookback_days: Days for price trend calculation

    Returns:
        DirectionSignal with full classification
    """
    # Get price trend
    pct_change, current_price, prior_price = await get_price_trend(
        symbol, pool, lookback_days
    )

    # Classify
    signal = classify_direction(put_call_ratio, pct_change)

    # Log classification
    logger.debug(f"{symbol}: {signal.direction.value} ({signal.reasoning})")

    return signal


async def batch_classify_candidates(
    candidates: list[dict],
    pool: asyncpg.Pool,
    lookback_days: int = 5
) -> dict[str, DirectionSignal]:
    """
    Batch classify multiple UOA candidates.

    Args:
        candidates: List of candidate dicts with 'symbol' and 'put_call_ratio'
        pool: Database connection pool
        lookback_days: Days for price trend calculation

    Returns:
        Dict mapping symbol -> DirectionSignal
    """
    if not candidates:
        return {}

    results = {}

    # Get all symbols and their P/C ratios
    symbols = [c['symbol'] for c in candidates]
    pc_ratios = {c['symbol']: float(c.get('put_call_ratio') or 1.0) for c in candidates}

    # Batch fetch price trends (could optimize with a single query)
    for symbol in symbols:
        pct_change, _, _ = await get_price_trend(symbol, pool, lookback_days)
        signal = classify_direction(pc_ratios[symbol], pct_change)
        results[symbol] = signal

    # Summary stats
    bullish = sum(1 for s in results.values() if s.direction == SignalDirection.BULLISH)
    bearish = sum(1 for s in results.values() if s.direction == SignalDirection.BEARISH)
    neutral = sum(1 for s in results.values() if s.direction == SignalDirection.NEUTRAL)

    logger.info(f"Direction classification: {bullish} BULLISH, {bearish} BEARISH, {neutral} NEUTRAL")

    return results


def get_direction_label(signal: DirectionSignal) -> str:
    """Get formatted direction label for display."""
    emoji = {
        SignalDirection.BULLISH: "BULL",
        SignalDirection.BEARISH: "BEAR",
        SignalDirection.NEUTRAL: "NEUT"
    }
    return f"{emoji[signal.direction]:>4}"


def get_entry_label(signal: DirectionSignal) -> str:
    """Get formatted entry side label for display."""
    return signal.entry_side.value
