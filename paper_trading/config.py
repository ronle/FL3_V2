"""
Paper Trading Configuration

Account A (v58 — Momentum EOD Screener):
- 3:50 PM: Screen orats_daily for momentum < -10%, $10+, ADV>=1K
- 3:55 PM: Close prior-day positions (D+1 exit)
- 3:56 PM: Buy top 10 most beaten-down, hold overnight
- Next day: -3% hard stop intraday, close at 3:55 PM (D+1 exit)
- V6 research: Sharpe 1.03 (minute bars, slippage, 3/3 years+, t=5.57)

Account B (big-hitter pattern trader):
- Poll engulfing_scores for 5min patterns every 30s
- Big-hitter filters: candle_range <= 0.57, risk >= $1/share, volume confirmed
- Limit order entry at pattern's entry_price, cancel after 30min if unfilled
- Exit at stop_loss (market), target_1 (market), or EOD 3:55 PM
- No new orders after 11 AM ET (v73: 3yr backtest shows morning +$17K, afternoon -$209)
- Supports both long and short (direction from pattern)

Legacy (UOA signals, disabled by default):
- Uptrend, Score >= 10, RSI < 55, ADV >= 1K, $50K+ notional
- Same-day exit at 3:55 PM
"""

from dataclasses import dataclass
from datetime import time as dt_time


@dataclass
class TradingConfig:
    """Paper trading configuration."""

    # v58: Momentum EOD Screener (Account A)
    USE_MOMENTUM_SCREENER: bool = False    # PAUSED v73: -$9.9K in 3 days (20 trades, 5% WR). Catching falling knives in down market.
    USE_UOA_SIGNALS: bool = False          # Disable UOA-based Account A entries
    MOMENTUM_THRESHOLD: float = -0.10      # price_momentum_20d cutoff (< -10%)
    MOMENTUM_PRICE_FLOOR: float = 10.0     # Min stock price
    MOMENTUM_SCREEN_TIME: dt_time = dt_time(15, 50)  # 3:50 PM ET — run screen
    MOMENTUM_BUY_TIME: dt_time = dt_time(15, 56)     # 3:56 PM ET — submit buys
    MOMENTUM_MAX_CANDIDATES: int = 10      # Max stocks to buy per day

    # Entry filters (legacy UOA path — active when USE_UOA_SIGNALS=True)
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

    # Account B — Big-Hitter Pattern Trader
    USE_ACCOUNT_B: bool = True
    ACCOUNT_B_POLL_INTERVAL_SEC: int = 30         # Poll engulfing_scores every 30s
    ACCOUNT_B_MAX_RISK_PER_TRADE: float = 500.0   # Max $ risk per trade
    ACCOUNT_B_MAX_CANDLE_RANGE: float = 0.57       # Max candle range (high-low) for big-hitter
    ACCOUNT_B_MIN_RISK_PER_SHARE: float = 1.00     # Min distance entry→stop (avoid tiny stops)
    ACCOUNT_B_CONFIRMATION_WINDOW_MIN: int = 30    # Cancel unfilled limit orders after this
    ACCOUNT_B_LOOKBACK_MIN: int = 10               # Only patterns from last N minutes
    ACCOUNT_B_FIRST_ENTRY_TIME: dt_time = dt_time(9, 35)  # No entries before 9:35 AM ET (v79: open-bar noise buffer; live data 39% WR at 9am vs 58% at 10am)
    ACCOUNT_B_LAST_ENTRY_TIME: dt_time = dt_time(11, 0)   # No entries after 11 AM ET (v73: 3yr backtest morning=+$17K, afternoon=-$209)

    # Account B — TA Tier Filters (v78→v80: disabled)
    # v78 added RSI/SMA gates based on 99-trade sample. Fail-open bug meant they never ran
    # on any of 246 live trades (Feb-Mar 2026). v79 made fail-closed but that blocks trades
    # on an unvalidated filter. v80: disabled gates, keep RSI stamping for future analysis.
    ACCOUNT_B_FILTER_WEAK: bool = True              # Reject pattern_strength='weak' (-$54/trade avg)
    ACCOUNT_B_REQUIRE_MOMENTUM_RSI: bool = False     # DISABLED v80: never validated live
    ACCOUNT_B_RSI_BULL_MIN: float = 55.0             # Min RSI for bullish entries (unused when disabled)
    ACCOUNT_B_RSI_BEAR_MAX: float = 45.0             # Max RSI for bearish entries (unused when disabled)
    ACCOUNT_B_REQUIRE_TREND_ALIGNMENT: bool = False   # DISABLED v80: never validated live

    # Account C — Cameron B2 Pattern Trader
    USE_ACCOUNT_C: bool = True
    CAMERON_RVOL_MIN: float = 10.0                    # Min relative volume for candidates
    CAMERON_SCAN_START: dt_time = dt_time(9, 45)      # Start scanning at 9:45 AM ET
    CAMERON_SCAN_END: dt_time = dt_time(11, 0)        # Stop scanning at 11:00 AM ET
    CAMERON_POLL_INTERVAL_SEC: int = 30               # Poll cameron_scores every 30s
    CAMERON_SCAN_INTERVAL_SEC: int = 60               # Run scanner every 60s
    CAMERON_MAX_BF_PER_DAY: int = 1                   # Max bull flag trades per day
    CAMERON_MAX_RISK_PER_TRADE: float = 500.0         # Max dollar risk per trade
    CAMERON_MAX_POSITIONS: int = 5                    # Max concurrent Cameron positions
    CAMERON_CONFIRMATION_WINDOW_MIN: int = 30         # Cancel unfilled limit orders after this

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
    HARD_STOP_PCT: float = -0.03  # -3% hard stop — V6 research: Sharpe 1.03 on minute bars (momentum screener)
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

    # Sparkline precomputation (for engulfing dashboard)
    SPARKLINE_REFRESH_INTERVAL_SEC: int = 300  # Refresh sparkline_1d every 5 min
    SPARKLINE_POINTS: int = 78                 # Downsample to ~78 points (≈ 5-min intervals over 6.5h)

    # Intraday bar collection
    COLLECT_INTRADAY_BARS: bool = True
    INTRADAY_BARS_MAX_BATCHES: int = 35      # 35 × 100 = 3,500 symbols (covers 3,014 tracked + headroom)
    INTRADAY_BARS_INTERVAL_SEC: int = 60     # collect every 60 seconds
    INTRADAY_BARS_RETENTION_DAYS: int = 21   # 21 calendar days ≈ 14 trading days

    # Logging
    LOG_FILE: str = "paper_trading.log"
    LOG_TRADES: bool = True


# Default config instance
DEFAULT_CONFIG = TradingConfig()
