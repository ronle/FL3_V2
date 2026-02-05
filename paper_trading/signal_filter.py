"""
Signal Filter

Applies entry rules to determine if a signal should trigger a trade:
- Score >= 10
- Uptrend (price > 20d SMA)
- Prior-day RSI < 50
- $50K+ notional
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

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

    def _log_evaluation_sync(self, signal: Signal, passed: bool, rejection_reason: str):
        """Log signal evaluation to database for analysis."""
        if not self.database_url:
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO signal_evaluations (
                    symbol, detected_at, notional, ratio, call_pct, sweep_pct,
                    num_strikes, contracts, rsi_14, macd_histogram, trend,
                    score_volume, score_call_pct, score_sweep, score_strikes,
                    score_notional, score_total, passed_all_filters, rejection_reason
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
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

        # Check RSI
        if signal.rsi_14_prior is not None:
            if signal.rsi_14_prior >= self.config.RSI_THRESHOLD:
                reasons.append(f"RSI {signal.rsi_14_prior:.1f} >= {self.config.RSI_THRESHOLD}")
                self.filter_reasons["rsi"] += 1
        else:
            # No RSI data - skip this filter or fail?
            # For safety, fail if no RSI
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

        # Log to database (every evaluation)
        self._log_evaluation(signal, passed, rejection_reason)

        if passed:
            self.passed_signals += 1
            logger.info(
                f"Signal PASSED: {signal.symbol} "
                f"score={signal.score} RSI={signal.rsi_14_prior:.1f} "
                f"notional=${signal.notional:,.0f}"
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


class SignalGenerator:
    """
    Generates signals from firehose data.

    This is a simplified version that creates Signal objects from
    the aggregated trade data coming from the firehose.

    Features dynamic TA fetching for symbols not in cache.
    """

    def __init__(self, ta_cache: Optional[Dict] = None):
        """
        Initialize signal generator.

        Args:
            ta_cache: Pre-loaded prior-day TA data {symbol: {rsi, macd, sma}}
        """
        self.ta_cache = ta_cache or {}
        self._polygon_fetcher = None
        self._fetch_lock = asyncio.Lock()
        self._fetched_symbols: set = set()  # Track symbols we've already tried to fetch

    async def _get_polygon_fetcher(self):
        """Lazy-load Polygon fetcher."""
        if self._polygon_fetcher is None:
            from adapters.polygon_bars import PolygonBarsFetcher
            api_key = os.environ.get("POLYGON_API_KEY")
            if api_key:
                self._polygon_fetcher = PolygonBarsFetcher(api_key, requests_per_minute=5)
                logger.info("Initialized Polygon fetcher for dynamic TA")
            else:
                logger.warning("POLYGON_API_KEY not set, dynamic TA fetch disabled")
        return self._polygon_fetcher

    async def fetch_ta_for_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Fetch TA data for a symbol from Polygon (on-demand).

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

            fetcher = await self._get_polygon_fetcher()
            if not fetcher:
                return None

            try:
                logger.info(f"Fetching TA for {symbol} from Polygon...")
                bar_data = await fetcher.get_bars(symbol, days=70)  # Extended for 50d SMA

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

        If symbol is not in TA cache, fetches from Polygon in real-time.
        Fetches current price from Alpaca if aggregator price is 0.

        IMPORTANT: Uses strict timeouts to avoid blocking WebSocket ping/pong.

        Returns:
            Signal object if TA data available, None if critical data missing.
        """
        # Check if we need to fetch TA - use strict 3s timeout to avoid blocking
        if symbol not in self.ta_cache:
            try:
                await asyncio.wait_for(self.fetch_ta_for_symbol(symbol), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(f"TA fetch timeout for {symbol}, rejecting signal")
            except Exception as e:
                logger.warning(f"TA fetch failed for {symbol}: {e}, rejecting signal")

        # CRITICAL: Reject signal if we don't have required TA data
        # Without RSI and SMA data, we can't properly filter the signal
        ta = self.ta_cache.get(symbol, {})
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
                ta = self.ta_cache.get(symbol, {})
                effective_price = ta.get("last_close", 0)

        return self.create_signal(
            symbol, score, notional, contracts, effective_price, trend,
            ratio=ratio, call_pct=call_pct, sweep_pct=sweep_pct,
            num_strikes=num_strikes, score_volume=score_volume,
            score_call_pct=score_call_pct, score_sweep=score_sweep,
            score_strikes=score_strikes, score_notional=score_notional,
        )

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
        # Get prior-day TA from cache
        ta = self.ta_cache.get(symbol, {})

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
