"""
Paper Trading Configuration

Entry Rules:
- Uptrend (price > 20d SMA at signal time)
- Score >= 10
- Prior-day RSI < 50 (adaptive: RSI < 60 on bounce-back days -- V29)
- $50K+ notional
- Max 10 concurrent positions

Exit Rules:
- Hold to market close (3:55 PM ET)
- Optional: -5% hard stop (disaster protection)
"""

from dataclasses import dataclass
from datetime import time as dt_time


@dataclass
class TradingConfig:
    """Paper trading configuration."""

    # Entry filters
    SCORE_THRESHOLD: int = 10
    RSI_THRESHOLD: float = 50.0
    MIN_NOTIONAL: float = 50_000
    REQUIRE_UPTREND: bool = True

    # Sentiment filter (TEST-8)
    USE_SENTIMENT_FILTER: bool = True
    SENTIMENT_MAX_MENTIONS: int = 5  # Reject if mentions >= this (crowded trade)
    SENTIMENT_MIN_INDEX: float = 0.0  # Reject if sentiment < this (negative)

    # Earnings proximity filter (5.5)
    USE_EARNINGS_FILTER: bool = True
    EARNINGS_PROXIMITY_DAYS: int = 2  # Reject if earnings within +/- this many days

    # Adaptive RSI — bounce-day relaxation (V29)
    USE_ADAPTIVE_RSI: bool = True
    ADAPTIVE_RSI_THRESHOLD: float = 60.0  # RSI threshold on bounce days (normal = RSI_THRESHOLD)
    ADAPTIVE_RSI_MIN_RED_DAYS: int = 2    # Minimum consecutive red SPY closes for bounce day

    # Market regime filter (V28)
    USE_MARKET_REGIME_FILTER: bool = True
    MARKET_REGIME_SYMBOL: str = "SPY"  # Benchmark to check
    MARKET_REGIME_MAX_DECLINE: float = -0.005  # -0.5% from open = pause entries

    # Stock WebSocket (PROD-1)
    # NOTE: Polygon stocks WebSocket provides 15-min delayed data on Stocks Starter plan.
    # Options Advanced plan provides real-time data (used for T.* firehose).
    # Stock WebSocket disabled - using Alpaca REST API for real-time position monitoring instead.
    USE_STOCK_WEBSOCKET: bool = False  # Disabled due to 15-min delay on Starter plan
    WEBSOCKET_FALLBACK_TO_REST: bool = True  # Fall back to REST if WebSocket fails
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 3  # Max reconnect attempts before fallback

    # Position limits
    MAX_CONCURRENT_POSITIONS: int = 10
    MAX_POSITION_SIZE_PCT: float = 0.10  # 10% of portfolio per trade

    # Exit rules
    EXIT_TIME: dt_time = dt_time(15, 55)  # 3:55 PM ET
    LAST_ENTRY_TIME: dt_time = dt_time(15, 50)  # No new positions after 3:50 PM
    HARD_STOP_PCT: float = -0.05  # -5% hard stop (optional)
    USE_HARD_STOP: bool = True

    # Market hours (ET)
    MARKET_OPEN: dt_time = dt_time(9, 30)
    MARKET_CLOSE: dt_time = dt_time(16, 0)
    PRE_MARKET_START: dt_time = dt_time(4, 0)

    # Timing
    SIGNAL_CHECK_INTERVAL_SEC: int = 60  # Check for signals every minute
    POSITION_CHECK_INTERVAL_SEC: int = 30  # Check positions every 30s

    # Alpaca
    ALPACA_PAPER_URL: str = "https://paper-api.alpaca.markets"
    ALPACA_DATA_URL: str = "https://data.alpaca.markets/v2"

    # Logging
    LOG_FILE: str = "paper_trading.log"
    LOG_TRADES: bool = True


# Default config instance
DEFAULT_CONFIG = TradingConfig()
