#!/usr/bin/env python3
"""Debug earnings calendar lookup."""

import asyncio
import os
import sys
import asyncpg


async def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        # Check earnings calendar schema
        print("EARNINGS CALENDAR SCHEMA:")
        print("=" * 60)
        columns = await pool.fetch("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'earnings_calendar'
            ORDER BY ordinal_position
        """)
        for c in columns:
            print(f"  {c['column_name']:<25} {c['data_type']}")

        # Check for specific symbols
        print("\n\nCHECK SPECIFIC SYMBOLS (IBM, SBUX, WDC, CMCSA):")
        print("=" * 60)
        test_symbols = ['IBM', 'SBUX', 'WDC', 'CMCSA', 'AAOI', 'UUUU']

        for symbol in test_symbols:
            rows = await pool.fetch("""
                SELECT symbol, event_date, hour, is_current
                FROM earnings_calendar
                WHERE symbol = $1
                ORDER BY event_date DESC
                LIMIT 5
            """, symbol)

            print(f"\n{symbol}:")
            if rows:
                for r in rows:
                    print(f"  {r['event_date']} hour={r['hour']} is_current={r['is_current']}")
            else:
                print("  (not found)")

        # Check recent earnings (around today)
        print("\n\nEARNINGS AROUND TODAY:")
        print("=" * 60)
        recent = await pool.fetch("""
            SELECT symbol, event_date, hour, is_current,
                   event_date - CURRENT_DATE as days_until
            FROM earnings_calendar
            WHERE event_date BETWEEN CURRENT_DATE - 3 AND CURRENT_DATE + 3
              AND is_current = true
            ORDER BY event_date, symbol
            LIMIT 30
        """)

        print(f"Found {len(recent)} earnings in +/- 3 day window:")
        for r in recent[:30]:
            print(f"  {r['symbol']:<8} {r['event_date']} days_until={r['days_until']} "
                  f"is_current={r['is_current']}")

        # Check if case sensitivity is an issue
        print("\n\nCASE SENSITIVITY CHECK:")
        print("=" * 60)
        ibm_check = await pool.fetch("""
            SELECT DISTINCT symbol FROM earnings_calendar
            WHERE UPPER(symbol) = 'IBM'
        """)
        print(f"IBM variations in earnings_calendar: {[r['symbol'] for r in ibm_check]}")

        # Check current_date
        print("\n\nDATABASE CURRENT_DATE:")
        print("=" * 60)
        current = await pool.fetchval("SELECT CURRENT_DATE")
        print(f"  CURRENT_DATE = {current}")

        # Check if the UOA candidates match earnings
        print("\n\nTOP UOA CANDIDATES vs EARNINGS:")
        print("=" * 60)
        top_uoa = ['VTEB', 'IBM', 'CLS', 'AAOI', 'UUUU', 'WDC', 'SBUX', 'TSCO', 'PLRX', 'UGL']

        for symbol in top_uoa:
            earnings = await pool.fetchrow("""
                SELECT event_date, hour, is_current,
                       event_date - CURRENT_DATE as days_until
                FROM earnings_calendar
                WHERE symbol = $1
                  AND event_date BETWEEN CURRENT_DATE - 3 AND CURRENT_DATE + 3
                  AND is_current = true
                ORDER BY ABS(event_date - CURRENT_DATE)
                LIMIT 1
            """, symbol)

            if earnings:
                print(f"  {symbol:<8} EARNINGS on {earnings['event_date']} "
                      f"(days_until={earnings['days_until']}) is_current={earnings['is_current']}")
            else:
                print(f"  {symbol:<8} NO EARNINGS in +/- 3 days")

    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
