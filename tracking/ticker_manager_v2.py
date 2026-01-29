"""
Tracked Tickers V2 Manager (Component 4.1)

Manages permanent symbol tracking list:
- Symbols are added on UOA trigger and NEVER removed
- Supports batched retrieval for TA pipeline
- Tracks trigger counts and timestamps

Usage:
    manager = TrackedTickersManager(db_pool)
    await manager.add_symbol("AAPL", trigger_ts)
    symbols = await manager.get_active_symbols()
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrackedTicker:
    """Tracked ticker data."""
    symbol: str
    first_trigger_ts: datetime
    trigger_count: int
    last_trigger_ts: datetime
    ta_enabled: bool
    created_at: datetime


class TrackedTickersManager:
    """
    Manages the permanent tracking list for V2.

    Key principle: Once a symbol triggers UOA, it's tracked forever.
    This ensures we never miss follow-up activity on previous triggers.

    Usage:
        manager = TrackedTickersManager(db_pool)

        # On UOA trigger
        await manager.add_symbol("AAPL", trigger_ts)

        # For TA pipeline
        symbols = await manager.get_active_symbols()
        batches = await manager.get_symbols_for_refresh(batch_size=50)
    """

    def __init__(self, db_pool=None):
        """
        Initialize manager.

        Args:
            db_pool: asyncpg connection pool
        """
        self.db_pool = db_pool
        self._cache: dict[str, TrackedTicker] = {}
        self._cache_loaded = False

        # Metrics
        self._adds = 0
        self._updates = 0
        self._cache_hits = 0

    async def add_symbol(
        self,
        symbol: str,
        trigger_ts: datetime,
        ta_enabled: bool = True,
    ) -> bool:
        """
        Add or update a symbol in the tracking list.

        If symbol already exists, increments trigger_count and updates last_trigger_ts.
        If new, inserts with trigger_count=1.

        Args:
            symbol: Underlying symbol (e.g., "AAPL")
            trigger_ts: Timestamp of the UOA trigger
            ta_enabled: Whether TA should be calculated for this symbol

        Returns:
            True if successfully added/updated
        """
        if not self.db_pool:
            logger.warning("No db_pool, tracking in memory only")
            self._add_to_cache(symbol, trigger_ts, ta_enabled)
            return True

        try:
            async with self.db_pool.acquire() as conn:
                # Upsert: insert or update trigger count
                result = await conn.execute("""
                    INSERT INTO tracked_tickers_v2
                    (symbol, first_trigger_ts, trigger_count, last_trigger_ts, ta_enabled)
                    VALUES ($1, $2, 1, $2, $3)
                    ON CONFLICT (symbol) DO UPDATE SET
                        trigger_count = tracked_tickers_v2.trigger_count + 1,
                        last_trigger_ts = $2,
                        updated_at = NOW()
                    RETURNING trigger_count
                """, symbol, trigger_ts, ta_enabled)

                # Check if it was insert or update
                count = int(result.split()[-1]) if result else 1
                if count == 1:
                    self._adds += 1
                    logger.info(f"Added new tracked symbol: {symbol}")
                else:
                    self._updates += 1
                    logger.debug(f"Updated tracked symbol: {symbol} (count={count})")

                # Update cache
                self._add_to_cache(symbol, trigger_ts, ta_enabled)
                return True

        except Exception as e:
            logger.error(f"Failed to add symbol {symbol}: {e}")
            return False

    def _add_to_cache(
        self,
        symbol: str,
        trigger_ts: datetime,
        ta_enabled: bool,
    ) -> None:
        """Add symbol to in-memory cache."""
        if symbol in self._cache:
            ticker = self._cache[symbol]
            ticker.trigger_count += 1
            ticker.last_trigger_ts = trigger_ts
        else:
            self._cache[symbol] = TrackedTicker(
                symbol=symbol,
                first_trigger_ts=trigger_ts,
                trigger_count=1,
                last_trigger_ts=trigger_ts,
                ta_enabled=ta_enabled,
                created_at=datetime.now(),
            )

    async def get_active_symbols(self, ta_enabled_only: bool = True) -> list[str]:
        """
        Get all active tracked symbols.

        Args:
            ta_enabled_only: If True, only return symbols with ta_enabled=True

        Returns:
            List of symbol strings
        """
        if not self.db_pool:
            if ta_enabled_only:
                return [s for s, t in self._cache.items() if t.ta_enabled]
            return list(self._cache.keys())

        try:
            async with self.db_pool.acquire() as conn:
                if ta_enabled_only:
                    rows = await conn.fetch("""
                        SELECT symbol FROM tracked_tickers_v2
                        WHERE ta_enabled = TRUE
                        ORDER BY symbol
                    """)
                else:
                    rows = await conn.fetch("""
                        SELECT symbol FROM tracked_tickers_v2
                        ORDER BY symbol
                    """)

                return [row['symbol'] for row in rows]

        except Exception as e:
            logger.error(f"Failed to get active symbols: {e}")
            return []

    async def get_symbols_for_refresh(
        self,
        batch_size: int = 50,
    ) -> list[list[str]]:
        """
        Get symbols batched for TA refresh.

        Splits the active symbols into batches suitable for Alpaca API calls.

        Args:
            batch_size: Number of symbols per batch (default 50 for Alpaca)

        Returns:
            List of symbol batches
        """
        symbols = await self.get_active_symbols(ta_enabled_only=True)

        if not symbols:
            return []

        # Split into batches
        batches = []
        for i in range(0, len(symbols), batch_size):
            batches.append(symbols[i:i + batch_size])

        logger.debug(f"Created {len(batches)} batches of ~{batch_size} symbols")
        return batches

    async def get_symbol_details(self, symbol: str) -> Optional[TrackedTicker]:
        """
        Get full details for a tracked symbol.

        Args:
            symbol: Symbol to look up

        Returns:
            TrackedTicker or None if not found
        """
        # Check cache first
        if symbol in self._cache:
            self._cache_hits += 1
            return self._cache[symbol]

        if not self.db_pool:
            return None

        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT symbol, first_trigger_ts, trigger_count,
                           last_trigger_ts, ta_enabled, created_at
                    FROM tracked_tickers_v2
                    WHERE symbol = $1
                """, symbol)

                if row:
                    ticker = TrackedTicker(
                        symbol=row['symbol'],
                        first_trigger_ts=row['first_trigger_ts'],
                        trigger_count=row['trigger_count'],
                        last_trigger_ts=row['last_trigger_ts'],
                        ta_enabled=row['ta_enabled'],
                        created_at=row['created_at'],
                    )
                    self._cache[symbol] = ticker
                    return ticker

                return None

        except Exception as e:
            logger.error(f"Failed to get symbol details for {symbol}: {e}")
            return None

    async def set_ta_enabled(self, symbol: str, enabled: bool) -> bool:
        """
        Enable or disable TA for a symbol.

        Args:
            symbol: Symbol to update
            enabled: Whether TA should be enabled

        Returns:
            True if updated successfully
        """
        if not self.db_pool:
            if symbol in self._cache:
                self._cache[symbol].ta_enabled = enabled
                return True
            return False

        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE tracked_tickers_v2
                    SET ta_enabled = $2, updated_at = NOW()
                    WHERE symbol = $1
                """, symbol, enabled)

                if symbol in self._cache:
                    self._cache[symbol].ta_enabled = enabled

                return 'UPDATE 1' in result

        except Exception as e:
            logger.error(f"Failed to set ta_enabled for {symbol}: {e}")
            return False

    async def get_count(self) -> dict:
        """
        Get counts of tracked symbols.

        Returns:
            Dict with total and ta_enabled counts
        """
        if not self.db_pool:
            total = len(self._cache)
            ta_enabled = sum(1 for t in self._cache.values() if t.ta_enabled)
            return {"total": total, "ta_enabled": ta_enabled}

        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE ta_enabled) as ta_enabled
                    FROM tracked_tickers_v2
                """)

                return {
                    "total": row['total'],
                    "ta_enabled": row['ta_enabled'],
                }

        except Exception as e:
            logger.error(f"Failed to get count: {e}")
            return {"total": 0, "ta_enabled": 0}

    async def load_cache(self) -> int:
        """
        Load all tracked symbols into cache.

        Useful for startup to avoid repeated DB queries.

        Returns:
            Number of symbols loaded
        """
        if not self.db_pool:
            return len(self._cache)

        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT symbol, first_trigger_ts, trigger_count,
                           last_trigger_ts, ta_enabled, created_at
                    FROM tracked_tickers_v2
                """)

                for row in rows:
                    self._cache[row['symbol']] = TrackedTicker(
                        symbol=row['symbol'],
                        first_trigger_ts=row['first_trigger_ts'],
                        trigger_count=row['trigger_count'],
                        last_trigger_ts=row['last_trigger_ts'],
                        ta_enabled=row['ta_enabled'],
                        created_at=row['created_at'],
                    )

                self._cache_loaded = True
                logger.info(f"Loaded {len(rows)} tracked symbols into cache")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return 0

    def get_metrics(self) -> dict:
        """Get manager metrics."""
        return {
            "adds": self._adds,
            "updates": self._updates,
            "cache_size": len(self._cache),
            "cache_hits": self._cache_hits,
            "cache_loaded": self._cache_loaded,
        }


if __name__ == "__main__":
    print("Tracked Tickers V2 Manager Tests")
    print("=" * 60)

    async def test_manager():
        manager = TrackedTickersManager()

        # Test adding symbols
        now = datetime.now()
        await manager.add_symbol("AAPL", now)
        await manager.add_symbol("TSLA", now)
        await manager.add_symbol("NVDA", now)
        await manager.add_symbol("AAPL", now)  # Duplicate - should increment

        print(f"\nSymbols added. Metrics: {manager.get_metrics()}")

        # Test retrieval
        symbols = await manager.get_active_symbols()
        print(f"Active symbols: {symbols}")

        # Test batching
        batches = await manager.get_symbols_for_refresh(batch_size=2)
        print(f"Batches (size=2): {batches}")

        # Test details
        aapl = await manager.get_symbol_details("AAPL")
        print(f"\nAAPL details: trigger_count={aapl.trigger_count}")

        # Test counts
        counts = await manager.get_count()
        print(f"Counts: {counts}")

        # Test disable
        await manager.set_ta_enabled("NVDA", False)
        symbols = await manager.get_active_symbols(ta_enabled_only=True)
        print(f"After disabling NVDA: {symbols}")

    asyncio.run(test_manager())
