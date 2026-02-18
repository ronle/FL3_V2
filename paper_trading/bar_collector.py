"""
Intraday 1-Minute Bar Collector

Fetches latest 1-min OHLCV bars for tracked symbols every 60 seconds
and stores them in spot_prices_1m table via asyncpg UPSERT.

Follows BucketAggregator pattern: in-memory buffer, periodic DB flush.

Uses Alpaca /v2/stocks/bars/latest endpoint — returns exactly 1 bar
per symbol per request (100 symbols/batch, SIP feed).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

ALPACA_DATA_URL = "https://data.alpaca.markets/v2"
MAX_SYMBOLS_PER_REQUEST = 100


@dataclass
class BarRecord:
    """Single 1-min bar ready for DB insertion."""
    symbol: str
    bar_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    trade_count: Optional[int] = None


class IntradayBarCollector:
    """
    Collects 1-min OHLCV bars for tracked symbols.

    Runs every 60 seconds during market hours:
    1. Fetch latest 1-min bar via /v2/stocks/bars/latest
    2. Buffer in memory
    3. Flush to spot_prices_1m via asyncpg UPSERT
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        db_pool=None,
        max_batches: int = 2,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.db_pool = db_pool
        self.max_batches = max_batches

        self._session: Optional[aiohttp.ClientSession] = None
        self._buffer: list[BarRecord] = []

        # Metrics
        self._total_fetches = 0
        self._total_bars_collected = 0
        self._total_flushes = 0
        self._total_rows_inserted = 0
        self._total_errors = 0
        self._last_collect_ts: Optional[datetime] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "APCA-API-KEY-ID": self.api_key,
                    "APCA-API-SECRET-KEY": self.secret_key,
                }
            )
        return self._session

    async def collect(self, symbols: list[str]) -> int:
        """
        Fetch latest 1-min bar for symbols, buffer, and flush to DB.

        Returns:
            Number of bars collected this cycle
        """
        if not symbols:
            return 0

        # Limit to max_batches * 100 symbols
        max_symbols = self.max_batches * MAX_SYMBOLS_PER_REQUEST
        if len(symbols) > max_symbols:
            symbols = symbols[:max_symbols]

        bars_collected = 0
        for i in range(0, len(symbols), MAX_SYMBOLS_PER_REQUEST):
            batch = symbols[i:i + MAX_SYMBOLS_PER_REQUEST]
            try:
                bars = await self._fetch_latest_bars(batch)
                self._buffer.extend(bars)
                bars_collected += len(bars)
            except Exception as e:
                logger.error(f"Bar collection batch {i // MAX_SYMBOLS_PER_REQUEST + 1} failed: {e}")
                self._total_errors += 1

        self._total_fetches += 1
        self._total_bars_collected += bars_collected
        self._last_collect_ts = datetime.utcnow()

        # Flush buffer to DB
        if self._buffer:
            await self.flush()

        return bars_collected

    async def _fetch_latest_bars(self, symbols: list[str]) -> list[BarRecord]:
        """
        Fetch latest 1-min bars via /v2/stocks/bars/latest endpoint.

        Returns exactly 1 bar per symbol, no pagination needed.
        """
        session = await self._get_session()
        url = f"{ALPACA_DATA_URL}/stocks/bars/latest"
        params = {
            "symbols": ",".join(symbols),
            "feed": "sip",
        }

        try:
            async with session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    bars_data = data.get("bars", {})

                    records = []
                    for sym, bar in bars_data.items():
                        try:
                            records.append(BarRecord(
                                symbol=sym,
                                bar_ts=datetime.fromisoformat(
                                    bar["t"].replace("Z", "+00:00")
                                ),
                                open=float(bar["o"]),
                                high=float(bar["h"]),
                                low=float(bar["l"]),
                                close=float(bar["c"]),
                                volume=int(bar["v"]),
                                vwap=float(bar["vw"]) if "vw" in bar else None,
                                trade_count=int(bar["n"]) if "n" in bar else None,
                            ))
                        except (KeyError, ValueError) as e:
                            logger.warning(f"Failed to parse bar for {sym}: {e}")

                    return records

                elif response.status == 429:
                    logger.warning("Rate limited by Alpaca on bars/latest, will retry next cycle")
                    self._total_errors += 1
                    return []
                else:
                    text = await response.text()
                    logger.error(f"Alpaca bars/latest error {response.status}: {text}")
                    self._total_errors += 1
                    return []

        except asyncio.TimeoutError:
            logger.warning("Alpaca bars/latest request timed out (15s)")
            self._total_errors += 1
            return []
        except Exception as e:
            logger.error(f"Alpaca bars/latest request failed: {e}")
            self._total_errors += 1
            return []

    async def flush(self) -> int:
        """
        Flush buffered bars to spot_prices_1m table.

        Returns:
            Number of rows inserted
        """
        if not self._buffer:
            return 0

        bars_to_insert = list(self._buffer)
        self._buffer.clear()

        if not self.db_pool:
            logger.warning("No db_pool, discarding %d bars", len(bars_to_insert))
            return 0

        try:
            async with self.db_pool.acquire() as conn:
                rows = [
                    (b.symbol, b.bar_ts, b.open, b.high, b.low, b.close,
                     b.volume, b.vwap, b.trade_count)
                    for b in bars_to_insert
                ]

                await conn.executemany("""
                    INSERT INTO spot_prices_1m
                        (symbol, bar_ts, open, high, low, close, volume, vwap, trade_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (symbol, bar_ts) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        vwap = EXCLUDED.vwap,
                        trade_count = EXCLUDED.trade_count
                """, rows)

            self._total_flushes += 1
            self._total_rows_inserted += len(rows)
            logger.info(f"Bar collector: flushed {len(rows)} bars to spot_prices_1m")
            return len(rows)

        except Exception as e:
            logger.error(f"Bar collector flush failed: {e}")
            self._buffer.extend(bars_to_insert)
            self._total_errors += 1
            return 0

    async def run_retention(self, days: int = 7) -> int:
        """
        Delete bars older than `days` days.
        Called once daily (e.g., on daily reset).

        Returns:
            Number of rows deleted
        """
        if not self.db_pool:
            return 0

        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM spot_prices_1m
                    WHERE bar_ts < NOW() - INTERVAL '1 day' * $1
                """, days)
                deleted = int(result.split()[-1]) if result else 0
                if deleted > 0:
                    logger.info(f"Bar collector retention: deleted {deleted} rows older than {days} days")
                return deleted
        except Exception as e:
            logger.error(f"Bar collector retention failed: {e}")
            return 0

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def get_metrics(self) -> dict:
        """Get collector metrics."""
        return {
            "total_fetches": self._total_fetches,
            "total_bars_collected": self._total_bars_collected,
            "total_flushes": self._total_flushes,
            "total_rows_inserted": self._total_rows_inserted,
            "total_errors": self._total_errors,
            "buffer_size": len(self._buffer),
            "last_collect_ts": (
                self._last_collect_ts.isoformat() if self._last_collect_ts else None
            ),
        }
