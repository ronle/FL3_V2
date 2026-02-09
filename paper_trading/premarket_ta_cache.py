"""
Pre-Market TA Cache Generator

Runs daily before market open to generate prior-day TA data
for all potentially tradable symbols.

Output: polygon_data/daily_ta_cache.json

Usage:
    python -m paper_trading.premarket_ta_cache
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.alpaca_bars_batch import AlpacaBarsFetcher

ET = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Symbols to track (can be expanded)
# Start with high-volume options tickers
DEFAULT_SYMBOLS = [
    # Mega caps
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA",
    # Tech
    "AMD", "INTC", "MU", "QCOM", "AVGO", "ORCL", "CRM", "ADBE",
    # Financials
    "JPM", "BAC", "GS", "MS", "C", "WFC", "BRK.B", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "MRK", "LLY", "BMY",
    # Consumer
    "WMT", "COST", "HD", "NKE", "SBUX", "MCD", "DIS",
    # Energy
    "XOM", "CVX", "COP", "SLB", "OXY",
    # ETFs
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK",
    # High beta / meme
    "GME", "AMC", "RIVN", "LCID", "PLTR", "SOFI", "COIN", "HOOD",
    # Others with high options activity
    "BA", "CAT", "GE", "F", "GM", "UBER", "LYFT", "ABNB",
    "SNAP", "PINS", "ROKU", "SQ", "PYPL", "SHOP",
    "ZM", "DOCU", "CRWD", "NET", "DDOG", "SNOW",
    "NFLX", "SPOT", "RBLX", "U",
]


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Calculate RSI from closing prices."""
    if len(closes) < period + 1:
        return None

    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    recent = changes[-period:]

    gains = [c if c > 0 else 0 for c in recent]
    losses = [-c if c < 0 else 0 for c in recent]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """Calculate EMA."""
    if len(prices) < period:
        return None

    ema = sum(prices[:period]) / period
    k = 2 / (period + 1)

    for price in prices[period:]:
        ema = price * k + ema * (1 - k)

    return round(ema, 4)


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """Calculate SMA."""
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 4)


def calculate_macd(closes: List[float]) -> tuple:
    """Calculate MACD(12, 26, 9). Returns (line, signal, histogram)."""
    if len(closes) < 35:
        return None, None, None

    ema_12 = calculate_ema(closes, 12)
    ema_26 = calculate_ema(closes, 26)

    if ema_12 is None or ema_26 is None:
        return None, None, None

    macd_line = ema_12 - ema_26

    # Calculate MACD history for signal line
    macd_values = []
    for i in range(26, len(closes) + 1):
        e12 = calculate_ema(closes[:i], 12)
        e26 = calculate_ema(closes[:i], 26)
        if e12 and e26:
            macd_values.append(e12 - e26)

    if len(macd_values) < 9:
        return round(macd_line, 4), None, None

    signal_line = calculate_ema(macd_values, 9)
    histogram = macd_line - signal_line if signal_line else None

    return (
        round(macd_line, 4),
        round(signal_line, 4) if signal_line else None,
        round(histogram, 4) if histogram else None
    )


async def generate_ta_cache(
    symbols: List[str] = None,
    alpaca_key: str = None,
    alpaca_secret: str = None,
) -> Dict:
    """
    Generate TA cache for all symbols.

    Returns dict: {symbol: {rsi_14, macd_hist, sma_20, trend, last_close}}
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    if not alpaca_key or not alpaca_secret:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        return {}

    fetcher = AlpacaBarsFetcher(alpaca_key, alpaca_secret)
    ta_cache = {}

    logger.info(f"Generating TA cache for {len(symbols)} symbols...")

    try:
        # Fetch daily bars (need 70 days for 50d SMA)
        # Explicit start date required — without it Alpaca returns no historical bars
        # Use naive UTC datetime to avoid double-timezone in adapter's isoformat() + "Z"
        from datetime import timezone
        start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)

        # Try SIP feed first (full market coverage), fallback to IEX if SIP unavailable
        # SIP requires Alpaca paid data subscription
        feed = "sip"
        logger.info(f"Fetching bars from Alpaca ({feed.upper()} feed) for {len(symbols)} symbols (since {start_date.date()})...")
        bars_data = await fetcher.get_bars_batch(
            symbols=symbols,
            timeframe="1Day",
            limit=70,  # Extended for 50d SMA calculation
            start=start_date,
            feed=feed,
        )
        logger.info(f"Alpaca returned data for {len(bars_data)} symbols")

        # Log symbols with no/insufficient data for debugging
        symbols_with_data = sum(1 for d in bars_data.values() if d.bars and len(d.bars) >= 15)
        symbols_no_data = [s for s, d in bars_data.items() if not d.bars or len(d.bars) == 0]
        symbols_partial = [s for s, d in bars_data.items() if d.bars and 0 < len(d.bars) < 15]
        logger.info(f"Symbols with sufficient data (>=15 bars): {symbols_with_data}")
        if symbols_no_data:
            logger.warning(f"Symbols with no data: {len(symbols_no_data)} (first 10: {symbols_no_data[:10]})")
        if symbols_partial:
            logger.warning(f"Symbols with partial data (<15 bars): {len(symbols_partial)} (first 10: {symbols_partial[:10]})")

        for symbol, bar_data in bars_data.items():
            if not bar_data.bars or len(bar_data.bars) < 15:
                logger.debug(f"Insufficient data for {symbol}")
                continue

            closes = [b.close for b in bar_data.bars]

            # Calculate indicators
            rsi_14 = calculate_rsi(closes, 14)
            macd_line, macd_signal, macd_hist = calculate_macd(closes)
            sma_20 = calculate_sma(closes, 20)
            sma_50 = calculate_sma(closes, 50)

            # Determine trend (price vs SMA-20)
            last_close = closes[-1] if closes else None
            trend = None
            if last_close and sma_20:
                trend = 1 if last_close > sma_20 else -1

            ta_cache[symbol] = {
                "rsi_14": rsi_14,
                "macd_line": macd_line,
                "macd_signal": macd_signal,
                "macd_hist": macd_hist,
                "sma_20": sma_20,
                "sma_50": sma_50,
                "last_close": last_close,
                "trend": trend,
                "updated": datetime.now(ET).isoformat(),
            }

        logger.info(f"Generated TA for {len(ta_cache)} symbols")

    finally:
        await fetcher.close()

    return ta_cache


def get_tracked_symbols(database_url: str) -> List[str]:
    """Fetch tracked symbols from tracked_tickers_v2."""
    try:
        import psycopg2

        conn = psycopg2.connect(database_url.strip())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol FROM tracked_tickers_v2 WHERE ta_enabled = TRUE"
            )
            symbols = [row[0] for row in cur.fetchall()]
        conn.close()
        logger.info(f"Loaded {len(symbols)} tracked symbols from DB")
        return symbols
    except Exception as e:
        logger.warning(f"Failed to load tracked symbols: {e}")
        return []


async def save_to_database(ta_cache: Dict) -> bool:
    """Save TA cache to database."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.warning("DATABASE_URL not set, skipping database save")
        return False

    try:
        import psycopg2
        from psycopg2.extras import execute_values

        database_url = database_url.strip()
        conn = psycopg2.connect(database_url)
        logger.info("Connected to database")

        today = datetime.now(ET).date().isoformat()

        # Prepare rows
        rows = []
        for symbol, ta in ta_cache.items():
            rows.append((
                symbol,
                today,
                ta.get("rsi_14"),
                ta.get("macd_line"),
                ta.get("macd_signal"),
                ta.get("macd_hist"),
                ta.get("sma_20"),
                None,  # ema_9 not calculated in this version
                ta.get("last_close"),
                ta.get("sma_50"),
            ))

        # Upsert to database
        sql = """
            INSERT INTO ta_daily_close
            (symbol, trade_date, rsi_14, macd, macd_signal, macd_histogram, sma_20, ema_9, close_price, sma_50)
            VALUES %s
            ON CONFLICT (symbol, trade_date)
            DO UPDATE SET
                rsi_14 = EXCLUDED.rsi_14,
                macd = EXCLUDED.macd,
                macd_signal = EXCLUDED.macd_signal,
                macd_histogram = EXCLUDED.macd_histogram,
                sma_20 = EXCLUDED.sma_20,
                close_price = EXCLUDED.close_price,
                sma_50 = EXCLUDED.sma_50
        """

        with conn.cursor() as cur:
            execute_values(cur, sql, rows, page_size=100)
        conn.commit()
        conn.close()

        logger.info(f"Saved {len(rows)} TA records to database")
        return True

    except Exception as e:
        logger.error(f"Database save failed: {e}")
        return False


async def main():
    """Generate and save TA cache."""
    logger.info("=" * 60)
    logger.info("Pre-Market TA Cache Generator")
    logger.info(f"Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')} ET")
    logger.info("=" * 60)

    alpaca_key = os.environ.get("ALPACA_API_KEY")
    alpaca_secret = os.environ.get("ALPACA_SECRET_KEY")

    if not alpaca_key or not alpaca_secret:
        logger.error("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        return

    # Build symbol list: tracked_tickers_v2 ∪ DEFAULT_SYMBOLS
    database_url = os.environ.get("DATABASE_URL", "")
    tracked = get_tracked_symbols(database_url) if database_url else []
    symbols = sorted(set(tracked) | set(DEFAULT_SYMBOLS))
    logger.info(
        f"Symbol sources: {len(tracked)} tracked + {len(DEFAULT_SYMBOLS)} default "
        f"= {len(symbols)} merged"
    )

    # Generate cache
    ta_cache = await generate_ta_cache(
        symbols=symbols,
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
    )

    if not ta_cache:
        logger.error("Failed to generate TA cache")
        return

    # Save to database (if DATABASE_URL is set)
    await save_to_database(ta_cache)

    # Save to file (for local fallback)
    try:
        output_dir = Path(__file__).parent.parent / "polygon_data"
        output_dir.mkdir(exist_ok=True)

        output_file = output_dir / "daily_ta_cache.json"

        output_data = {
            "generated": datetime.now(ET).isoformat(),
            "symbol_count": len(ta_cache),
            "ta_data": ta_cache,
        }

        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=2)

        logger.info(f"TA cache saved to file: {output_file}")
    except Exception as e:
        logger.warning(f"File save failed (OK if running in container): {e}")

    # Print summary
    rsi_under_50 = sum(1 for t in ta_cache.values() if t.get("rsi_14") and t["rsi_14"] < 50)
    uptrend = sum(1 for t in ta_cache.values() if t.get("trend") == 1)

    logger.info(f"Summary:")
    logger.info(f"  Total symbols: {len(ta_cache)}")
    logger.info(f"  RSI < 50: {rsi_under_50}")
    logger.info(f"  Uptrend: {uptrend}")
    logger.info(f"  RSI < 50 AND Uptrend: {sum(1 for t in ta_cache.values() if t.get('rsi_14') and t['rsi_14'] < 50 and t.get('trend') == 1)}")


if __name__ == "__main__":
    asyncio.run(main())
