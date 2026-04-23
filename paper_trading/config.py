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

Account E (Expert Committee — AI virtual trading desk):
- 7 domain experts emit signals with conviction scores + TTL
- PM Synthesizer applies weighted consensus (score >= 60 threshold)
- Risk Manager has veto power on any trade
- Supports intraday (EOD close, -3% stop) and swing (2-5 day, -5% stop)
- US equities + options, long and short
- Weekly weight recalibration (Sunday 6 PM ET)

Legacy (UOA signals, disabled by default):
- Uptrend, Score >= 10, RSI < 55, ADV >= 1K, $50K+ notional
- Same-day exit at 3:55 PM
"""

from dataclasses import dataclass, field
from datetime import time as dt_time


@dataclass
class TradingConfig:
    """Paper trading configuration."""

    def __post_init__(self):
        if self.ACCOUNT_E_BASE_WEIGHTS is None:
            self.ACCOUNT_E_BASE_WEIGHTS = {
                "flow_analyst": 0.25,
                "technical_analyst": 0.20,
                "quant_analyst": 0.20,
                "sentiment_analyst": 0.15,
                "macro_strategist": 0.10,
                "risk_manager": 0.10,
            }

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

    # Account D — SPY 0DTE Binary Bet (15-min hold)
    # Core concept: 15-minute binary bet on SPY direction using TA consensus.
    # Every trade enters and exits within a fixed 15-minute window.
    USE_ACCOUNT_D: bool = True
    ACCOUNT_D_POLL_INTERVAL_SEC: int = 30          # Poll for SPY signals every 30s
    ACCOUNT_D_MAX_RISK_PER_TRADE: float = 200.0    # Max $ premium per trade (options are leveraged)
    ACCOUNT_D_MAX_CONTRACTS: int = 3               # Max option contracts per trade
    ACCOUNT_D_MAX_POSITIONS: int = 3               # Max concurrent 0DTE positions
    ACCOUNT_D_MAX_TRADES_PER_DAY: int = 6          # Daily trade cap — stop entering after this many fills
    ACCOUNT_D_HOLD_DURATION_MIN: int = 15          # Hard exit after 15 minutes regardless of P&L
    ACCOUNT_D_SCAN_START: dt_time = dt_time(9, 45)  # Start scanning at 9:45 AM ET (after open vol settles)
    ACCOUNT_D_SCAN_END: dt_time = dt_time(15, 40)   # Stop scanning at 3:40 PM ET (last 15-min window before 3:55 force-close)
    ACCOUNT_D_MIN_DELTA: float = 0.30              # Min delta for strike selection
    ACCOUNT_D_MAX_DELTA: float = 0.50              # Max delta for strike selection
    ACCOUNT_D_MIN_OPTION_VOLUME: int = 100         # Min option volume at strike
    ACCOUNT_D_MAX_SPREAD_PCT: float = 0.10         # Max bid-ask spread as % of mid
    ACCOUNT_D_CONFIRMATION_WINDOW_MIN: int = 5     # Cancel unfilled limit orders after 5 min (0DTE moves fast)
    ACCOUNT_D_STOP_LOSS_PCT: float = -0.40         # Stop loss at -40% of premium
    ACCOUNT_D_TAKE_PROFIT_PCT: float = 0.50        # Take profit at +50% of premium
    ACCOUNT_D_TIME_EXIT_BUFFER_MIN: int = 5        # Force-close safety net: 3:55 PM (only if 15-min exit missed)
    ACCOUNT_D_MIN_CONFIDENCE: float = 0.60         # Min TA confidence to enter (3/5 = 0.60, collect data first)
    ACCOUNT_D_TA_CONSENSUS_MIN: int = 3            # Min indicators agreeing (out of 5)
    ACCOUNT_D_VIRTUAL_LOG_INTERVAL_SEC: int = 300  # Log premium every 5 min during hold (virtual mode)

    # Account E — Expert Committee (AI virtual trading desk)
    USE_ACCOUNT_E: bool = False  # Disabled until Account E Alpaca credentials are set

    # Account E Agent Settings (v2 — Claude CLI agents)
    ACCOUNT_E_USE_AGENTS: bool = True           # True=Claude CLI agents, False=rule-based only
    ACCOUNT_E_EXPERT_MODEL: str = "sonnet"      # Model for 4 expert agents
    ACCOUNT_E_PM_MODEL: str = "opus"            # Model for PM synthesis agent
    ACCOUNT_E_EXPERT_BUDGET: float = 0.25       # USD hard cap per expert agent
    ACCOUNT_E_PM_BUDGET: float = 0.50           # USD hard cap for PM agent
    ACCOUNT_E_AGENT_TIMEOUT_SEC: int = 180      # 3 min per agent
    ACCOUNT_E_MAX_DAILY_AGENT_COST: float = 30.00  # USD daily ceiling (4 Sonnet + 1 Opus)

    # Expert base weights (signal-weighted ensemble)
    ACCOUNT_E_BASE_WEIGHTS: dict = None  # Set in __post_init__

    # PM synthesis thresholds
    ACCOUNT_E_MIN_WEIGHTED_SCORE: float = 60.0       # Min conviction to trigger trade
    ACCOUNT_E_COLD_START_MIN_SCORE: float = 70.0     # Higher threshold during first 20 trades
    ACCOUNT_E_CONFLICT_DISCOUNT: float = 0.7         # 30% discount when both sides have 2+ experts
    ACCOUNT_E_OPPOSITION_PENALTY: float = 0.5        # Opposition halves the dominant score

    # Position sizing & limits
    ACCOUNT_E_MAX_POSITIONS: int = 15                # Max concurrent open positions
    ACCOUNT_E_MAX_POSITION_SIZE_PCT: float = 0.10    # Max 10% of portfolio per position
    ACCOUNT_E_INTRADAY_STOP_PCT: float = -0.03       # Hard stop for intraday trades
    ACCOUNT_E_SWING_STOP_PCT: float = -0.05          # Hard stop for swing trades
    ACCOUNT_E_MAX_SWING_DAYS: int = 5                # Force-close swing positions on D+5

    # Timing
    ACCOUNT_E_POLL_INTERVAL_SEC: int = 30            # Poll pm_decisions_e every N seconds
    ACCOUNT_E_POSITION_CHECK_INTERVAL_SEC: int = 30  # Check stop/target hits every N seconds
    ACCOUNT_E_FIRST_ENTRY_TIME: dt_time = dt_time(9, 35)   # No entries before 9:35 AM ET
    ACCOUNT_E_LAST_ENTRY_TIME: dt_time = dt_time(15, 40)   # No entries after 3:40 PM ET
    ACCOUNT_E_OPTION_EXPIRY_CLOSE_TIME: dt_time = dt_time(15, 30)  # Close expiring options by 3:30 PM

    # Risk manager veto thresholds
    ACCOUNT_E_MAX_BETA: float = 1.2                  # Portfolio beta ceiling
    ACCOUNT_E_MAX_SECTOR_CONCENTRATION: float = 0.30 # Max 30% sector exposure
    ACCOUNT_E_MAX_DRAWDOWN: float = 0.10             # Max 10% portfolio drawdown

    # Cold-start bootstrap
    ACCOUNT_E_MIN_TRADES_FOR_RECAL: int = 20         # Trades before dynamic weight recalibration
    ACCOUNT_E_COLD_START_SIZE_MULT: float = 0.50     # 50% position sizing for first 10 trades

    # Weight recalibration
    ACCOUNT_E_WEIGHT_MIN: float = 0.05               # Floor weight per expert (5%)
    ACCOUNT_E_WEIGHT_MAX: float = 0.40               # Ceiling weight per expert (40%)
    ACCOUNT_E_TRAILING_SHARPE_WINDOW: int = 20       # Rolling 20-trade Sharpe for recalibration

    # Expert rate limiting (max signals per symbol per window_minutes)
    ACCOUNT_E_PORTFOLIO_SIGNAL_CAP: int = 50         # Max 50 signals/hour across all symbols

    # Options
    ACCOUNT_E_ALLOW_OPTIONS: bool = True              # Allow option trades
    ACCOUNT_E_ALLOW_SHORT: bool = True                # Allow bearish/short signals

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
    INTRADAY_BARS_MAX_BATCHES: int = 55      # 55 × 100 = 5,500 symbols (covers 5,203 tracked + headroom)
    INTRADAY_BARS_INTERVAL_SEC: int = 60     # collect every 60 seconds
    INTRADAY_BARS_RETENTION_DAYS: int = 21   # 21 calendar days ≈ 14 trading days

    # Logging
    LOG_FILE: str = "paper_trading.log"
    LOG_TRADES: bool = True


# Default config instance
DEFAULT_CONFIG = TradingConfig()
