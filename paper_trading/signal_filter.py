"""
Signal Filter

Applies entry rules to determine if a signal should trigger a trade:
- Score >= 10
- Uptrend (price > 20d SMA)
- Prior-day RSI < 50 (adaptive: RSI < 60 on bounce-back days -- V29)
- $50K+ notional
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dt_time
from typing import Optional, List, Dict, Tuple

import pytz

from .config import TradingConfig, DEFAULT_CONFIG
from .dashboard import get_dashboard, log_active_signal_to_db

logger = logging.getLogger(__name__)

# ETFs to exclude — our edge is on individual stocks
ETF_EXCLUSIONS = {
    # Major index ETFs
    'SPY', 'QQQ', 'IWM', 'DIA', 'RSP', 'MDY', 'IJR', 'IJH',
    # Sector SPDRs
    'XLE', 'XLF', 'XLK', 'XLV', 'XLI', 'XLU', 'XLP', 'XLY', 'XLB', 'XLRE', 'XLC',
    # Thematic / Industry ETFs
    'ITB', 'XHB', 'XOP', 'XBI', 'XRT', 'XME', 'KWEB', 'MCHI', 'FXI',
    'SOXX', 'SMH', 'HACK', 'BOTZ', 'ROBO', 'IBB',
    'IYR', 'VNQ', 'GDXJ', 'GDX', 'JETS', 'KRE', 'KBE',
    # Leveraged / Inverse
    'VTI', 'VOO', 'VXX', 'UVXY', 'SQQQ', 'TQQQ', 'SPXU', 'SPXS',
    'UPRO', 'LABU', 'LABD', 'SOXL', 'SOXS', 'TNA', 'TZA',
    # Commodity ETFs
    'GLD', 'SLV', 'USO', 'UNG', 'WEAT', 'DBA', 'DBC',
    # Bond ETFs
    'TLT', 'HYG', 'LQD', 'JNK', 'AGG', 'BND', 'SHY', 'IEF',
    # International ETFs
    'EEM', 'EFA', 'VWO', 'IEMG',
    # ARK ETFs
    'ARKK', 'ARKG', 'ARKW', 'ARKF', 'ARKQ', 'ARKX',
    # Crypto ETFs
    'IBIT', 'BITO', 'GBTC', 'ETHE', 'FBTC', 'BITB',
}

# Sector concentration limit (max positions per sector)
MAX_SECTOR_CONCENTRATION = 2


# Global sector cache - loaded once at startup
_SECTOR_CACHE: Dict[str, str] = {}
_SECTOR_CACHE_LOADED = False


def _load_sector_cache(db_url: str) -> None:
    """Load all sectors into cache at startup (non-blocking after first call)."""
    global _SECTOR_CACHE, _SECTOR_CACHE_LOADED
    if _SECTOR_CACHE_LOADED:
        return

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, sector FROM master_tickers
            WHERE sector IS NOT NULL
        """)
        for row in cur.fetchall():
            _SECTOR_CACHE[row[0]] = row[1]
        cur.close()
        conn.close()
        _SECTOR_CACHE_LOADED = True
        logger.info(f"Loaded sector cache: {len(_SECTOR_CACHE)} symbols")
    except Exception as e:
        logger.warning(f"Failed to load sector cache: {e}")


def get_sector_for_symbol(symbol: str, db_url: str = None) -> str:
    """
    Look up sector for a symbol from cache.

    Returns sector name or "Unknown" if not found.
    """
    if not db_url:
        db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return "Unknown"

    # Load cache on first call
    if not _SECTOR_CACHE_LOADED:
        _load_sector_cache(db_url)

    return _SECTOR_CACHE.get(symbol, "Unknown")


def track_symbol_for_ta(db_url: str, symbol: str, trigger_ts: datetime) -> bool:
    """
    Add symbol to tracked_tickers_v2 for intraday TA updates.

    Called when a signal passes all filters. Ensures the symbol will
    receive 5-minute TA refreshes from ta_pipeline_v2.

    Uses upsert: if symbol exists, increments trigger_count.
    """
    if not db_url:
        return False

    try:
        import psycopg2
        conn = psycopg2.connect(db_url.strip())
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO tracked_tickers_v2
            (symbol, first_trigger_ts, trigger_count, last_trigger_ts, ta_enabled)
            VALUES (%s, %s, 1, %s, TRUE)
            ON CONFLICT (symbol) DO UPDATE SET
                trigger_count = tracked_tickers_v2.trigger_count + 1,
                last_trigger_ts = %s,
                updated_at = NOW()
        """, (symbol, trigger_ts, trigger_ts, trigger_ts))
        conn.commit()
        cur.close()
        conn.close()
        logger.debug(f"Tracked symbol for TA: {symbol}")
        return True
    except Exception as e:
        logger.warning(f"Failed to track symbol {symbol}: {e}")
        return False


@dataclass
class Signal:
    """Incoming trading signal."""
    symbol: str
    detection_time: datetime
    score: int
    notional: float
    contracts: int

    # TA indicators (prior-day)
    rsi_14_prior: Optional[float] = None
    macd_hist_prior: Optional[float] = None
    sma_20_prior: Optional[float] = None
    sma_50_prior: Optional[float] = None  # Multi-week momentum guard

    # Current price context
    price_at_signal: Optional[float] = None
    trend: Optional[int] = None  # 1 = uptrend, -1 = downtrend

    # Additional context
    call_pct: Optional[float] = None
    sweep_pct: Optional[float] = None
    strike_concentration: Optional[float] = None
    num_strikes: Optional[int] = None
    ratio: Optional[float] = None

    # Score breakdown
    score_volume: Optional[int] = None
    score_call_pct: Optional[int] = None
    score_sweep: Optional[int] = None
    score_strikes: Optional[int] = None
    score_notional: Optional[int] = None

    # Shadow metadata (GEX etc.) — not used in filtering
    metadata: Optional[Dict] = None


@dataclass
class FilterResult:
    """Result of applying filter to a signal."""
    signal: Signal
    passed: bool
    reasons: List[str]  # Reasons for filtering (if not passed)

    @property
    def summary(self) -> str:
        if self.passed:
            return f"PASS: {self.signal.symbol} score={self.signal.score}"
        return f"FAIL: {self.signal.symbol} - {', '.join(self.reasons)}"


class SignalFilter:
    """
    Filters signals based on entry rules.

    Rules:
    - Score >= SCORE_THRESHOLD (default 10)
    - Uptrend: price > 20d SMA (trend == 1)
    - RSI < RSI_THRESHOLD (default 50)
    - Notional >= MIN_NOTIONAL (default $50K)
    """

    def __init__(self, config: TradingConfig = DEFAULT_CONFIG, database_url: Optional[str] = None):
        self.config = config
        db_url = database_url or os.environ.get("DATABASE_URL")
        self.database_url = db_url.strip() if db_url else None

        # Track filter statistics
        self.total_signals = 0
        self.passed_signals = 0
        self.filter_reasons: Dict[str, int] = {
            "etf": 0,
            "score": 0,
            "trend": 0,
            "rsi": 0,
            "notional": 0,
            "sma50": 0,
            "sentiment_mentions": 0,
            "sentiment_negative": 0,
            "earnings": 0,
        }

        # Earnings cache to avoid repeated DB lookups
        self._earnings_cache: Dict[str, Tuple[bool, Optional[int], Optional[str]]] = {}
        self._earnings_loaded = False

        # Sentiment cache to avoid repeated DB lookups
        self._sentiment_cache: Dict[str, Tuple[Optional[int], Optional[float]]] = {}

        # Adaptive RSI state (V29)
        self._bounce_eligible = False
        self._bounce_checked = False
        self._is_bounce_day = False
        self._red_streak = 0
        self._spy_prior_close = None
        self._effective_rsi_threshold = config.RSI_THRESHOLD  # default 50.0

        # Pre-load caches at startup
        self._preload_caches()

    def _preload_caches(self):
        """Pre-load earnings and sector data to avoid blocking during signal processing."""
        if not self.database_url:
            return

        # Load sector cache (global)
        _load_sector_cache(self.database_url)

        # Load earnings cache for all symbols with earnings in window
        self._load_earnings_cache()

        # Check bounce-day eligibility (V29)
        if self.config.USE_ADAPTIVE_RSI:
            self._check_bounce_day_eligible()

    def _load_earnings_cache(self):
        """Load all earnings data within the proximity window at startup."""
        if self._earnings_loaded or not self.database_url:
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            days = self.config.EARNINGS_PROXIMITY_DAYS

            cur.execute("""
                SELECT
                    symbol,
                    event_date,
                    event_date - CURRENT_DATE as days_until,
                    hour
                FROM earnings_calendar
                WHERE event_date BETWEEN CURRENT_DATE - %s AND CURRENT_DATE + %s
                  AND is_current = true
            """, (days, days))

            for row in cur.fetchall():
                symbol = row[0]
                days_until = row[2]

                if days_until == 0:
                    timing = "TODAY"
                elif days_until == 1:
                    timing = "TOMORROW"
                elif days_until == -1:
                    timing = "YESTERDAY"
                elif days_until > 0:
                    timing = f"+{days_until} DAYS"
                else:
                    timing = f"{days_until} DAYS"

                self._earnings_cache[symbol] = (True, days_until, timing)

            cur.close()
            conn.close()
            self._earnings_loaded = True
            logger.info(f"Loaded earnings cache: {len(self._earnings_cache)} symbols with upcoming earnings")
        except Exception as e:
            logger.warning(f"Failed to load earnings cache: {e}")

    # ------------------------------------------------------------------
    # Adaptive RSI — bounce-day detection (V29)
    # ------------------------------------------------------------------

    def _check_bounce_day_eligible(self):
        """
        Check if today is eligible for bounce-day RSI relaxation.

        Queries SPY's last 5 daily closes from ta_daily_close.
        Sets self._bounce_eligible if 2+ consecutive red closes detected.
        Actual confirmation happens via _auto_confirm_bounce_day() when
        we see SPY's opening price after market open.
        """
        if not self.database_url or not self.config.USE_ADAPTIVE_RSI:
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            cur.execute("""
                SELECT trade_date, close_price
                FROM ta_daily_close
                WHERE symbol = 'SPY'
                ORDER BY trade_date DESC
                LIMIT 5
            """)

            rows = cur.fetchall()
            cur.close()
            conn.close()

            if len(rows) < 3:
                self._bounce_eligible = False
                return

            # rows are newest-first, reverse for chronological order
            closes = [float(row[1]) for row in reversed(rows)]

            # Store SPY prior close for confirmation step
            self._spy_prior_close = closes[-1]

            # Count consecutive red closes from most recent backwards
            red_streak = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes[i] < closes[i - 1]:
                    red_streak += 1
                else:
                    break

            self._bounce_eligible = red_streak >= self.config.ADAPTIVE_RSI_MIN_RED_DAYS
            self._red_streak = red_streak

            if self._bounce_eligible:
                logger.info(
                    f"BOUNCE DAY ELIGIBLE: SPY had {red_streak} consecutive red closes. "
                    f"Waiting for green open to confirm. Prior close: {self._spy_prior_close:.2f}"
                )
            else:
                logger.info(
                    f"Normal day: SPY red streak = {red_streak} "
                    f"(need >= {self.config.ADAPTIVE_RSI_MIN_RED_DAYS})"
                )

        except Exception as e:
            logger.warning(f"Failed to check bounce day: {e}")
            self._bounce_eligible = False

    def _auto_confirm_bounce_day(self):
        """
        Auto-confirm bounce day by fetching SPY's current price from Alpaca.

        Called once from apply() on first signal after 9:31 AM ET when
        bounce_eligible is True. Sets _effective_rsi_threshold for the day.
        """
        if self._bounce_checked:
            return
        self._bounce_checked = True

        if not self._bounce_eligible or self._spy_prior_close is None:
            return

        try:
            import requests
            api_key = os.environ.get("ALPACA_API_KEY")
            secret_key = os.environ.get("ALPACA_SECRET_KEY")

            if not api_key or not secret_key:
                logger.warning("No Alpaca keys, cannot confirm bounce day")
                return

            resp = requests.get(
                "https://data.alpaca.markets/v2/stocks/SPY/snapshot",
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": secret_key,
                },
                timeout=3,
            )

            if resp.status_code != 200:
                logger.warning(f"SPY snapshot failed ({resp.status_code}), bounce day not confirmed")
                return

            data = resp.json()
            # Use daily bar open (today's open) or latest trade
            daily_bar = data.get("dailyBar", {})
            spy_open = float(daily_bar.get("o", 0)) if daily_bar.get("o") else None

            if not spy_open:
                latest = data.get("latestTrade", {})
                spy_open = float(latest.get("p", 0)) if latest.get("p") else None

            if not spy_open:
                logger.warning("Could not get SPY open price, bounce day not confirmed")
                return

            if spy_open > self._spy_prior_close:
                self._is_bounce_day = True
                self._effective_rsi_threshold = self.config.ADAPTIVE_RSI_THRESHOLD
                logger.info(
                    f"BOUNCE DAY CONFIRMED: SPY opened {spy_open:.2f} vs prior close "
                    f"{self._spy_prior_close:.2f}. RSI threshold relaxed to "
                    f"{self._effective_rsi_threshold}"
                )
            else:
                self._is_bounce_day = False
                self._effective_rsi_threshold = self.config.RSI_THRESHOLD
                logger.info(
                    f"Bounce day NOT confirmed: SPY opened {spy_open:.2f} <= prior close "
                    f"{self._spy_prior_close:.2f}. RSI threshold stays at "
                    f"{self._effective_rsi_threshold}"
                )

        except Exception as e:
            logger.warning(f"Failed to confirm bounce day: {e}")

    def _log_evaluation_sync(self, signal: Signal, passed: bool, rejection_reason: str):
        """Log signal evaluation to database for analysis."""
        if not self.database_url:
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            import json as _json
            metadata_json = _json.dumps(signal.metadata) if signal.metadata else None

            cur.execute("""
                INSERT INTO signal_evaluations (
                    symbol, detected_at, notional, ratio, call_pct, sweep_pct,
                    num_strikes, contracts, rsi_14, macd_histogram, trend,
                    score_volume, score_call_pct, score_sweep, score_strikes,
                    score_notional, score_total, passed_all_filters, rejection_reason,
                    metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                signal.symbol,
                signal.detection_time,
                signal.notional,
                signal.ratio,
                signal.call_pct,
                signal.sweep_pct,
                signal.num_strikes,
                signal.contracts,
                signal.rsi_14_prior,
                signal.macd_hist_prior,
                signal.trend,
                signal.score_volume,
                signal.score_call_pct,
                signal.score_sweep,
                signal.score_strikes,
                signal.score_notional,
                signal.score,
                passed,
                rejection_reason[:200] if rejection_reason else None,
                metadata_json,
            ))

            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to log evaluation to DB: {e}")

    def _log_evaluation(self, signal: Signal, passed: bool, rejection_reason: str):
        """
        Log signal evaluation to database (non-blocking).

        Runs the DB insert in a thread pool to avoid blocking the event loop.
        """
        import concurrent.futures
        try:
            # Use a thread pool to run the sync DB call without blocking
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            executor.submit(self._log_evaluation_sync, signal, passed, rejection_reason)
            # Don't wait for result - fire and forget
        except Exception as e:
            logger.debug(f"Failed to submit log evaluation: {e}")

    def _get_sentiment_data(self, symbol: str, signal_date: datetime) -> Tuple[Optional[int], Optional[float]]:
        """
        Fetch sentiment data from vw_media_daily_features view.

        Uses prior day's sentiment (T-1) since that's the latest available at signal time.

        NOTE: Migrated from sentiment_daily table to vw_media_daily_features view
        as of Feb 2026. The view computes on-the-fly from source tables (articles,
        article_insights) so it never goes stale, unlike the table which depends
        on the fr-sentiment-agg batch job.

        Column mapping:
        - mentions_total -> media_count (count of articles/media items)
        - sentiment_index -> avg_stance_weighted (weighted stance score)

        Returns:
            Tuple of (mentions_total, sentiment_index) or (None, None) if not found
        """
        cache_key = f"{symbol}:{signal_date.date()}"
        if cache_key in self._sentiment_cache:
            return self._sentiment_cache[cache_key]

        if not self.database_url:
            return (None, None)

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            # Use prior day's sentiment (T-1)
            prior_date = (signal_date - timedelta(days=1)).date()

            # Query vw_media_daily_features view (always fresh, no batch job dependency)
            cur.execute("""
                SELECT
                    COALESCE(media_count, 0) as mentions_total,
                    COALESCE(avg_stance_weighted, 0.0) as sentiment_index
                FROM vw_media_daily_features
                WHERE ticker = %s AND asof_date = %s
            """, (symbol, prior_date))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                result = (int(row[0]), float(row[1]))
            else:
                result = (None, None)

            self._sentiment_cache[cache_key] = result
            return result

        except Exception as e:
            logger.warning(f"Failed to fetch sentiment for {symbol}: {e}")
            return (None, None)

    def _check_earnings_proximity(self, symbol: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Check if symbol has earnings within proximity window.

        Uses pre-loaded cache from startup to avoid blocking DB calls.

        Returns:
            Tuple of (is_adjacent, days_to_earnings, timing)
            - is_adjacent: True if earnings within window
            - days_to_earnings: Days until earnings (negative = past)
            - timing: 'TODAY', 'TOMORROW', '+2 DAYS', etc.
        """
        # Ensure cache is loaded
        if not self._earnings_loaded:
            self._load_earnings_cache()

        # Check cache - if not in cache, no earnings in window
        if symbol in self._earnings_cache:
            return self._earnings_cache[symbol]

        # Not in cache = no earnings within window
        return (False, None, None)

    def _check_earnings_proximity_UNUSED(self, symbol: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """DEPRECATED: Old per-symbol DB lookup. Kept for reference."""
        if symbol in self._earnings_cache:
            return self._earnings_cache[symbol]

        if not self.database_url:
            return (False, None, None)

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            days = self.config.EARNINGS_PROXIMITY_DAYS

            cur.execute("""
                SELECT
                    event_date,
                    event_date - CURRENT_DATE as days_until,
                    hour
                FROM earnings_calendar
                WHERE symbol = %s
                  AND event_date BETWEEN CURRENT_DATE - %s AND CURRENT_DATE + %s
                  AND is_current = true
                ORDER BY ABS(event_date - CURRENT_DATE)
                LIMIT 1
            """, (symbol, days, days))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row is None:
                result = (False, None, None)
            else:
                days_until = row[1]

                if days_until == 0:
                    timing = "TODAY"
                elif days_until == 1:
                    timing = "TOMORROW"
                elif days_until == -1:
                    timing = "YESTERDAY"
                elif days_until > 0:
                    timing = f"+{days_until} DAYS"
                else:
                    timing = f"{days_until} DAYS"

                result = (True, days_until, timing)

            self._earnings_cache[symbol] = result
            return result

        except Exception as e:
            logger.warning(f"Earnings check failed for {symbol}: {e}")
            return (False, None, None)

    def passes_earnings_filter(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Check if symbol passes earnings proximity filter.

        Filter logic:
        - PASS if no earnings data within window
        - FAIL if earnings within +/- EARNINGS_PROXIMITY_DAYS

        Returns:
            Tuple of (passed, rejection_reason)
        """
        if not self.config.USE_EARNINGS_FILTER:
            return (True, None)

        is_adjacent, days_until, timing = self._check_earnings_proximity(symbol)

        if is_adjacent:
            return (False, f"earnings {timing}")

        return (True, None)

    def passes_sentiment_filter(self, symbol: str, signal_date: datetime) -> Tuple[bool, Optional[str]]:
        """
        Check if symbol passes sentiment filter.

        Filter logic (from TEST-8 analysis):
        - PASS if no sentiment data (don't penalize missing data)
        - FAIL if mentions >= 5 (too crowded, -0.73% avg)
        - FAIL if sentiment_index < 0 (negative sentiment, -1.23% avg)

        Returns:
            Tuple of (passed, rejection_reason)
        """
        if not self.config.USE_SENTIMENT_FILTER:
            return (True, None)

        mentions, sentiment = self._get_sentiment_data(symbol, signal_date)

        # No data = OK (don't penalize missing data)
        if mentions is None and sentiment is None:
            return (True, None)

        # Reject high mentions (crowded trade)
        if mentions is not None and mentions >= self.config.SENTIMENT_MAX_MENTIONS:
            return (False, f"high mentions ({mentions})")

        # Reject negative sentiment
        if sentiment is not None and sentiment < self.config.SENTIMENT_MIN_INDEX:
            return (False, f"negative sentiment ({sentiment:.2f})")

        return (True, None)

    def apply(self, signal: Signal) -> FilterResult:
        """
        Apply all filters to a signal.

        Returns FilterResult with pass/fail and reasons.
        """
        self.total_signals += 1
        reasons = []

        # Check ETF exclusion FIRST (before any other filters)
        if signal.symbol in ETF_EXCLUSIONS:
            reasons.append(f"ETF excluded ({signal.symbol})")
            self.filter_reasons["etf"] += 1
            # Log and return early - don't waste time on other checks
            self._log_evaluation(signal, False, f"ETF excluded ({signal.symbol})")
            logger.info(f"Signal FILTERED: {signal.symbol} - ETF excluded")
            return FilterResult(signal=signal, passed=False, reasons=reasons)

        # Check score
        if signal.score < self.config.SCORE_THRESHOLD:
            reasons.append(f"score {signal.score} < {self.config.SCORE_THRESHOLD}")
            self.filter_reasons["score"] += 1

        # Check trend (uptrend required)
        if self.config.REQUIRE_UPTREND:
            if signal.trend is None or signal.trend != 1:
                reasons.append("not uptrend")
                self.filter_reasons["trend"] += 1

        # Auto-confirm bounce day on first signal after market open (V29)
        if (self.config.USE_ADAPTIVE_RSI
                and self._bounce_eligible
                and not self._bounce_checked):
            et = pytz.timezone("America/New_York")
            now_et = datetime.now(et)
            if now_et.time() >= dt_time(9, 31):
                self._auto_confirm_bounce_day()

        # Check RSI — use effective threshold (50 normal, 60 bounce day)
        rsi_threshold = self._effective_rsi_threshold
        if signal.rsi_14_prior is not None:
            if signal.rsi_14_prior >= rsi_threshold:
                reasons.append(f"RSI {signal.rsi_14_prior:.1f} >= {rsi_threshold}")
                self.filter_reasons["rsi"] += 1
        else:
            reasons.append("no RSI data")
            self.filter_reasons["rsi"] += 1

        # Check 50d SMA (multi-week momentum guard)
        if signal.sma_50_prior is not None and signal.price_at_signal is not None:
            if signal.price_at_signal < signal.sma_50_prior:
                reasons.append(f"below 50d SMA ({signal.price_at_signal:.2f} < {signal.sma_50_prior:.2f})")
                self.filter_reasons["sma50"] += 1

        # Check notional
        if signal.notional < self.config.MIN_NOTIONAL:
            reasons.append(f"notional ${signal.notional:,.0f} < ${self.config.MIN_NOTIONAL:,.0f}")
            self.filter_reasons["notional"] += 1

        # Check sentiment (TEST-8)
        sentiment_passed, sentiment_reason = self.passes_sentiment_filter(
            signal.symbol, signal.detection_time
        )
        if not sentiment_passed:
            reasons.append(sentiment_reason)
            if "mentions" in sentiment_reason:
                self.filter_reasons["sentiment_mentions"] += 1
            else:
                self.filter_reasons["sentiment_negative"] += 1

        # Check earnings proximity (5.5)
        earnings_passed, earnings_reason = self.passes_earnings_filter(signal.symbol)
        if not earnings_passed:
            reasons.append(earnings_reason)
            self.filter_reasons["earnings"] += 1

        passed = len(reasons) == 0
        rejection_reason = "; ".join(reasons) if reasons else None

        # Tag signal metadata with bounce-day context (V29)
        if self.config.USE_ADAPTIVE_RSI:
            if signal.metadata is None:
                signal.metadata = {}
            signal.metadata["bounce_day"] = self._is_bounce_day
            signal.metadata["rsi_threshold_used"] = self._effective_rsi_threshold

        # Log to database (every evaluation)
        self._log_evaluation(signal, passed, rejection_reason)

        if passed:
            self.passed_signals += 1
            bounce_tag = ""
            if (self._is_bounce_day and signal.rsi_14_prior is not None
                    and signal.rsi_14_prior >= self.config.RSI_THRESHOLD):
                bounce_tag = " [BOUNCE DAY: RSI<60]"
            logger.info(
                f"Signal PASSED: {signal.symbol} "
                f"score={signal.score} RSI={signal.rsi_14_prior:.1f} "
                f"notional=${signal.notional:,.0f}{bounce_tag}"
            )

            # Log to active_signals table
            log_active_signal_to_db(
                db_url=self.database_url,
                symbol=signal.symbol,
                detected_at=signal.detection_time,
                notional=signal.notional,
                ratio=signal.ratio or 0,
                call_pct=signal.call_pct or 0,
                sweep_pct=signal.sweep_pct or 0,
                num_strikes=signal.num_strikes or 0,
                contracts=signal.contracts,
                rsi=signal.rsi_14_prior or 0,
                trend=signal.trend or 0,
                price=signal.price_at_signal or 0,
                score=signal.score,
            )

            # Track symbol for intraday TA updates (adds to tracked_tickers_v2)
            track_symbol_for_ta(
                db_url=self.database_url,
                symbol=signal.symbol,
                trigger_ts=signal.detection_time,
            )

            # Push to Google Sheets dashboard
            dashboard = get_dashboard()
            if dashboard.enabled:
                dashboard.log_signal(
                    symbol=signal.symbol,
                    score=signal.score,
                    rsi=signal.rsi_14_prior or 0,
                    ratio=signal.ratio or 0,
                    notional=signal.notional,
                    price=signal.price_at_signal or 0,
                    timestamp=signal.detection_time,
                )
        else:
            # Log filtered signals at INFO level now for visibility
            logger.info(
                f"Signal FILTERED: {signal.symbol} score={signal.score} - {rejection_reason}"
            )

        return FilterResult(signal=signal, passed=passed, reasons=reasons)

    def get_stats(self) -> Dict:
        """Get filter statistics."""
        pass_rate = (
            self.passed_signals / self.total_signals * 100
            if self.total_signals > 0
            else 0
        )

        return {
            "total_signals": self.total_signals,
            "passed_signals": self.passed_signals,
            "pass_rate": pass_rate,
            "filter_reasons": dict(self.filter_reasons),
        }

    def reset_stats(self):
        """Reset statistics for new day."""
        self.total_signals = 0
        self.passed_signals = 0
        self.filter_reasons = {k: 0 for k in self.filter_reasons}
        self._sentiment_cache.clear()  # Clear sentiment cache for new day
        self._earnings_cache.clear()  # Clear earnings cache for new day

        # Reset bounce-day state for new day (V29)
        self._bounce_eligible = False
        self._bounce_checked = False
        self._is_bounce_day = False
        self._red_streak = 0
        self._spy_prior_close = None
        self._effective_rsi_threshold = self.config.RSI_THRESHOLD

        # Re-check bounce eligibility for the new day
        if self.config.USE_ADAPTIVE_RSI:
            self._check_bounce_day_eligible()


class SignalGenerator:
    """
    Generates signals from firehose data.

    This is a simplified version that creates Signal objects from
    the aggregated trade data coming from the firehose.

    Features:
    - Dynamic TA fetching for symbols not in cache
    - Intraday TA refresh from ta_snapshots_v2 (after 9:35 AM)
    """

    # Time after which we use intraday TA instead of daily
    INTRADAY_TA_START = dt_time(9, 35)  # 5 min after open

    def __init__(self, ta_cache: Optional[Dict] = None, database_url: str = None):
        """
        Initialize signal generator.

        Args:
            ta_cache: Pre-loaded prior-day TA data {symbol: {rsi, macd, sma}}
            database_url: Database URL for fetching intraday TA from ta_snapshots_v2
        """
        self.ta_cache = ta_cache or {}  # Daily TA cache (prior day close)
        self.database_url = database_url or os.environ.get("DATABASE_URL")
        self._alpaca_fetcher = None
        self._fetch_lock = asyncio.Lock()
        self._fetched_symbols: set = set()  # Track symbols we've already tried to fetch

        # Intraday TA cache (refreshed from ta_snapshots_v2)
        self._intraday_ta_cache: Dict[str, Dict] = {}
        self._intraday_cache_ts: Optional[datetime] = None
        self._intraday_cache_max_age_sec = 300  # Refresh every 5 minutes

        # GEX cache (loaded once, refreshed daily — data changes nightly via ORATS ingest)
        self._gex_cache: Dict[str, Dict] = {}
        self._gex_cache_loaded = False

        # Timezone for market hours check
        self._et = pytz.timezone("America/New_York")

    def _should_use_intraday_ta(self) -> bool:
        """Check if we should use intraday TA (after 9:35 AM ET)."""
        now_et = datetime.now(self._et)
        return now_et.time() >= self.INTRADAY_TA_START

    def _is_intraday_cache_stale(self) -> bool:
        """Check if intraday TA cache needs refresh."""
        if not self._intraday_cache_ts:
            return True
        age = (datetime.now(self._et) - self._intraday_cache_ts).total_seconds()
        return age > self._intraday_cache_max_age_sec

    async def _refresh_intraday_ta_cache(self) -> None:
        """
        Refresh intraday TA cache from ta_snapshots_v2 table.

        Fetches the latest snapshot for all symbols (within last 10 minutes).
        This is called periodically to get fresh 5-minute TA data.
        """
        if not self.database_url:
            logger.debug("No database URL, skipping intraday TA refresh")
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url.strip())
            cur = conn.cursor()

            # Get latest TA snapshots (within last 10 minutes to handle gaps)
            cur.execute("""
                WITH latest AS (
                    SELECT symbol, MAX(snapshot_ts) as max_ts
                    FROM ta_snapshots_v2
                    WHERE snapshot_ts > NOW() - INTERVAL '10 minutes'
                    GROUP BY symbol
                )
                SELECT t.symbol, t.rsi_14, t.sma_20, t.price, t.snapshot_ts
                FROM ta_snapshots_v2 t
                JOIN latest l ON t.symbol = l.symbol AND t.snapshot_ts = l.max_ts
            """)

            rows = cur.fetchall()
            cur.close()
            conn.close()

            if rows:
                new_cache = {}
                for symbol, rsi_14, sma_20, price, snapshot_ts in rows:
                    new_cache[symbol] = {
                        "rsi_14": float(rsi_14) if rsi_14 else None,
                        "sma_20": float(sma_20) if sma_20 else None,
                        "last_close": float(price) if price else None,
                        "snapshot_ts": snapshot_ts,
                    }
                self._intraday_ta_cache = new_cache
                self._intraday_cache_ts = datetime.now(self._et)
                logger.info(f"Refreshed intraday TA cache: {len(new_cache)} symbols")
            else:
                logger.warning("No recent intraday TA data found in ta_snapshots_v2")

        except Exception as e:
            logger.warning(f"Failed to refresh intraday TA cache: {e}")

    def _get_ta_for_symbol(self, symbol: str) -> Dict:
        """
        Get TA data for a symbol, using intraday data when appropriate.

        Priority:
        1. Before 9:35 AM: Use daily cache (prior day close)
        2. After 9:35 AM: Use intraday cache if available, fall back to daily
        3. For sma_50: Always use daily cache (50-day average doesn't change intraday)

        Returns:
            Dict with rsi_14, sma_20, sma_50, last_close, trend
        """
        daily_ta = self.ta_cache.get(symbol, {})

        # Before intraday start time, use daily cache
        if not self._should_use_intraday_ta():
            return daily_ta

        # After intraday start, try to use fresh intraday data
        intraday_ta = self._intraday_ta_cache.get(symbol, {})

        if not intraday_ta:
            # No intraday data, fall back to daily
            return daily_ta

        # Merge: use intraday for RSI/SMA20, daily for SMA50/MACD
        merged = {
            "rsi_14": intraday_ta.get("rsi_14") or daily_ta.get("rsi_14"),
            "sma_20": intraday_ta.get("sma_20") or daily_ta.get("sma_20"),
            "sma_50": daily_ta.get("sma_50"),  # Always from daily (50-day avg)
            "macd_hist": daily_ta.get("macd_hist"),  # Keep from daily
            "last_close": intraday_ta.get("last_close") or daily_ta.get("last_close"),
        }

        # Recalculate trend based on current price vs intraday SMA20
        if merged.get("last_close") and merged.get("sma_20"):
            merged["trend"] = 1 if merged["last_close"] > merged["sma_20"] else -1
        else:
            merged["trend"] = daily_ta.get("trend")

        return merged

    async def _get_alpaca_fetcher(self):
        """Lazy-load Alpaca bars fetcher."""
        if self._alpaca_fetcher is None:
            from adapters.alpaca_bars_batch import AlpacaBarsFetcher
            api_key = os.environ.get("ALPACA_API_KEY")
            secret_key = os.environ.get("ALPACA_SECRET_KEY")
            if api_key and secret_key:
                self._alpaca_fetcher = AlpacaBarsFetcher(api_key, secret_key)
                logger.info("Initialized Alpaca fetcher for dynamic TA")
            else:
                logger.warning("ALPACA_API_KEY/ALPACA_SECRET_KEY not set, dynamic TA fetch disabled")
        return self._alpaca_fetcher

    async def fetch_ta_for_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Fetch TA data for a symbol from Alpaca (on-demand).

        Returns dict with rsi_14, macd_hist, sma_20, last_close, trend
        or None if fetch fails.
        """
        # Don't retry symbols we've already tried
        if symbol in self._fetched_symbols:
            return None

        async with self._fetch_lock:
            # Double-check after acquiring lock
            if symbol in self.ta_cache:
                return self.ta_cache[symbol]
            if symbol in self._fetched_symbols:
                return None

            self._fetched_symbols.add(symbol)

            fetcher = await self._get_alpaca_fetcher()
            if not fetcher:
                return None

            try:
                logger.info(f"Fetching TA for {symbol} from Alpaca...")
                # Explicit start date required — without it Alpaca returns only today's bar
                from datetime import timezone
                start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
                bar_data = await fetcher.get_bars(symbol, timeframe="1Day", limit=70, start=start_date, feed="sip")

                if not bar_data.bars or len(bar_data.bars) < 20:
                    logger.warning(f"Insufficient bar data for {symbol}: {len(bar_data.bars) if bar_data.bars else 0} bars")
                    return None

                closes = [b.close for b in bar_data.bars]

                # Calculate indicators (same as premarket_ta_cache)
                rsi_14 = self._calculate_rsi(closes, 14)
                sma_20 = self._calculate_sma(closes, 20)
                sma_50 = self._calculate_sma(closes, 50)  # Multi-week momentum
                macd_line, macd_signal, macd_hist = self._calculate_macd(closes)

                last_close = closes[-1] if closes else None
                trend = None
                if last_close and sma_20:
                    trend = 1 if last_close > sma_20 else -1

                ta_data = {
                    "rsi_14": rsi_14,
                    "macd_hist": macd_hist,
                    "sma_20": sma_20,
                    "sma_50": sma_50,
                    "last_close": last_close,
                    "trend": trend,
                }

                # Cache for future use
                self.ta_cache[symbol] = ta_data
                logger.info(f"Fetched TA for {symbol}: RSI={rsi_14}, trend={trend}")

                return ta_data

            except Exception as e:
                logger.error(f"Failed to fetch TA for {symbol}: {e}")
                return None

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> Optional[float]:
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

    def _calculate_sma(self, prices: List[float], period: int) -> Optional[float]:
        """Calculate SMA."""
        if len(prices) < period:
            return None
        return round(sum(prices[-period:]) / period, 4)

    def _calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """Calculate EMA."""
        if len(prices) < period:
            return None
        ema = sum(prices[:period]) / period
        k = 2 / (period + 1)
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)
        return round(ema, 4)

    def _calculate_macd(self, closes: List[float]) -> tuple:
        """Calculate MACD(12, 26, 9). Returns (line, signal, histogram)."""
        if len(closes) < 35:
            return None, None, None
        ema_12 = self._calculate_ema(closes, 12)
        ema_26 = self._calculate_ema(closes, 26)
        if ema_12 is None or ema_26 is None:
            return None, None, None
        macd_line = ema_12 - ema_26
        macd_values = []
        for i in range(26, len(closes) + 1):
            e12 = self._calculate_ema(closes[:i], 12)
            e26 = self._calculate_ema(closes[:i], 26)
            if e12 and e26:
                macd_values.append(e12 - e26)
        if len(macd_values) < 9:
            return round(macd_line, 4), None, None
        signal_line = self._calculate_ema(macd_values, 9)
        histogram = macd_line - signal_line if signal_line else None
        return (
            round(macd_line, 4),
            round(signal_line, 4) if signal_line else None,
            round(histogram, 4) if histogram else None
        )

    async def _fetch_current_price(self, symbol: str) -> float:
        """
        Fetch current stock price from Alpaca snapshot API.

        Returns real-time price during market hours, 0 if fetch fails.
        """
        try:
            import aiohttp
            api_key = os.environ.get("ALPACA_API_KEY")
            secret_key = os.environ.get("ALPACA_SECRET_KEY")

            if not api_key or not secret_key:
                return 0

            headers = {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }

            url = f"https://data.alpaca.markets/v2/stocks/{symbol}/snapshot"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return 0

                    data = await resp.json()
                    # Use latest trade price, or daily bar close as fallback
                    latest_trade = data.get("latestTrade", {})
                    if latest_trade.get("p"):
                        return float(latest_trade["p"])

                    daily_bar = data.get("dailyBar", {})
                    if daily_bar.get("c"):
                        return float(daily_bar["c"])

                    return 0
        except Exception as e:
            logger.debug(f"Failed to fetch price for {symbol}: {e}")
            return 0

    async def create_signal_async(
        self,
        symbol: str,
        score: int,
        notional: float,
        contracts: int,
        price: float,
        trend: int,
        # Score breakdown
        ratio: float = 0,
        call_pct: float = 0,
        sweep_pct: float = 0,
        num_strikes: int = 0,
        score_volume: int = 0,
        score_call_pct: int = 0,
        score_sweep: int = 0,
        score_strikes: int = 0,
        score_notional: int = 0,
    ) -> Optional[Signal]:
        """
        Create a Signal object from aggregated data (async version with dynamic TA fetch).

        TA data source priority:
        1. Before 9:35 AM: Daily cache (prior day close from ta_daily_close)
        2. After 9:35 AM: Intraday cache (5-min refresh from ta_snapshots_v2)
        3. Fallback: On-demand fetch from Alpaca API

        Fetches current price from Alpaca if aggregator price is 0.

        IMPORTANT: Uses strict timeouts to avoid blocking WebSocket ping/pong.

        Returns:
            Signal object if TA data available, None if critical data missing.
        """
        # Refresh intraday TA cache if we're in intraday mode and cache is stale
        if self._should_use_intraday_ta() and self._is_intraday_cache_stale():
            try:
                await asyncio.wait_for(self._refresh_intraday_ta_cache(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Intraday TA cache refresh timeout")
            except Exception as e:
                logger.warning(f"Intraday TA cache refresh failed: {e}")

        # Check if we need to fetch TA from Alpaca (fallback for unknown symbols)
        # Only do this if symbol is not in daily cache AND not in intraday cache
        if symbol not in self.ta_cache and symbol not in self._intraday_ta_cache:
            try:
                await asyncio.wait_for(self.fetch_ta_for_symbol(symbol), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(f"TA fetch timeout for {symbol}, rejecting signal")
            except Exception as e:
                logger.warning(f"TA fetch failed for {symbol}: {e}, rejecting signal")

        # Get TA using the smart lookup (intraday vs daily based on time)
        ta = self._get_ta_for_symbol(symbol)

        # CRITICAL: Reject signal if we don't have required TA data
        # Without RSI and SMA data, we can't properly filter the signal
        if not ta or ta.get("rsi_14") is None or ta.get("sma_20") is None:
            logger.info(f"Signal REJECTED: {symbol} - missing TA data (RSI/SMA not available)")
            return None

        # Get real-time price from Alpaca since aggregator doesn't have stock prices
        # (options trades only contain option premiums, not underlying stock prices)
        # Use strict 2s timeout to avoid blocking WebSocket ping
        effective_price = price if price and price > 0 else 0
        if effective_price <= 0:
            try:
                effective_price = await asyncio.wait_for(self._fetch_current_price(symbol), timeout=2.0)
            except asyncio.TimeoutError:
                logger.debug(f"Price fetch timeout for {symbol}")
                effective_price = 0
            except Exception as e:
                logger.debug(f"Price fetch failed for {symbol}: {e}")
                effective_price = 0

            if effective_price <= 0:
                # Final fallback to TA cache's last_close
                effective_price = ta.get("last_close", 0)

        signal = self.create_signal(
            symbol, score, notional, contracts, effective_price, trend,
            ratio=ratio, call_pct=call_pct, sweep_pct=sweep_pct,
            num_strikes=num_strikes, score_volume=score_volume,
            score_call_pct=score_call_pct, score_sweep=score_sweep,
            score_strikes=score_strikes, score_notional=score_notional,
        )

        # Shadow: attach GEX metadata if available (not used in filtering)
        if signal and self.database_url:
            try:
                gex = self._lookup_gex(signal.symbol)
                if gex:
                    signal.metadata = gex
            except Exception as e:
                logger.debug(f"GEX lookup failed for {signal.symbol}: {e}")

        return signal

    def _load_gex_cache(self):
        """Bulk load latest GEX metrics for all symbols into cache.

        Uses DISTINCT ON to get one row per symbol (latest snapshot_ts).
        Called lazily on first GEX lookup. Data only changes nightly via ORATS ingest.
        """
        if self._gex_cache_loaded or not self.database_url:
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT ON (symbol)
                    symbol, net_gex, net_dex, call_wall_strike, put_wall_strike,
                    gamma_flip_level, spot_price, contracts_analyzed
                FROM gex_metrics_snapshot
                ORDER BY symbol, snapshot_ts DESC
            """)
            for row in cur.fetchall():
                self._gex_cache[row[0]] = {
                    "net_gex": float(row[1]) if row[1] else None,
                    "net_dex": float(row[2]) if row[2] else None,
                    "call_wall": float(row[3]) if row[3] else None,
                    "put_wall": float(row[4]) if row[4] else None,
                    "gamma_flip": float(row[5]) if row[5] else None,
                    "gex_spot": float(row[6]) if row[6] else None,
                    "contracts_analyzed": int(row[7]) if row[7] else None,
                }
            cur.close()
            conn.close()
            self._gex_cache_loaded = True
            logger.info(f"Loaded GEX cache: {len(self._gex_cache)} symbols")
        except Exception as e:
            logger.warning(f"Failed to load GEX cache: {e}")

    def _lookup_gex(self, symbol: str) -> Optional[Dict]:
        """Lookup latest GEX metrics for a symbol from cache."""
        if not self._gex_cache_loaded:
            self._load_gex_cache()
        return self._gex_cache.get(symbol)

    def create_signal(
        self,
        symbol: str,
        score: int,
        notional: float,
        contracts: int,
        price: float,
        trend: int,
        # Score breakdown
        ratio: float = 0,
        call_pct: float = 0,
        sweep_pct: float = 0,
        num_strikes: int = 0,
        score_volume: int = 0,
        score_call_pct: int = 0,
        score_sweep: int = 0,
        score_strikes: int = 0,
        score_notional: int = 0,
    ) -> Signal:
        """
        Create a Signal object from aggregated data.

        Args:
            symbol: Stock symbol
            score: UOA score
            notional: Total notional value
            contracts: Number of contracts
            price: Current price
            trend: Trend indicator (1=up, -1=down)
            ratio: Volume ratio vs baseline
            call_pct: Percentage of call volume
            sweep_pct: Percentage of sweep volume
            num_strikes: Number of unique strikes
            score_*: Individual score components

        Returns:
            Signal object with TA indicators populated
        """
        # Get TA using smart lookup (intraday vs daily based on time)
        ta = self._get_ta_for_symbol(symbol)

        # Use TA cache's trend (based on price vs SMA20) if available
        # This is more reliable than the aggregator's intraday trend
        ta_trend = ta.get("trend")
        final_trend = ta_trend if ta_trend is not None else trend

        # Price should be set by caller (create_signal_async fetches from Alpaca)
        # Only use last_close as last resort for sync callers
        effective_price = price if price and price > 0 else ta.get("last_close", 0)

        return Signal(
            symbol=symbol,
            detection_time=datetime.now(),
            score=score,
            notional=notional,
            contracts=contracts,
            rsi_14_prior=ta.get("rsi_14"),
            macd_hist_prior=ta.get("macd_hist"),
            sma_20_prior=ta.get("sma_20"),
            sma_50_prior=ta.get("sma_50"),  # Multi-week momentum
            price_at_signal=effective_price,
            trend=final_trend,
            # Additional context
            ratio=ratio,
            call_pct=call_pct,
            sweep_pct=sweep_pct,
            num_strikes=num_strikes,
            # Score breakdown
            score_volume=score_volume,
            score_call_pct=score_call_pct,
            score_sweep=score_sweep,
            score_strikes=score_strikes,
            score_notional=score_notional,
        )

    def update_ta_cache(self, symbol: str, ta_data: Dict):
        """Update TA cache for a symbol."""
        self.ta_cache[symbol] = ta_data

    def load_ta_cache(self, ta_data: Dict[str, Dict]):
        """Load bulk TA data into cache."""
        self.ta_cache.update(ta_data)


if __name__ == "__main__":
    # Test the filter
    print("Signal Filter Test")
    print("=" * 60)

    config = TradingConfig()
    filter = SignalFilter(config)

    # Test signals
    signals = [
        Signal("AAPL", datetime.now(), 12, 75000, 500, rsi_14_prior=35, trend=1),
        Signal("TSLA", datetime.now(), 8, 100000, 300, rsi_14_prior=45, trend=1),
        Signal("NVDA", datetime.now(), 15, 60000, 400, rsi_14_prior=55, trend=1),
        Signal("AMD", datetime.now(), 11, 40000, 200, rsi_14_prior=40, trend=1),
        Signal("MSFT", datetime.now(), 10, 80000, 350, rsi_14_prior=48, trend=-1),
    ]

    for signal in signals:
        result = filter.apply(signal)
        print(f"\n{result.summary}")

    print(f"\n{'-'*60}")
    print(f"Stats: {filter.get_stats()}")
