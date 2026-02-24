"""
Paper Trading Configuration

Entry Rules:
- Uptrend (price > 20d SMA at signal time)
- Score >= 10
- Prior-day RSI < 55 (S5)
- ADV >= 1,000 contracts/day (v57 — D-1 from orats_daily)
- GEX dead zone filter (skip 2-5% above gamma flip)
- $50K+ notional
- Max 10 concurrent positions

Exit Rules:
- Hold to market close (3:55 PM ET)
- -5% hard stop (v57 — widened from -2%; 3yr backtest Sharpe 1.25 vs 1.06)
"""

from dataclasses import dataclass
from datetime import time as dt_time


@dataclass
class TradingConfig:
    """Paper trading configuration."""

    # Entry filters
    SCORE_THRESHOLD: int = 10
    RSI_THRESHOLD: float = 55.0  # S5: raised from 50 — RSI<55 shows improving Sharpe YoY (1.59→3.23→3.27), RSI<50 degrading
    MIN_NOTIONAL: float = 50_000
    REQUIRE_UPTREND: bool = True

    # Sentiment filter (TEST-8)
    USE_SENTIMENT_FILTER: bool = True
    SENTIMENT_MAX_MENTIONS: int = 5  # Reject if mentions >= this (crowded trade)
    SENTIMENT_MIN_INDEX: float = 0.0  # Reject if sentiment < this (negative)

    # Earnings proximity filter (5.5)
    USE_EARNINGS_FILTER: bool = True
    EARNINGS_PROXIMITY_DAYS: int = 2  # Reject if earnings within +/- this many days

    # Call% filter (S4) — DISABLED by S5: gate blocked 99.6% of score>=10 signals (34/7,985 passed)
    USE_CALL_PCT_FILTER: bool = False
    CALL_PCT_MAX: float = 0.95  # value retained for reference; inactive while USE_CALL_PCT_FILTER=False

    # GEX dead-zone filter (S5+GEX) — skip signals where spot is 2-5% above gamma flip
    # Rationale: 3-year backtest shows Sharpe 0.91 in this band vs 2.54 baseline
    # Removing dead zone: Sharpe 3.30 -> 3.50, mean +0.655% -> +0.737%, -17% volume
    USE_GEX_DEAD_ZONE_FILTER: bool = True
    GEX_DEAD_ZONE_MIN_PCT: float = 2.0   # lower bound: spot > flip + 2%
    GEX_DEAD_ZONE_MAX_PCT: float = 5.0   # upper bound: spot < flip + 5%

    # Adaptive RSI — bounce-day relaxation (V29) — DISABLED by S4
    USE_ADAPTIVE_RSI: bool = False
    ADAPTIVE_RSI_THRESHOLD: float = 60.0  # RSI threshold on bounce days (normal = RSI_THRESHOLD)
    ADAPTIVE_RSI_MIN_RED_DAYS: int = 2    # Minimum consecutive red SPY closes for bounce day

    # Account B — Engulfing-Primary, V2 Score as Confirmation (A/B test)
    USE_ACCOUNT_B: bool = True
    ENGULFING_LOOKBACK_MINUTES: int = 30       # 5-min pattern fallback window
    ENGULFING_DAILY_LOOKBACK_HOURS: int = 20   # Daily patterns persist overnight

    # ADV filter (v57) — reject illiquid names (avg_daily_volume from orats_daily, D-1)
    # 3yr backtest: ADV>=1K Sharpe 1.25, WR 56.7%, PF 1.52 vs no-filter Sharpe 0.78
    USE_ADV_FILTER: bool = True
    MIN_ADV: int = 1000  # minimum avg_daily_volume (options contracts/day)

    # Market regime filter (V28)
    USE_MARKET_REGIME_FILTER: bool = True
    MARKET_REGIME_SYMBOL: str = "SPY"  # Benchmark to check
    MARKET_REGIME_MAX_DECLINE: float = -0.005  # -0.5% from open = pause entries

    # Stock WebSocket (PROD-1) — Alpaca SIP real-time stream
    # Upgraded to Algo Trader Plus plan: real-time SIP trades+quotes via wss://stream.data.alpaca.markets/v2/sip
    # Currently disabled — enable after live testing to replace 30s REST polling for hard stop detection.
    USE_STOCK_WEBSOCKET: bool = True  # Real-time SIP trades+quotes for event-driven hard stop detection
    WEBSOCKET_FALLBACK_TO_REST: bool = True  # Fall back to REST if WebSocket fails
    WEBSOCKET_MAX_RECONNECT_ATTEMPTS: int = 3  # Max reconnect attempts before fallback

    # Position limits
    MAX_CONCURRENT_POSITIONS: int = 10
    MAX_POSITION_SIZE_PCT: float = 0.10  # 10% of portfolio per trade

    # Exit rules
    EXIT_TIME: dt_time = dt_time(15, 55)  # 3:55 PM ET
    LAST_ENTRY_TIME: dt_time = dt_time(15, 50)  # No new positions after 3:50 PM
    HARD_STOP_PCT: float = -0.05  # -5% hard stop — 3yr backtest: -5% Sharpe 1.25 vs -2% Sharpe 1.06 at ADV>=1K
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

    # Intraday bar collection
    COLLECT_INTRADAY_BARS: bool = True
    INTRADAY_BARS_MAX_BATCHES: int = 20      # 20 × 100 = 2,000 symbols (full market)
    INTRADAY_BARS_INTERVAL_SEC: int = 60     # collect every 60 seconds
    INTRADAY_BARS_RETENTION_DAYS: int = 21   # 21 calendar days ≈ 14 trading days

    # Logging
    LOG_FILE: str = "paper_trading.log"
    LOG_TRADES: bool = True


# Default config instance
DEFAULT_CONFIG = TradingConfig()
