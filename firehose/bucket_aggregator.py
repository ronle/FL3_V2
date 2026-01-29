"""
Bucket Aggregator (Component 3.5)

Stores 30-minute bucket summaries for baseline refinement.
Features:
- Accumulates per-symbol stats for 30-min windows
- Flushes to database at bucket boundaries
- Sparse storage (only symbols with activity)
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

BUCKET_MINUTES = 30


@dataclass
class BucketStats:
    """Stats for a single 30-minute bucket."""
    symbol: str
    trade_date: date
    bucket_start: dt_time
    prints: int = 0
    notional: float = 0.0
    contracts: int = 0
    unique_options: set = field(default_factory=set)

    @property
    def contracts_unique(self) -> int:
        return len(self.unique_options)


class BucketAggregator:
    """
    Aggregates trades into 30-minute buckets for baseline storage.

    At bucket boundaries, flushes accumulated stats to database.

    Usage:
        agg = BucketAggregator(db_pool)
        agg.add_trade(underlying, option_symbol, price, size)
        # ... at bucket boundary:
        await agg.flush()
    """

    def __init__(
        self,
        db_pool=None,
        bucket_minutes: int = BUCKET_MINUTES,
        auto_flush: bool = True,
    ):
        """
        Initialize bucket aggregator.

        Args:
            db_pool: Database connection pool
            bucket_minutes: Bucket size in minutes
            auto_flush: Whether to auto-flush at boundaries
        """
        self.db_pool = db_pool
        self.bucket_minutes = bucket_minutes
        self.auto_flush = auto_flush

        # Current bucket data: symbol -> BucketStats
        self._current_bucket: dict[str, BucketStats] = {}
        self._current_bucket_start: Optional[dt_time] = None
        self._current_date: Optional[date] = None

        # Metrics
        self._total_trades = 0
        self._total_flushes = 0
        self._total_rows_inserted = 0

    def _get_bucket_start(self, dt: datetime) -> dt_time:
        """Get bucket start time for a datetime."""
        minute = (dt.minute // self.bucket_minutes) * self.bucket_minutes
        return dt_time(dt.hour, minute)

    def _check_bucket_boundary(self, now: datetime) -> bool:
        """Check if we've crossed into a new bucket."""
        current_bucket = self._get_bucket_start(now)
        current_date = now.date()

        if self._current_bucket_start is None:
            self._current_bucket_start = current_bucket
            self._current_date = current_date
            return False

        # New bucket if date changed or bucket changed
        if current_date != self._current_date or current_bucket != self._current_bucket_start:
            return True

        return False

    def add_trade(
        self,
        underlying: str,
        option_symbol: str,
        price: float,
        size: int,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Add a trade to the current bucket.

        Args:
            underlying: Underlying symbol
            option_symbol: OCC option symbol
            price: Trade price
            size: Number of contracts
            timestamp: Trade timestamp (defaults to now)

        Returns:
            True if bucket boundary was crossed (flush needed)
        """
        now = timestamp or datetime.now()
        self._total_trades += 1

        # Check for bucket boundary
        boundary_crossed = self._check_bucket_boundary(now)

        # Initialize bucket if needed
        bucket_start = self._get_bucket_start(now)
        trade_date = now.date()

        if underlying not in self._current_bucket:
            self._current_bucket[underlying] = BucketStats(
                symbol=underlying,
                trade_date=trade_date,
                bucket_start=bucket_start,
            )

        # Accumulate stats
        stats = self._current_bucket[underlying]
        stats.prints += 1
        stats.notional += price * size * 100
        stats.contracts += size
        stats.unique_options.add(option_symbol)

        # Update current bucket tracking
        if boundary_crossed:
            self._current_bucket_start = bucket_start
            self._current_date = trade_date

        return boundary_crossed

    async def flush(self) -> int:
        """
        Flush current bucket to database.

        Returns:
            Number of rows inserted
        """
        if not self._current_bucket:
            return 0

        buckets_to_insert = list(self._current_bucket.values())
        self._current_bucket.clear()

        if not self.db_pool:
            logger.warning("No db_pool, skipping flush")
            return 0

        try:
            async with self.db_pool.acquire() as conn:
                # Batch insert
                rows = [
                    (b.symbol, b.trade_date, b.bucket_start, b.prints, b.notional, b.contracts_unique)
                    for b in buckets_to_insert
                ]

                await conn.executemany("""
                    INSERT INTO intraday_baselines_30m
                    (symbol, trade_date, bucket_start, prints, notional, contracts_unique)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (symbol, trade_date, bucket_start)
                    DO UPDATE SET
                        prints = intraday_baselines_30m.prints + EXCLUDED.prints,
                        notional = intraday_baselines_30m.notional + EXCLUDED.notional,
                        contracts_unique = GREATEST(intraday_baselines_30m.contracts_unique, EXCLUDED.contracts_unique)
                """, rows)

            self._total_flushes += 1
            self._total_rows_inserted += len(rows)

            logger.info(f"Flushed {len(rows)} bucket rows to database")
            return len(rows)

        except Exception as e:
            logger.error(f"Bucket flush failed: {e}")
            # Re-add to bucket for retry
            for b in buckets_to_insert:
                self._current_bucket[b.symbol] = b
            return 0

    def get_pending_count(self) -> int:
        """Get number of symbols pending flush."""
        return len(self._current_bucket)

    def get_current_bucket_info(self) -> dict:
        """Get info about current bucket."""
        return {
            "bucket_start": self._current_bucket_start.strftime("%H:%M") if self._current_bucket_start else None,
            "trade_date": self._current_date.isoformat() if self._current_date else None,
            "symbols_count": len(self._current_bucket),
            "total_prints": sum(b.prints for b in self._current_bucket.values()),
            "total_notional": sum(b.notional for b in self._current_bucket.values()),
        }

    def get_metrics(self) -> dict:
        """Get aggregator metrics."""
        return {
            "total_trades": self._total_trades,
            "total_flushes": self._total_flushes,
            "total_rows_inserted": self._total_rows_inserted,
            "pending_symbols": len(self._current_bucket),
            "bucket_minutes": self.bucket_minutes,
        }


if __name__ == "__main__":
    print("Bucket Aggregator Tests")
    print("=" * 60)

    agg = BucketAggregator()

    # Simulate trades
    symbols = ["AAPL", "TSLA", "NVDA"]
    now = datetime.now()

    print(f"\nSimulating trades at {now.strftime('%H:%M')}...")

    for i in range(1000):
        symbol = symbols[i % len(symbols)]
        agg.add_trade(
            underlying=symbol,
            option_symbol=f"O:{symbol}250117C00150000",
            price=5.0,
            size=10,
            timestamp=now,
        )

    print(f"\nCurrent bucket info:")
    info = agg.get_current_bucket_info()
    for k, v in info.items():
        print(f"  {k}: {v}")

    print(f"\nMetrics: {agg.get_metrics()}")

    # Show per-symbol stats
    print("\nPer-symbol stats:")
    for symbol, stats in agg._current_bucket.items():
        print(f"  {symbol}: {stats.prints} prints, ${stats.notional:,.0f}, {stats.contracts_unique} contracts")
