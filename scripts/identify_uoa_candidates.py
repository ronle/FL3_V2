#!/usr/bin/env python3
"""
Identify UOA candidates from ORATS data.

Analyzes the latest ORATS daily data to find symbols with unusual options activity
that would be flagged for tracking on the first trading day.

Includes earnings proximity filter to reduce false positives from earnings-related
volume spikes.
"""

import asyncio
import os
import sys

import asyncpg

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.earnings_filter import batch_check_earnings, apply_earnings_penalty, get_earnings_stats
from analysis.direction_classifier import (
    batch_classify_candidates, SignalDirection, get_direction_label, get_entry_label
)

# UOA detection thresholds
VOLUME_RATIO_THRESHOLD = 2.0  # Volume > 2x average (using volume_zscore)
IV_RANK_THRESHOLD = 40  # IV rank > 40
MIN_VOLUME = 1000  # Minimum total volume
EARNINGS_WINDOW_DAYS = 3  # Days before/after earnings to flag
EARNINGS_PENALTY = 0.3  # Score multiplier for earnings-adjacent (70% reduction)


async def get_uoa_candidates(pool: asyncpg.Pool) -> list:
    """Query ORATS data to find UOA candidates."""

    # First check latest date
    latest = await pool.fetchval("SELECT MAX(asof_date) FROM orats_daily")
    print(f"Latest ORATS data: {latest}")

    if not latest:
        print("No ORATS data found!")
        return []

    # Count rows for latest date
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM orats_daily WHERE asof_date = $1", latest
    )
    print(f"Symbols with data: {count}")

    # Query for UOA candidates
    query = """
    SELECT
        symbol,
        asof_date,
        stock_price,
        total_volume,
        call_volume,
        put_volume,
        avg_daily_volume,
        CASE
            WHEN avg_daily_volume > 0
            THEN total_volume::float / avg_daily_volume
            ELSE 1
        END as volume_ratio,
        volume_zscore,
        volume_trend_pct,
        volume_accel,
        iv_rank,
        iv_30day,
        hv_30day,
        iv_hv_ratio,
        put_call_ratio,
        total_open_interest,
        call_open_interest,
        put_open_interest,
        delta_call_oi,
        delta_put_oi
    FROM orats_daily
    WHERE asof_date = $1
      AND total_volume >= $2
      -- Volume spike: either high z-score or high ratio vs average
      AND (
          volume_zscore >= 1.5
          OR (avg_daily_volume > 0 AND total_volume::float / avg_daily_volume >= $3)
      )
      -- IV elevated (optional - some plays happen at low IV too)
      AND (iv_rank IS NULL OR iv_rank >= $4 OR volume_zscore >= 2.5)
    ORDER BY
        CASE
            WHEN avg_daily_volume > 0
            THEN total_volume::float / avg_daily_volume
            ELSE volume_zscore
        END DESC
    LIMIT 100
    """

    candidates = await pool.fetch(
        query,
        latest,
        MIN_VOLUME,
        VOLUME_RATIO_THRESHOLD,
        IV_RANK_THRESHOLD
    )

    return candidates


def calculate_score(candidate: dict) -> float:
    """Calculate raw UOA score for a candidate."""
    vol_ratio = float(candidate['volume_ratio'] or 1)
    vol_zscore = float(candidate['volume_zscore'] or 0)
    iv = float(candidate['iv_rank'] or 0)

    vol_score = min(vol_ratio, 10) * 10  # Cap at 100
    zscore_score = min(vol_zscore * 20, 50)  # Cap at 50
    iv_score = iv / 2  # 0-50
    return vol_score + zscore_score + iv_score


async def main():
    # Get database URL from environment
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    # Connect to database
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    print("=" * 80)
    print("FL3_V2 UOA CANDIDATE IDENTIFICATION")
    print("=" * 80)
    print()

    try:
        # Get earnings stats first
        earnings_stats = await get_earnings_stats(pool)
        if earnings_stats:
            print(f"Earnings Calendar: {earnings_stats.get('today', 0)} today, "
                  f"{earnings_stats.get('tomorrow', 0)} tomorrow, "
                  f"{earnings_stats.get('yesterday', 0)} yesterday")
            print()

        candidates = await get_uoa_candidates(pool)

        if not candidates:
            print("No UOA candidates found matching criteria.")
            print(f"  - Volume ratio >= {VOLUME_RATIO_THRESHOLD}x OR volume_zscore >= 1.5")
            print(f"  - IV rank >= {IV_RANK_THRESHOLD} (or high volume spike)")
            print(f"  - Min volume >= {MIN_VOLUME}")
            return

        print(f"Found {len(candidates)} UOA candidates")

        # Check earnings proximity for all candidates
        symbols = [c['symbol'] for c in candidates]
        earnings_map = await batch_check_earnings(symbols, pool, days=EARNINGS_WINDOW_DAYS)

        # Classify direction for all candidates
        direction_map = await batch_classify_candidates(
            [{'symbol': c['symbol'], 'put_call_ratio': c.get('put_call_ratio')} for c in candidates],
            pool
        )

        # Score all candidates and separate by earnings status
        scored_candidates = []
        for c in candidates:
            symbol = c['symbol']
            raw_score = calculate_score(c)

            # Check earnings
            is_earnings, days_to, timing = earnings_map.get(symbol, (False, None, None))

            # Apply penalty if earnings-adjacent
            adjusted_score = apply_earnings_penalty(raw_score, is_earnings, EARNINGS_PENALTY)

            # Get direction classification
            direction_signal = direction_map.get(symbol)

            scored_candidates.append({
                'candidate': c,
                'raw_score': raw_score,
                'adjusted_score': adjusted_score,
                'is_earnings': is_earnings,
                'earnings_days': days_to,
                'earnings_timing': timing,
                'direction': direction_signal,
            })

        # Sort by adjusted score
        scored_candidates.sort(key=lambda x: x['adjusted_score'], reverse=True)

        # Separate into earnings-adjacent and clean
        earnings_adjacent = [s for s in scored_candidates if s['is_earnings']]
        clean_candidates = [s for s in scored_candidates if not s['is_earnings']]

        # Print filter statistics
        print("\n" + "=" * 80)
        print("EARNINGS FILTER STATS")
        print("=" * 80)
        print(f"  Total candidates: {len(candidates)}")
        print(f"  Earnings-adjacent: {len(earnings_adjacent)} (filtered)")
        print(f"  Clean candidates: {len(clean_candidates)}")
        print(f"  Earnings window: +/- {EARNINGS_WINDOW_DAYS} days")
        print(f"  Earnings penalty: {EARNINGS_PENALTY}x score ({int((1-EARNINGS_PENALTY)*100)}% reduction)")

        # Print earnings-adjacent candidates
        print("\n" + "=" * 80)
        print(f"EARNINGS-ADJACENT ({EARNINGS_PENALTY}x Confidence) - {len(earnings_adjacent)} symbols")
        print("=" * 80)
        if earnings_adjacent:
            print(f"\n{'Symbol':<8} {'Score':>7} {'(was)':>7} {'Price':>8} {'Volume':>10} {'Earnings':>12}")
            print("-" * 60)
            for s in earnings_adjacent[:20]:
                c = s['candidate']
                print(f"{c['symbol']:<8} {s['adjusted_score']:>7.1f} ({s['raw_score']:>5.0f}) "
                      f"${float(c['stock_price']):>7.2f} {c['total_volume']:>10,} {s['earnings_timing']:>12}")
        else:
            print("\n  (No earnings-adjacent candidates)")

        # Print clean candidates
        print("\n" + "=" * 80)
        print(f"CLEAN CANDIDATES (Full Confidence) - {len(clean_candidates)} symbols")
        print("=" * 80)
        if clean_candidates:
            print(f"\n{'Rank':<5} {'Symbol':<8} {'Score':>7} {'Price':>8} {'Volume':>10} "
                  f"{'Ratio':>6} {'ZScore':>7} {'IVRank':>7}")
            print("-" * 70)
            for i, s in enumerate(clean_candidates[:30], 1):
                c = s['candidate']
                print(f"{i:<5} {c['symbol']:<8} {s['adjusted_score']:>7.1f} ${float(c['stock_price']):>7.2f} "
                      f"{c['total_volume']:>10,} {float(c['volume_ratio'] or 0):>6.1f}x "
                      f"{float(c['volume_zscore'] or 0):>7.2f} {float(c['iv_rank'] or 0):>7.0f}")
        else:
            print("\n  (No clean candidates)")

        # Categorize clean candidates
        print("\n" + "=" * 80)
        print("CLEAN CANDIDATES BY CATEGORY")
        print("=" * 80)

        clean_cands = [s['candidate'] for s in clean_candidates]

        # Call-heavy (bullish signal)
        call_heavy = [c for c in clean_cands if (c['put_call_ratio'] or 999) < 0.5]
        print(f"\nCALL-HEAVY (C/P < 0.5) - Potential bullish: {len(call_heavy)} symbols")
        for c in call_heavy[:10]:
            ratio = float(c['volume_ratio'] or c['volume_zscore'] or 0)
            print(f"  {c['symbol']:<8} ${float(c['stock_price']):>7.2f}  Vol: {c['total_volume']:>8,}  "
                  f"Ratio: {ratio:.1f}x  IV: {float(c['iv_rank'] or 0):.0f}")

        # Put-heavy (bearish signal or hedging)
        put_heavy = [c for c in clean_cands if (c['put_call_ratio'] or 0) > 2.0]
        print(f"\nPUT-HEAVY (C/P > 2.0) - Potential bearish/hedge: {len(put_heavy)} symbols")
        for c in put_heavy[:10]:
            ratio = float(c['volume_ratio'] or c['volume_zscore'] or 0)
            print(f"  {c['symbol']:<8} ${float(c['stock_price']):>7.2f}  Vol: {c['total_volume']:>8,}  "
                  f"Ratio: {ratio:.1f}x  IV: {float(c['iv_rank'] or 0):.0f}")

        # High IV with volume spike
        high_iv = [c for c in clean_cands if (c['iv_rank'] or 0) >= 70]
        print(f"\nHIGH IV (>= 70): {len(high_iv)} symbols")
        for c in high_iv[:10]:
            ratio = float(c['volume_ratio'] or c['volume_zscore'] or 0)
            print(f"  {c['symbol']:<8} ${float(c['stock_price']):>7.2f}  Vol: {c['total_volume']:>8,}  "
                  f"Ratio: {ratio:.1f}x  IV: {float(c['iv_rank'] or 0):.0f}")

        # Final summary
        print("\n" + "=" * 80)
        print("TOP 10 RECOMMENDATIONS (Clean + High Score)")
        print("=" * 80)
        print(f"\n{'Rank':<5} {'Symbol':<8} {'Score':>7} {'Price':>8} {'Volume':>10} {'Dir':>5} {'Entry':>6} {'Category':<12}")
        print("-" * 75)

        for i, s in enumerate(clean_candidates[:10], 1):
            c = s['candidate']
            direction = s.get('direction')

            # Determine category
            pc_ratio = float(c['put_call_ratio'] or 1)
            if pc_ratio < 0.5:
                category = "Call-Heavy"
            elif pc_ratio > 2.0:
                category = "Put-Heavy"
            elif float(c['iv_rank'] or 0) >= 70:
                category = "High IV"
            else:
                category = "Balanced"

            dir_label = get_direction_label(direction) if direction else "N/A"
            entry_label = get_entry_label(direction) if direction else "N/A"

            print(f"{i:<5} {c['symbol']:<8} {s['adjusted_score']:>7.1f} ${float(c['stock_price']):>7.2f} "
                  f"{c['total_volume']:>10,} {dir_label:>5} {entry_label:>6} {category:<12}")

        # Direction summary
        print("\n" + "=" * 80)
        print("DIRECTION SUMMARY (Clean Candidates)")
        print("=" * 80)

        bullish = [s for s in clean_candidates if s.get('direction') and s['direction'].direction == SignalDirection.BULLISH]
        bearish = [s for s in clean_candidates if s.get('direction') and s['direction'].direction == SignalDirection.BEARISH]
        neutral = [s for s in clean_candidates if s.get('direction') and s['direction'].direction == SignalDirection.NEUTRAL]

        print(f"\n  BULLISH (LONG entry):  {len(bullish):>3} candidates")
        print(f"  BEARISH (SHORT entry): {len(bearish):>3} candidates")
        print(f"  NEUTRAL (either side): {len(neutral):>3} candidates")

        if bullish:
            print(f"\n  Top 5 BULLISH: {', '.join(s['candidate']['symbol'] for s in bullish[:5])}")
        if bearish:
            print(f"  Top 5 BEARISH: {', '.join(s['candidate']['symbol'] for s in bearish[:5])}")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
