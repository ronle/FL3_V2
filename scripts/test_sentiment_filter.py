#!/usr/bin/env python3
"""
TEST-8: Sentiment Filter Validation

Checks sentiment_daily data status and backtests the filter against
UOA signals to validate the expected improvement.

Expected Results (from CLI_UPDATE_PLAN_v2.md):
- High mentions (>=5): -0.73% avg, 40% WR -> REJECT
- Negative sentiment: -1.23% avg, 34% WR -> REJECT
- Low/no mentions + non-negative: +0.42% avg, 47% WR -> PASS
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta

import asyncpg


async def check_sentiment_data_status(pool: asyncpg.Pool):
    """Check the status of sentiment_daily table."""
    print("=" * 70)
    print("SENTIMENT DATA STATUS")
    print("=" * 70)

    # Check table exists and row count
    count = await pool.fetchval("SELECT COUNT(*) FROM sentiment_daily")
    print(f"\nTotal rows: {count:,}")

    # Check date range
    date_range = await pool.fetchrow("""
        SELECT MIN(asof_date) as earliest, MAX(asof_date) as latest
        FROM sentiment_daily
    """)
    print(f"Date range: {date_range['earliest']} to {date_range['latest']}")

    # Check recent data (is pipeline running?)
    recent = await pool.fetch("""
        SELECT asof_date, COUNT(*) as symbols
        FROM sentiment_daily
        WHERE asof_date >= CURRENT_DATE - 10
        GROUP BY asof_date
        ORDER BY asof_date DESC
        LIMIT 10
    """)

    print(f"\nRecent data (last 10 days):")
    if recent:
        for r in recent:
            print(f"  {r['asof_date']}: {r['symbols']:,} symbols")
    else:
        print("  (No recent data - pipeline may be stopped!)")

    # Check coverage
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    yesterday_count = await pool.fetchval("""
        SELECT COUNT(*) FROM sentiment_daily WHERE asof_date = $1
    """, yesterday)
    print(f"\nYesterday ({yesterday}): {yesterday_count:,} symbols")

    return date_range['latest']


async def backtest_sentiment_filter(pool: asyncpg.Pool):
    """
    Backtest sentiment filter against UOA triggers.

    Note: Uses uoa_triggers_v2 (FL3 V2 signals) not signals_generated (V1).
    """
    print("\n" + "=" * 70)
    print("SENTIMENT FILTER BACKTEST")
    print("=" * 70)

    # First check if we have uoa_triggers_v2 data
    trigger_count = await pool.fetchval("SELECT COUNT(*) FROM uoa_triggers_v2")
    if trigger_count == 0:
        print("\nNo data in uoa_triggers_v2 yet.")
        print("Run UOA scan job first to generate signals.")
        return

    print(f"\nTotal UOA triggers: {trigger_count:,}")

    # Get trigger date range
    trigger_dates = await pool.fetchrow("""
        SELECT MIN(trigger_ts::date) as earliest, MAX(trigger_ts::date) as latest
        FROM uoa_triggers_v2
    """)
    print(f"Trigger date range: {trigger_dates['earliest']} to {trigger_dates['latest']}")

    # Backtest query - join triggers with sentiment and returns
    # Note: We use T-1 sentiment (prior day) since that's what's available at signal time
    query = """
    WITH trigger_sentiment AS (
        SELECT
            t.symbol,
            t.trigger_ts::date as trigger_date,
            s.mentions_total,
            s.sentiment_index,
            CASE
                WHEN s.mentions_total IS NULL AND s.sentiment_index IS NULL THEN 'NO_DATA'
                WHEN s.mentions_total >= 5 THEN 'HIGH_MENTIONS'
                WHEN s.sentiment_index < 0 THEN 'NEGATIVE'
                ELSE 'PASS'
            END as filter_result
        FROM uoa_triggers_v2 t
        LEFT JOIN sentiment_daily s
            ON s.ticker = t.symbol
            AND s.asof_date = t.trigger_ts::date - 1
    )
    SELECT
        filter_result,
        COUNT(*) as signals,
        ROUND(COUNT(*)::numeric / SUM(COUNT(*)) OVER () * 100, 1) as pct
    FROM trigger_sentiment
    GROUP BY filter_result
    ORDER BY signals DESC
    """

    results = await pool.fetch(query)

    print(f"\nFilter Distribution:")
    print(f"{'Result':<15} {'Signals':>10} {'%':>8}")
    print("-" * 35)
    for r in results:
        print(f"{r['filter_result']:<15} {r['signals']:>10,} {r['pct']:>7.1f}%")

    # If we have orats_daily_returns, we can calculate actual performance
    print("\n" + "-" * 70)
    print("Performance by Filter (requires orats_daily_returns)")
    print("-" * 70)

    perf_query = """
    WITH trigger_sentiment AS (
        SELECT
            t.symbol,
            t.trigger_ts::date as trigger_date,
            s.mentions_total,
            s.sentiment_index,
            CASE
                WHEN s.mentions_total IS NULL AND s.sentiment_index IS NULL THEN 'NO_DATA'
                WHEN s.mentions_total >= 5 THEN 'HIGH_MENTIONS'
                WHEN s.sentiment_index < 0 THEN 'NEGATIVE'
                ELSE 'PASS'
            END as filter_result
        FROM uoa_triggers_v2 t
        LEFT JOIN sentiment_daily s
            ON s.ticker = t.symbol
            AND s.asof_date = t.trigger_ts::date - 1
    ),
    with_returns AS (
        SELECT
            ts.*,
            r.r_p1 as return_1d
        FROM trigger_sentiment ts
        LEFT JOIN orats_daily_returns r
            ON r.ticker = ts.symbol
            AND r.trade_date = ts.trigger_date
    )
    SELECT
        filter_result,
        COUNT(*) as signals,
        COUNT(return_1d) as with_returns,
        ROUND(AVG(return_1d) * 100, 3) as avg_return_pct,
        ROUND(SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END)::numeric /
              NULLIF(COUNT(return_1d), 0) * 100, 1) as win_rate
    FROM with_returns
    GROUP BY filter_result
    ORDER BY avg_return_pct DESC NULLS LAST
    """

    try:
        perf = await pool.fetch(perf_query)
        print(f"\n{'Result':<15} {'Signals':>8} {'W/Returns':>10} {'Avg Ret%':>10} {'Win Rate':>10}")
        print("-" * 55)
        for r in perf:
            avg_ret = f"{r['avg_return_pct']:.3f}%" if r['avg_return_pct'] else "N/A"
            wr = f"{r['win_rate']:.1f}%" if r['win_rate'] else "N/A"
            print(f"{r['filter_result']:<15} {r['signals']:>8,} {r['with_returns']:>10,} {avg_ret:>10} {wr:>10}")
    except Exception as e:
        print(f"Could not calculate performance: {e}")
        print("(orats_daily_returns may not have matching data)")


async def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        latest_date = await check_sentiment_data_status(pool)

        # Check if pipeline needs restart
        today = datetime.now().date()
        if latest_date and (today - latest_date).days > 2:
            print(f"\n{'!'*70}")
            print("WARNING: Sentiment pipeline appears to be stopped!")
            print(f"Last data: {latest_date}, Today: {today}")
            print(f"Gap: {(today - latest_date).days} days")
            print("ACTION: Restart the sentiment ingest job in V1 project")
            print(f"{'!'*70}")

        await backtest_sentiment_filter(pool)

        print("\n" + "=" * 70)
        print("RECOMMENDATIONS")
        print("=" * 70)
        print("""
Based on TEST-8 analysis:
1. REJECT signals with mentions >= 5 (crowded trades perform poorly)
2. REJECT signals with negative sentiment (34% WR, -1.2% avg)
3. PASS signals with low/no mentions and non-negative sentiment

Sentiment filter has been added to paper_trading/signal_filter.py
- Config: USE_SENTIMENT_FILTER = True (enabled by default)
- Config: SENTIMENT_MAX_MENTIONS = 5
- Config: SENTIMENT_MIN_INDEX = 0.0

BLOCKER: If sentiment pipeline is stopped, restart it!
        """)

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
