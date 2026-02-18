#!/usr/bin/env python3
"""
Update Spot Prices (Standalone)

Fetches latest stock prices from Alpaca (with yfinance fallback) and updates spot_prices table.
This is a standalone version for FL3_V2, independent of V1 CLI.

Usage:
    python scripts/update_spot_prices.py
    python scripts/update_spot_prices.py --only AAPL,MSFT,TSLA

Environment Variables:
    DATABASE_URL: PostgreSQL connection string (required)
    ALPACA_API_KEY: Alpaca API key (optional, for Alpaca pricing)
    ALPACA_SECRET_KEY: Alpaca secret key (optional, for Alpaca pricing)
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_tracked_symbols_v2(conn) -> List[str]:
    """Get active symbols from tracked_tickers_v2 table."""
    with conn.cursor() as cur:
        # Try V2 table first
        try:
            cur.execute("""
                SELECT symbol FROM tracked_tickers_v2
                WHERE ta_enabled = TRUE
                ORDER BY symbol ASC
            """)
            rows = cur.fetchall()
            if rows:
                return [r[0].upper() for r in rows]
        except Exception:
            pass

        # Fallback to V1 table
        try:
            cur.execute("""
                SELECT ticker FROM tracked_tickers
                WHERE removed_at IS NULL
                ORDER BY ticker ASC
            """)
            rows = cur.fetchall()
            return [r[0].upper() for r in rows]
        except Exception as e:
            logger.error(f"Failed to get tracked tickers: {e}")
            return []


def fetch_alpaca_batch(symbols: List[str]) -> Dict[str, Tuple[Optional[float], str]]:
    """Fetch latest prices from Alpaca in batch."""
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        logger.warning("Alpaca credentials not set, skipping Alpaca fetch")
        return {}

    results = {}
    # Alpaca allows up to 1000 symbols per request
    batch_size = 1000

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        symbols_param = ",".join(batch)

        try:
            url = "https://data.alpaca.markets/v2/stocks/trades/latest"
            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
            resp = requests.get(
                url,
                headers=headers,
                params={"symbols": symbols_param, "feed": "iex"},
                timeout=30
            )

            if resp.status_code == 200:
                data = resp.json()
                trades = data.get("trades", {})
                for symbol, trade in trades.items():
                    price = trade.get("p")
                    if price is not None and price > 0:
                        results[symbol.upper()] = (float(price), "alpaca:trade")
            else:
                logger.warning(f"Alpaca batch request failed: {resp.status_code}")

        except Exception as e:
            logger.warning(f"Alpaca batch fetch error: {e}")

    return results


def fetch_yfinance_price(symbol: str) -> Optional[Tuple[float, str]]:
    """Fetch latest price from yfinance."""
    if not HAS_YFINANCE:
        return None

    try:
        # Convert ticker format (BRK.B -> BRK-B for yfinance)
        yf_symbol = symbol.replace(".", "-").upper()
        ticker = yf.Ticker(yf_symbol)

        # Try fast_info first
        try:
            fi = ticker.fast_info
            price = getattr(fi, "last_price", None)
            if price is not None and price > 0:
                return (float(price), "yfinance:fast_info")
        except Exception:
            pass

        # Fallback to history
        hist = ticker.history(period="2d", interval="1d")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 0:
                return (price, "yfinance:history")

    except Exception as e:
        logger.debug(f"yfinance error for {symbol}: {e}")

    return None


def ensure_spot_prices_table(conn):
    """Ensure spot_prices table exists with required columns."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.spot_prices (
                id BIGSERIAL PRIMARY KEY,
                ticker TEXT NOT NULL,
                trade_date DATE NOT NULL,
                underlying DOUBLE PRECISION,
                currency TEXT DEFAULT 'USD',
                source TEXT,
                inserted_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # Add unique constraint if not exists
        try:
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_spot_prices_ticker_trade_date
                ON public.spot_prices (ticker, trade_date);
            """)
        except Exception:
            pass

    conn.commit()


def upsert_spot_price(conn, ticker: str, trade_date, price: float, source: str):
    """Insert or update spot price."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO spot_prices (ticker, trade_date, underlying, currency, source, inserted_at)
            VALUES (%s, %s, %s, 'USD', %s, NOW())
            ON CONFLICT (ticker, trade_date) DO UPDATE SET
                underlying = EXCLUDED.underlying,
                source = EXCLUDED.source,
                inserted_at = NOW()
        """, (ticker, trade_date, price, source))


def main():
    import psycopg2
    from zoneinfo import ZoneInfo

    parser = argparse.ArgumentParser(description="Update spot prices")
    parser.add_argument("--only", type=str, help="Comma-separated symbols to update")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't persist")
    parser.add_argument("--skip-alpaca", action="store_true", help="Skip Alpaca, use yfinance only")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(db_url)

    try:
        # Ensure table exists
        if not args.dry_run:
            ensure_spot_prices_table(conn)

        # Get symbols
        if args.only:
            symbols = [s.strip().upper() for s in args.only.split(",") if s.strip()]
        else:
            symbols = get_tracked_symbols_v2(conn)

        if not symbols:
            logger.error("No symbols to update")
            return 1

        logger.info(f"Updating spot prices for {len(symbols)} symbols")

        # Get current date in ET
        et = ZoneInfo("America/New_York")
        trade_date = datetime.now(et).date()

        # Fetch from Alpaca first (batch)
        alpaca_results = {}
        if not args.skip_alpaca:
            alpaca_results = fetch_alpaca_batch(symbols)
            logger.info(f"Alpaca returned prices for {len(alpaca_results)} symbols")

        # Track results
        updated = 0
        failed = []

        for symbol in symbols:
            price = None
            source = None

            # Check Alpaca result
            if symbol in alpaca_results:
                price, source = alpaca_results[symbol]

            # Fallback to yfinance
            if price is None and HAS_YFINANCE:
                result = fetch_yfinance_price(symbol)
                if result:
                    price, source = result

            if price is not None and price > 0:
                if args.dry_run:
                    logger.info(f"  {symbol}: ${price:.2f} ({source})")
                else:
                    upsert_spot_price(conn, symbol, trade_date, price, source)
                updated += 1
            else:
                failed.append(symbol)

        if not args.dry_run:
            conn.commit()

        logger.info(f"Updated {updated}/{len(symbols)} spot prices")

        if failed:
            logger.warning(f"Failed to get prices for {len(failed)} symbols: {', '.join(failed[:20])}")
            if len(failed) > 20:
                logger.warning(f"  ... and {len(failed) - 20} more")

        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        conn.rollback()
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
