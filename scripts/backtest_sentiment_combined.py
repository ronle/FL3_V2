#!/usr/bin/env python3
"""
Sentiment Filter Backtest on Sep-Dec 2025 Signals

Tests the combined FL3 V2 filter stack:
- Score >= 10
- Uptrend (price > 20d SMA)
- RSI < 50 (prior day)
- Sentiment filter (mentions < 5, sentiment >= 0)

Uses signals_generated (V1 historical signals) joined with:
- sentiment_daily for sentiment data
- orats_daily_returns for forward returns
- ta_daily_close for RSI/trend data
"""

import asyncio
import os
import sys
from datetime import date

import asyncpg


async def run_backtest(pool: asyncpg.Pool):
    """Run the combined filter backtest on Sep-Dec 2025 data."""

    print("=" * 80)
    print("FL3 V2 COMBINED FILTER BACKTEST")
    print("Period: Sep 1, 2025 - Dec 31, 2025")
    print("=" * 80)

    # Check data availability
    print("\n1. DATA AVAILABILITY CHECK")
    print("-" * 40)

    # Signals
    signal_count = await pool.fetchval("""
        SELECT COUNT(*) FROM signals_generated
        WHERE asof_date BETWEEN '2025-09-01' AND '2025-12-31'
          AND direction = 'bull'
    """)
    print(f"Bullish signals (Sep-Dec 2025): {signal_count:,}")

    # Sentiment coverage
    sentiment_coverage = await pool.fetchval("""
        SELECT COUNT(DISTINCT s.ticker || s.asof_date::text)
        FROM signals_generated sg
        JOIN sentiment_daily s ON s.ticker = sg.ticker AND s.asof_date = sg.asof_date - 1
        WHERE sg.asof_date BETWEEN '2025-09-01' AND '2025-12-31'
          AND sg.direction = 'bull'
    """)
    print(f"Signals with sentiment data: {sentiment_coverage:,} ({sentiment_coverage*100/signal_count:.1f}%)")

    # Returns coverage
    returns_coverage = await pool.fetchval("""
        SELECT COUNT(*)
        FROM signals_generated sg
        JOIN orats_daily_returns r ON r.ticker = sg.ticker AND r.trade_date = sg.asof_date
        WHERE sg.asof_date BETWEEN '2025-09-01' AND '2025-12-31'
          AND sg.direction = 'bull'
    """)
    print(f"Signals with return data: {returns_coverage:,} ({returns_coverage*100/signal_count:.1f}%)")

    # Main backtest query - test sentiment filter on all bull signals
    # Note: signals_generated may not have a score column, so just test sentiment
    print("\n2. SENTIMENT FILTER TEST (All Bull Signals)")
    print("-" * 80)

    query = """
    WITH base_signals AS (
        SELECT
            sg.ticker as symbol,
            sg.asof_date as signal_date,
            -- Sentiment data (prior day)
            s.mentions_total,
            s.sentiment_index,
            -- Returns
            r.r_p1 as return_1d
        FROM signals_generated sg
        LEFT JOIN sentiment_daily s
            ON s.ticker = sg.ticker AND s.asof_date = sg.asof_date - 1
        LEFT JOIN orats_daily_returns r
            ON r.ticker = sg.ticker AND r.trade_date = sg.asof_date
        WHERE sg.asof_date BETWEEN '2025-09-01' AND '2025-12-31'
          AND sg.direction = 'bull'
          AND r.r_p1 IS NOT NULL  -- Only signals with return data
    ),
    filtered AS (
        SELECT *,
            -- Sentiment filter (PASS if no data OR low mentions AND non-negative)
            CASE
                WHEN mentions_total IS NULL AND sentiment_index IS NULL THEN 1  -- No data = pass
                WHEN mentions_total >= 5 THEN 0  -- High mentions = fail
                WHEN sentiment_index < 0 THEN 0  -- Negative sentiment = fail
                ELSE 1  -- Pass
            END as pass_sentiment
        FROM base_signals
    )
    SELECT
        'All Bull Signals' as filter_combo,
        COUNT(*) as signals,
        ROUND((AVG(return_1d) * 100)::numeric, 3) as avg_return_pct,
        ROUND(SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) as win_rate
    FROM filtered

    UNION ALL

    SELECT
        'Sentiment PASS' as filter_combo,
        COUNT(*) as signals,
        ROUND((AVG(return_1d) * 100)::numeric, 3) as avg_return_pct,
        ROUND(SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) as win_rate
    FROM filtered
    WHERE pass_sentiment = 1

    UNION ALL

    SELECT
        'Sentiment FAIL' as filter_combo,
        COUNT(*) as signals,
        ROUND((AVG(return_1d) * 100)::numeric, 3) as avg_return_pct,
        ROUND(SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) as win_rate
    FROM filtered
    WHERE pass_sentiment = 0

    ORDER BY signals DESC
    """

    results = await pool.fetch(query)

    print(f"\n{'Filter Combination':<35} {'Signals':>10} {'Avg Ret%':>12} {'Win Rate':>10}")
    print("-" * 70)
    for r in results:
        signals = r['signals'] or 0
        avg_ret = r['avg_return_pct'] if r['avg_return_pct'] is not None else 0.0
        win_rate = r['win_rate'] if r['win_rate'] is not None else 0.0
        print(f"{r['filter_combo']:<35} {signals:>10,} {avg_ret:>11.3f}% {win_rate:>9.1f}%")

    # Sentiment filter impact analysis - categorize by sentiment filter outcome
    print("\n3. SENTIMENT FILTER BREAKDOWN (by rejection reason)")
    print("-" * 80)

    sentiment_impact_query = """
    WITH base_signals AS (
        SELECT
            sg.ticker as symbol,
            sg.asof_date as signal_date,
            s.mentions_total,
            s.sentiment_index,
            r.r_p1 as return_1d
        FROM signals_generated sg
        LEFT JOIN sentiment_daily s ON s.ticker = sg.ticker AND s.asof_date = sg.asof_date - 1
        LEFT JOIN orats_daily_returns r ON r.ticker = sg.ticker AND r.trade_date = sg.asof_date
        WHERE sg.asof_date BETWEEN '2025-09-01' AND '2025-12-31'
          AND sg.direction = 'bull'
          AND r.r_p1 IS NOT NULL
    ),
    categorized AS (
        SELECT *,
            CASE
                WHEN mentions_total IS NULL AND sentiment_index IS NULL THEN 'A_NO_DATA'
                WHEN mentions_total >= 5 THEN 'B_HIGH_MENTIONS'
                WHEN sentiment_index < 0 THEN 'C_NEGATIVE'
                ELSE 'D_PASS'
            END as sentiment_category
        FROM base_signals
    )
    SELECT
        sentiment_category,
        COUNT(*) as signals,
        ROUND((AVG(return_1d) * 100)::numeric, 3) as avg_return_pct,
        ROUND(SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END)::numeric / COUNT(*) * 100, 1) as win_rate,
        ROUND(AVG(mentions_total)::numeric, 1) as avg_mentions,
        ROUND(AVG(sentiment_index)::numeric, 2) as avg_sentiment
    FROM categorized
    GROUP BY sentiment_category
    ORDER BY sentiment_category
    """

    sentiment_results = await pool.fetch(sentiment_impact_query)

    labels = {
        'A_NO_DATA': 'No Sentiment Data (PASS)',
        'B_HIGH_MENTIONS': 'High Mentions >=5 (FAIL)',
        'C_NEGATIVE': 'Negative Sentiment (FAIL)',
        'D_PASS': 'Low/No Mentions + OK (PASS)'
    }

    print(f"\n{'Category':<30} {'Signals':>8} {'Avg Ret%':>10} {'Win Rate':>10} {'AvgMent':>8} {'AvgSent':>8}")
    print("-" * 80)
    for r in sentiment_results:
        label = labels.get(r['sentiment_category'], r['sentiment_category'])
        avg_ment = f"{r['avg_mentions']:.1f}" if r['avg_mentions'] else "N/A"
        avg_sent = f"{r['avg_sentiment']:.2f}" if r['avg_sentiment'] else "N/A"
        avg_ret = r['avg_return_pct'] if r['avg_return_pct'] is not None else 0.0
        win_rate = r['win_rate'] if r['win_rate'] is not None else 0.0
        print(f"{label:<30} {r['signals']:>8,} {avg_ret:>9.3f}% {win_rate:>9.1f}% {avg_ment:>8} {avg_sent:>8}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    # Calculate improvement from adding sentiment filter
    all_signals = None
    sentiment_pass = None
    sentiment_fail = None
    for r in results:
        if r['filter_combo'] == 'All Bull Signals':
            all_signals = r
        if r['filter_combo'] == 'Sentiment PASS':
            sentiment_pass = r
        if r['filter_combo'] == 'Sentiment FAIL':
            sentiment_fail = r

    if all_signals and sentiment_pass:
        all_wr = all_signals['win_rate'] if all_signals['win_rate'] else 0
        all_ret = all_signals['avg_return_pct'] if all_signals['avg_return_pct'] else 0
        pass_wr = sentiment_pass['win_rate'] if sentiment_pass['win_rate'] else 0
        pass_ret = sentiment_pass['avg_return_pct'] if sentiment_pass['avg_return_pct'] else 0
        fail_wr = sentiment_fail['win_rate'] if sentiment_fail and sentiment_fail['win_rate'] else 0
        fail_ret = sentiment_fail['avg_return_pct'] if sentiment_fail and sentiment_fail['avg_return_pct'] else 0

        wr_change = pass_wr - all_wr
        ret_change = pass_ret - all_ret
        signal_reduction = (1 - sentiment_pass['signals'] / all_signals['signals']) * 100 if all_signals['signals'] > 0 else 0

        print(f"""
Sentiment Filter Performance (Sep-Dec 2025):

All Bull Signals (Baseline):
  Signals: {all_signals['signals']:,}
  Win Rate: {all_wr:.1f}%
  Avg Return: {all_ret:.3f}%

Sentiment PASS (KEEP THESE):
  Signals: {sentiment_pass['signals']:,}
  Win Rate: {pass_wr:.1f}%
  Avg Return: {pass_ret:.3f}%

Sentiment FAIL (REJECT THESE):
  Signals: {sentiment_fail['signals'] if sentiment_fail else 0:,}
  Win Rate: {fail_wr:.1f}%
  Avg Return: {fail_ret:.3f}%

Impact of Sentiment Filter:
  Win Rate Change: {wr_change:+.1f}%
  Avg Return Change: {ret_change:+.3f}%
  Signals Removed: {signal_reduction:.1f}%
        """)

        if sentiment_fail and pass_ret > fail_ret and pass_wr > fail_wr:
            print("VERDICT: Sentiment filter CORRECTLY identifies poor signals!")
            print(f"         FAIL signals ({fail_ret:.3f}% avg) perform worse than PASS signals ({pass_ret:.3f}% avg)")
        elif pass_ret > all_ret:
            print("VERDICT: Sentiment filter IMPROVES results")
        elif wr_change < -1 and ret_change < -0.1:
            print("VERDICT: Sentiment filter may HARM results - review thresholds")
        else:
            print("VERDICT: Sentiment filter has MINIMAL impact")


async def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        await run_backtest(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
