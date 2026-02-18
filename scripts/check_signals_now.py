#!/usr/bin/env python3
"""Check signal evaluations - closest to passing NOW."""

import os
import sys

import psycopg2


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    print("=" * 110)
    print("Query 1: Most recent signals from the last 30 minutes")
    print("=" * 110)

    cur.execute("""
        SELECT symbol, detected_at, score_total, rejection_reason, notional, rsi_14, trend
        FROM signal_evaluations
        WHERE detected_at > NOW() - INTERVAL '30 minutes'
        ORDER BY detected_at DESC
        LIMIT 30;
    """)
    rows = cur.fetchall()

    if rows:
        print(f"{'Symbol':<8} {'Detected At':<22} {'Score':<7} {'Rejection':<25} {'Notional':<15} {'RSI':<8} {'Trend'}")
        print("-" * 110)
        for r in rows:
            symbol = r[0] or ''
            detected = str(r[1])[:19] if r[1] else ''
            score = f"{r[2]:.1f}" if r[2] else ''
            reason = (r[3] or '')[:24]
            notional = f"{r[4]:,.0f}" if r[4] else ''
            rsi = f"{r[5]:.1f}" if r[5] else ''
            trend = r[6] or ''
            print(f"{symbol:<8} {detected:<22} {score:<7} {reason:<25} {notional:<15} {rsi:<8} {trend}")
    else:
        print("** NO SIGNALS in last 30 minutes! **")

    print()
    print("=" * 110)
    print("Query 2: Score >= 10, RSI < 50, failed ONLY on trend (last 2 hours)")
    print("=" * 110)

    cur.execute("""
        SELECT symbol, detected_at, score_total, rejection_reason, notional, rsi_14
        FROM signal_evaluations
        WHERE detected_at > NOW() - INTERVAL '2 hours'
          AND score_total >= 10
          AND rsi_14 < 50
          AND rejection_reason = 'not uptrend'
        ORDER BY notional DESC
        LIMIT 15;
    """)
    rows = cur.fetchall()

    if rows:
        print(f"{'Symbol':<8} {'Detected At':<22} {'Score':<7} {'Rejection':<25} {'Notional':<15} {'RSI'}")
        print("-" * 90)
        for r in rows:
            symbol = r[0] or ''
            detected = str(r[1])[:19] if r[1] else ''
            score = f"{r[2]:.1f}" if r[2] else ''
            reason = (r[3] or '')[:24]
            notional = f"{r[4]:,.0f}" if r[4] else ''
            rsi = f"{r[5]:.1f}" if r[5] else ''
            print(f"{symbol:<8} {detected:<22} {score:<7} {reason:<25} {notional:<15} {rsi}")
    else:
        print("** No signals matching criteria (score>=10, RSI<50, failed on trend) **")

    print()
    print("=" * 110)
    print("Query 3: Signal counts - last 30 min vs earlier (2 hour window)")
    print("=" * 110)

    cur.execute("""
        SELECT
          CASE
            WHEN detected_at > NOW() - INTERVAL '30 minutes' THEN 'Last 30 min'
            ELSE 'Earlier'
          END as period,
          COUNT(*)
        FROM signal_evaluations
        WHERE detected_at > NOW() - INTERVAL '2 hours'
        GROUP BY 1;
    """)
    rows = cur.fetchall()

    if rows:
        for r in rows:
            print(f"{r[0]}: {r[1]} signals")
    else:
        print("** NO SIGNALS in last 2 hours! **")

    # Check timezone
    print()
    print("=" * 110)
    print("DIAGNOSTIC: Database timezone and current time")
    print("=" * 110)
    cur.execute("SELECT NOW(), CURRENT_TIMESTAMP, current_setting('TIMEZONE');")
    row = cur.fetchone()
    print(f"NOW(): {row[0]}")
    print(f"CURRENT_TIMESTAMP: {row[1]}")
    print(f"TIMEZONE: {row[2]}")

    # Additional diagnostic queries
    print()
    print("=" * 110)
    print("DIAGNOSTIC: Last 10 signals regardless of time")
    print("=" * 110)

    cur.execute("""
        SELECT symbol, detected_at, score_total, rejection_reason, notional
        FROM signal_evaluations
        ORDER BY detected_at DESC
        LIMIT 10;
    """)
    rows = cur.fetchall()
    if rows:
        print(f"{'Symbol':<8} {'Detected At':<25} {'Score':<7} {'Rejection':<30} {'Notional':<15}")
        print("-" * 100)
        for r in rows:
            symbol = r[0] or ''
            detected = str(r[1])[:22] if r[1] else ''
            score = f"{r[2]:.1f}" if r[2] else ''
            reason = (r[3] or '')[:29]
            notional = f"{r[4]:,.0f}" if r[4] else ''
            print(f"{symbol:<8} {detected:<25} {score:<7} {reason:<30} {notional:<15}")
    else:
        print("** NO SIGNALS AT ALL IN TABLE! **")

    print()
    print("=" * 110)
    print("DIAGNOSTIC: Count by day (last 7 days)")
    print("=" * 110)

    cur.execute("""
        SELECT DATE(detected_at) as day, COUNT(*) as cnt
        FROM signal_evaluations
        WHERE detected_at > NOW() - INTERVAL '7 days'
        GROUP BY 1
        ORDER BY 1 DESC;
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"{r[0]}: {r[1]} signals")
    else:
        print("** NO SIGNALS in last 7 days! **")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
