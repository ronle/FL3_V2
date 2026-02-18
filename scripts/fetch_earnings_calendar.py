#!/usr/bin/env python3
"""
Fetch Earnings Calendar (Standalone)

Fetches earnings calendar from FMP (Financial Modeling Prep) and persists to PostgreSQL.
This is a standalone version for FL3_V2, independent of V1 CLI.

Usage:
    python scripts/fetch_earnings_calendar.py
    python scripts/fetch_earnings_calendar.py --days 60

Environment Variables:
    FMP_API_KEY: FMP API key (required)
    DATABASE_URL: PostgreSQL connection string (required for persistence)
"""

import os
import sys
import json
import logging
import argparse
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# FMP API configuration
FMP_BASE_URL = "https://financialmodelingprep.com/stable"


def fetch_fmp_earnings(
    from_date: str,
    to_date: str,
    api_key: str,
    timeout: float = 30.0
) -> List[Dict[str, Any]]:
    """Fetch earnings calendar from FMP API."""
    url = f"{FMP_BASE_URL}/earnings-calendar"
    params = {
        "from": from_date,
        "to": to_date,
        "apikey": api_key
    }

    resp = requests.get(url, params=params, timeout=timeout)

    if resp.status_code == 429:
        raise RuntimeError("FMP rate limit (429). Reduce frequency or check plan limits.")
    if resp.status_code == 401:
        raise RuntimeError("FMP unauthorized (401). Check FMP_API_KEY.")
    resp.raise_for_status()

    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"Invalid JSON from FMP: {e}")

    # FMP returns a list directly
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        # Handle potential wrapper formats
        for key in ("data", "earnings", "earningsCalendar"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    return []


def coerce_num(v: Any) -> Optional[float]:
    """Safely convert value to float."""
    try:
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() == "null":
            return None
        return float(s)
    except Exception:
        return None


def derive_quarter(event_date: str) -> Optional[int]:
    """Derive fiscal quarter from event date (approximate)."""
    try:
        month = int(event_date.split("-")[1])
        # Q1: Jan-Mar, Q2: Apr-Jun, Q3: Jul-Sep, Q4: Oct-Dec
        return ((month - 1) // 3) + 1
    except Exception:
        return None


def persist_to_db(items: List[Dict[str, Any]], provider: str = "fmp") -> Dict[str, int]:
    """Persist earnings calendar items to PostgreSQL."""
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    # Ensure table exists
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS public.earnings_calendar (
        id                  BIGSERIAL PRIMARY KEY,
        provider            TEXT NOT NULL,
        symbol              TEXT NOT NULL,
        event_date          DATE NOT NULL,
        hour                TEXT,
        quarter             INTEGER,
        year                INTEGER,
        eps_estimate        DOUBLE PRECISION,
        eps_actual          DOUBLE PRECISION,
        revenue_estimate    DOUBLE PRECISION,
        revenue_actual      DOUBLE PRECISION,
        raw                 JSONB,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_current          BOOLEAN NOT NULL DEFAULT TRUE,
        UNIQUE (provider, symbol, event_date)
    );
    """

    upsert_sql = """
    INSERT INTO public.earnings_calendar (
        provider, symbol, event_date, hour, quarter, year,
        eps_estimate, eps_actual, revenue_estimate, revenue_actual, raw, is_current
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE
    )
    ON CONFLICT (provider, symbol, event_date) DO UPDATE SET
        hour = EXCLUDED.hour,
        quarter = EXCLUDED.quarter,
        year = EXCLUDED.year,
        eps_estimate = EXCLUDED.eps_estimate,
        eps_actual = EXCLUDED.eps_actual,
        revenue_estimate = EXCLUDED.revenue_estimate,
        revenue_actual = EXCLUDED.revenue_actual,
        raw = EXCLUDED.raw,
        is_current = TRUE,
        updated_at = NOW();
    """

    # Build rows - FMP field mapping differs from Finnhub
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue

        # FMP uses 'date' field
        d = (it.get("date") or it.get("eventDate") or "").strip()
        sym = (it.get("symbol") or it.get("ticker") or "").strip().upper()
        if not d or not sym:
            continue

        # FMP doesn't provide hour, quarter directly - derive quarter from date
        hour = it.get("hour")  # Usually None for FMP
        q = it.get("quarter") or derive_quarter(d)
        y = it.get("year")

        # Try to extract year from date if not provided
        if not y:
            try:
                y = int(d.split("-")[0])
            except Exception:
                y = None

        # FMP uses 'epsEstimated' and 'revenueEstimated' (with 'd')
        # Also check Finnhub-style field names for compatibility
        eps_est = coerce_num(it.get("epsEstimated") or it.get("epsEstimate"))
        eps_act = coerce_num(it.get("epsActual"))
        rev_est = coerce_num(it.get("revenueEstimated") or it.get("revenueEstimate"))
        rev_act = coerce_num(it.get("revenueActual"))

        rows.append((
            provider,
            sym,
            d,
            hour,
            int(q) if q is not None else None,
            int(y) if y is not None else None,
            eps_est,
            eps_act,
            rev_est,
            rev_act,
            json.dumps(it),
        ))

    if not rows:
        return {"upserts": 0}

    upserts = 0
    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(create_table_sql)
            for row in rows:
                cur.execute(upsert_sql, row)
                upserts += 1

            # Mark old entries as not current
            cur.execute("""
                UPDATE public.earnings_calendar
                SET is_current = FALSE, updated_at = NOW()
                WHERE is_current = TRUE
                  AND event_date < CURRENT_DATE
            """)
        conn.commit()
    finally:
        conn.close()

    return {"upserts": upserts}


def main():
    parser = argparse.ArgumentParser(description="Fetch earnings calendar from FMP")
    parser.add_argument("--days", type=int, default=31, help="Days ahead to fetch (default 31)")
    parser.add_argument("--from-date", dest="from_date", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", dest="to_date", type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't persist")
    args = parser.parse_args()

    # Get API key - FMP
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        logger.error("FMP_API_KEY not set")
        sys.exit(1)

    # Calculate date range
    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        today = date.today()
        from_date = today.isoformat()
        to_date = (today + timedelta(days=args.days)).isoformat()

    logger.info(f"Fetching earnings calendar from {from_date} to {to_date} (provider: FMP)")

    try:
        items = fetch_fmp_earnings(from_date, to_date, api_key)
        logger.info(f"Fetched {len(items)} earnings events from FMP")

        if args.dry_run:
            logger.info("Dry run - not persisting to database")
            for it in items[:10]:
                logger.info(f"  {it.get('symbol')}: {it.get('date')}")
            if len(items) > 10:
                logger.info(f"  ... and {len(items) - 10} more")
        else:
            result = persist_to_db(items, provider="fmp")
            logger.info(f"Persisted {result['upserts']} earnings events to database")

        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
