"""
Earnings Proximity Filter (Component 5.5)

Filters UOA candidates that are near earnings dates to reduce false positives.
Earnings-related volume spikes are legitimate volatility, not P&D signals.
"""

from datetime import date, timedelta
from typing import Optional, Tuple
import logging

import asyncpg

logger = logging.getLogger(__name__)


async def is_earnings_adjacent(
    symbol: str,
    pool: asyncpg.Pool,
    days: int = 3
) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Check if symbol has earnings within +/- days window.

    Args:
        symbol: Stock symbol
        pool: Database connection pool
        days: Number of days before/after to check (default 3)

    Returns:
        Tuple of (is_adjacent, days_to_earnings, earnings_timing)
        - is_adjacent: True if earnings within window
        - days_to_earnings: Number of days until earnings (negative = past)
        - earnings_timing: 'TODAY', 'TOMORROW', 'YESTERDAY', '+2 DAYS', '-2 DAYS', etc.
    """
    query = """
        SELECT
            event_date,
            event_date - CURRENT_DATE as days_until,
            hour
        FROM earnings_calendar
        WHERE symbol = $1
          AND event_date BETWEEN CURRENT_DATE - $2 AND CURRENT_DATE + $2
          AND is_current = true
        ORDER BY ABS(event_date - CURRENT_DATE)
        LIMIT 1
    """

    try:
        row = await pool.fetchrow(query, symbol, days)

        if row is None:
            return (False, None, None)

        days_until = row['days_until']

        # Determine human-readable timing
        if days_until == 0:
            timing = "TODAY"
        elif days_until == 1:
            timing = "TOMORROW"
        elif days_until == -1:
            timing = "YESTERDAY"
        elif days_until > 0:
            timing = f"+{days_until} DAYS"
        else:
            timing = f"{days_until} DAYS"

        return (True, days_until, timing)

    except Exception as e:
        logger.warning(f"Earnings check failed for {symbol}: {e}")
        return (False, None, None)


async def batch_check_earnings(
    symbols: list[str],
    pool: asyncpg.Pool,
    days: int = 3
) -> dict[str, Tuple[bool, Optional[int], Optional[str]]]:
    """
    Batch check earnings proximity for multiple symbols.

    Args:
        symbols: List of stock symbols
        pool: Database connection pool
        days: Number of days before/after to check

    Returns:
        Dict mapping symbol -> (is_adjacent, days_to_earnings, timing)
    """
    if not symbols:
        return {}

    # Use INTERVAL for date arithmetic
    query = """
        SELECT
            symbol,
            event_date,
            (event_date - CURRENT_DATE) as days_until,
            hour
        FROM earnings_calendar
        WHERE symbol = ANY($1::text[])
          AND event_date >= CURRENT_DATE - make_interval(days => $2)
          AND event_date <= CURRENT_DATE + make_interval(days => $2)
          AND is_current = true
    """

    results = {sym: (False, None, None) for sym in symbols}

    try:
        rows = await pool.fetch(query, symbols, days)

        logger.info(f"Batch earnings check: {len(symbols)} symbols, {len(rows)} with earnings")

        for row in rows:
            symbol = row['symbol']
            days_until = int(row['days_until']) if row['days_until'] is not None else 0

            # Only update if this is closer than existing entry
            existing = results.get(symbol, (False, None, None))
            if existing[1] is None or abs(days_until) < abs(existing[1]):
                if days_until == 0:
                    timing = "TODAY"
                elif days_until == 1:
                    timing = "TOMORROW"
                elif days_until == -1:
                    timing = "YESTERDAY"
                elif days_until > 0:
                    timing = f"+{days_until} DAYS"
                else:
                    timing = f"{days_until} DAYS"

                results[symbol] = (True, days_until, timing)

    except Exception as e:
        logger.warning(f"Batch earnings check failed: {e}")
        import traceback
        logger.warning(traceback.format_exc())

    return results


def apply_earnings_penalty(
    score: float,
    is_earnings_adjacent: bool,
    penalty_multiplier: float = 0.3
) -> float:
    """
    Apply confidence penalty to earnings-adjacent candidates.

    Args:
        score: Original candidate score
        is_earnings_adjacent: Whether candidate is near earnings
        penalty_multiplier: Score multiplier for earnings (default 0.3 = 70% reduction)

    Returns:
        Adjusted score
    """
    if is_earnings_adjacent:
        return score * penalty_multiplier
    return score


async def get_earnings_stats(pool: asyncpg.Pool) -> dict:
    """Get earnings calendar statistics."""
    query = """
        SELECT
            COUNT(*) as total_events,
            COUNT(*) FILTER (WHERE event_date = CURRENT_DATE) as today,
            COUNT(*) FILTER (WHERE event_date = CURRENT_DATE + 1) as tomorrow,
            COUNT(*) FILTER (WHERE event_date = CURRENT_DATE - 1) as yesterday,
            MIN(event_date) as earliest,
            MAX(event_date) as latest
        FROM earnings_calendar
        WHERE is_current = true
          AND event_date BETWEEN CURRENT_DATE - 7 AND CURRENT_DATE + 7
    """

    try:
        row = await pool.fetchrow(query)
        return {
            'total_events': row['total_events'],
            'today': row['today'],
            'tomorrow': row['tomorrow'],
            'yesterday': row['yesterday'],
            'earliest': row['earliest'],
            'latest': row['latest'],
        }
    except Exception as e:
        logger.warning(f"Failed to get earnings stats: {e}")
        return {}
