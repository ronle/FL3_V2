"""
Baseline Manager (Component 2.2)

Manages hybrid baseline strategy for UOA detection:
- Cold start (days 1-30): Use ORATS daily volume with time multipliers
- Warm (days 30+): Use 20-day rolling bucket averages
- Hybrid: Prefer bucket history, fall back to ORATS

Performance: Caches baselines for frequently accessed symbols.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date, time, datetime, timedelta
from pathlib import Path
from typing import Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

# Default time-of-day multipliers (U-shaped intraday pattern)
DEFAULT_MULTIPLIERS = {
    "09:30": 3.0,
    "10:00": 1.8,
    "10:30": 1.4,
    "11:00": 1.1,
    "11:30": 0.8,
    "12:00": 0.6,
    "12:30": 0.5,
    "13:00": 0.6,
    "13:30": 0.8,
    "14:00": 1.0,
    "14:30": 1.1,
    "15:00": 1.3,
    "15:30": 2.0,
}

TRADING_MINUTES = 390  # 9:30 AM to 4:00 PM
BUCKET_MINUTES = 30
BASELINE_LOOKBACK_DAYS = 20
DEFAULT_BASELINE = 1000  # Conservative default when no data


@dataclass
class Baseline:
    """Baseline data for a symbol at a specific time bucket."""
    symbol: str
    bucket_start: time
    expected_notional: float
    expected_prints: int
    source: str  # 'bucket_history', 'orats', 'default'
    confidence: float  # 0-1, based on data quality


class BaselineManager:
    """
    Manages baseline calculations with hybrid strategy.

    Usage:
        manager = BaselineManager(db_pool, config_path='config/time_multipliers.json')
        baseline = await manager.get_baseline('AAPL', time(10, 0))
        ratio = actual_volume / baseline.expected_notional
    """

    def __init__(self, db_pool=None, config_path: Optional[str] = None):
        """
        Initialize baseline manager.

        Args:
            db_pool: asyncpg connection pool for database queries
            config_path: Path to time_multipliers.json config
        """
        self.db_pool = db_pool
        self.multipliers = self._load_multipliers(config_path)
        self._cache = {}  # Symbol -> bucket -> Baseline
        self._orats_cache = {}  # Symbol -> daily volume

    def _load_multipliers(self, config_path: Optional[str]) -> dict[str, float]:
        """Load time multipliers from config file."""
        if config_path:
            try:
                path = Path(config_path)
                if path.exists():
                    with open(path) as f:
                        config = json.load(f)
                        return config.get('multipliers', DEFAULT_MULTIPLIERS)
            except Exception as e:
                logger.warning(f"Failed to load multipliers from {config_path}: {e}")

        return DEFAULT_MULTIPLIERS

    def get_multiplier(self, bucket_start: time) -> float:
        """Get time-of-day multiplier for a bucket."""
        key = bucket_start.strftime("%H:%M")
        return self.multipliers.get(key, 1.0)

    def _bucket_to_key(self, bucket_start: time) -> str:
        """Convert bucket time to cache key."""
        return bucket_start.strftime("%H:%M")

    async def get_baseline(
        self,
        symbol: str,
        bucket_start: time,
        trade_date: Optional[date] = None
    ) -> Baseline:
        """
        Get baseline for symbol at specific time bucket.

        Strategy:
        1. Check cache first
        2. Try bucket history (20-day rolling average)
        3. Fall back to ORATS-derived baseline
        4. Use default if no data available

        Args:
            symbol: Underlying ticker symbol
            bucket_start: Start time of 30-min bucket (e.g., time(10, 0))
            trade_date: Date for baseline calculation (default: today)

        Returns:
            Baseline object with expected values and source info
        """
        if trade_date is None:
            trade_date = date.today()

        cache_key = f"{symbol}:{self._bucket_to_key(bucket_start)}"

        # Check cache (invalidate daily)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if hasattr(cached, '_cache_date') and cached._cache_date == trade_date:
                return cached

        # Try bucket history first
        baseline = await self._get_bucket_baseline(symbol, bucket_start, trade_date)
        if baseline:
            baseline._cache_date = trade_date
            self._cache[cache_key] = baseline
            return baseline

        # Fall back to ORATS
        baseline = await self._get_orats_baseline(symbol, bucket_start)
        if baseline:
            baseline._cache_date = trade_date
            self._cache[cache_key] = baseline
            return baseline

        # No data - use default
        logger.warning(f"No baseline data for {symbol}, using default")
        baseline = Baseline(
            symbol=symbol,
            bucket_start=bucket_start,
            expected_notional=DEFAULT_BASELINE,
            expected_prints=10,
            source='default',
            confidence=0.1
        )
        baseline._cache_date = trade_date
        self._cache[cache_key] = baseline
        return baseline

    async def _get_bucket_baseline(
        self,
        symbol: str,
        bucket_start: time,
        trade_date: date
    ) -> Optional[Baseline]:
        """Get baseline from 20-day bucket history."""
        if not self.db_pool:
            return None

        query = """
            SELECT
                AVG(notional) as avg_notional,
                AVG(prints) as avg_prints,
                COUNT(*) as days_count
            FROM intraday_baselines_30m
            WHERE symbol = $1
              AND bucket_start = $2
              AND trade_date > $3
              AND trade_date < $4
        """

        lookback_start = trade_date - timedelta(days=BASELINE_LOOKBACK_DAYS + 1)

        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    query, symbol, bucket_start, lookback_start, trade_date
                )

                if row and row['days_count'] and row['days_count'] >= 5:
                    # Need at least 5 days for reasonable baseline
                    confidence = min(row['days_count'] / BASELINE_LOOKBACK_DAYS, 1.0)
                    return Baseline(
                        symbol=symbol,
                        bucket_start=bucket_start,
                        expected_notional=float(row['avg_notional'] or 0),
                        expected_prints=int(row['avg_prints'] or 0),
                        source='bucket_history',
                        confidence=confidence
                    )
        except Exception as e:
            logger.error(f"Error fetching bucket baseline for {symbol}: {e}")

        return None

    async def _get_orats_baseline(
        self,
        symbol: str,
        bucket_start: time
    ) -> Optional[Baseline]:
        """Get baseline derived from ORATS daily volume."""
        if not self.db_pool:
            return None

        # Check cache first
        if symbol in self._orats_cache:
            daily_volume = self._orats_cache[symbol]
        else:
            query = """
                SELECT total_volume, avg_daily_volume
                FROM orats_daily
                WHERE symbol = $1
                ORDER BY asof_date DESC
                LIMIT 1
            """

            try:
                async with self.db_pool.acquire() as conn:
                    row = await conn.fetchrow(query, symbol)
                    if row:
                        # Use avg_daily_volume if available, else total_volume
                        daily_volume = row['avg_daily_volume'] or row['total_volume'] or 0
                        self._orats_cache[symbol] = daily_volume
                    else:
                        return None
            except Exception as e:
                logger.error(f"Error fetching ORATS data for {symbol}: {e}")
                return None

        if not daily_volume or daily_volume <= 0:
            return None

        # Calculate bucket baseline
        # Formula: (daily_volume / trading_minutes) * bucket_minutes * multiplier
        multiplier = self.get_multiplier(bucket_start)
        per_minute = daily_volume / TRADING_MINUTES
        bucket_base = per_minute * BUCKET_MINUTES
        expected_notional = bucket_base * multiplier

        # Estimate prints (assume avg 10 contracts per print)
        expected_prints = int(expected_notional / 10)

        return Baseline(
            symbol=symbol,
            bucket_start=bucket_start,
            expected_notional=expected_notional,
            expected_prints=max(expected_prints, 1),
            source='orats',
            confidence=0.5  # Lower confidence than bucket history
        )

    def get_baseline_sync(
        self,
        symbol: str,
        bucket_start: time,
        orats_daily_volume: Optional[int] = None
    ) -> Baseline:
        """
        Synchronous baseline calculation (for non-async contexts).
        Uses ORATS volume directly if provided.
        """
        if orats_daily_volume and orats_daily_volume > 0:
            multiplier = self.get_multiplier(bucket_start)
            per_minute = orats_daily_volume / TRADING_MINUTES
            bucket_base = per_minute * BUCKET_MINUTES
            expected_notional = bucket_base * multiplier

            return Baseline(
                symbol=symbol,
                bucket_start=bucket_start,
                expected_notional=expected_notional,
                expected_prints=max(int(expected_notional / 10), 1),
                source='orats',
                confidence=0.5
            )

        return Baseline(
            symbol=symbol,
            bucket_start=bucket_start,
            expected_notional=DEFAULT_BASELINE,
            expected_prints=10,
            source='default',
            confidence=0.1
        )

    def clear_cache(self):
        """Clear all cached baselines."""
        self._cache.clear()
        self._orats_cache.clear()

    def cache_stats(self) -> dict:
        """Get cache statistics."""
        return {
            'baseline_entries': len(self._cache),
            'orats_entries': len(self._orats_cache)
        }


def get_current_bucket() -> time:
    """Get the current 30-minute bucket start time."""
    now = datetime.now()
    minute = (now.minute // 30) * 30
    return time(now.hour, minute)


def get_all_buckets() -> list[time]:
    """Get all trading day bucket start times."""
    buckets = []
    hour, minute = 9, 30
    while hour < 16:
        buckets.append(time(hour, minute))
        minute += 30
        if minute >= 60:
            minute = 0
            hour += 1
    return buckets


if __name__ == "__main__":
    # Test without database
    manager = BaselineManager(config_path='config/time_multipliers.json')

    print("Baseline Manager Tests")
    print("=" * 60)

    # Test multipliers
    print("\nTime Multipliers:")
    for bucket in get_all_buckets():
        mult = manager.get_multiplier(bucket)
        print(f"  {bucket.strftime('%H:%M')}: {mult:.1f}x")

    # Test sync baseline calculation
    print("\nSync Baseline (AAPL, 100K daily volume):")
    for bucket in [time(9, 30), time(12, 0), time(15, 30)]:
        baseline = manager.get_baseline_sync('AAPL', bucket, orats_daily_volume=100_000)
        print(f"  {bucket.strftime('%H:%M')}: {baseline.expected_notional:,.0f} "
              f"(source: {baseline.source}, conf: {baseline.confidence:.1f})")

    print("\nCurrent bucket:", get_current_bucket())
    print("Cache stats:", manager.cache_stats())
