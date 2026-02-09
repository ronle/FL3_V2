"""
Polygon Daily Bars Fetcher

Fetches daily OHLCV bars from Polygon.io REST API.
Used for pre-market TA calculations.

Usage:
    fetcher = PolygonBarsFetcher(api_key)
    bars = await fetcher.get_bars_batch(["AAPL", "TSLA", "NVDA"], days=40)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import aiohttp

logger = logging.getLogger(__name__)

POLYGON_API_URL = "https://api.polygon.io"


@dataclass
class Bar:
    """Single OHLCV bar."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    trade_count: Optional[int] = None


@dataclass
class BarData:
    """Collection of bars for a symbol."""
    symbol: str
    bars: List[Bar]
    latest_price: Optional[float] = None
    latest_volume: Optional[int] = None

    @property
    def has_data(self) -> bool:
        return len(self.bars) > 0


class PolygonBarsFetcher:
    """
    Fetches daily bars from Polygon.io API.

    Usage:
        fetcher = PolygonBarsFetcher(api_key)
        bars = await fetcher.get_bars_batch(
            symbols=["AAPL", "TSLA"],
            days=40
        )
    """

    def __init__(self, api_key: str, requests_per_minute: int = 5):
        """
        Initialize fetcher.

        Args:
            api_key: Polygon API key
            requests_per_minute: Rate limit (free tier = 5/min)
        """
        self.api_key = api_key
        self.request_interval = 60 / requests_per_minute
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0

        # Metrics
        self._total_requests = 0
        self._total_symbols = 0
        self._errors = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _rate_limit(self) -> None:
        """Apply rate limiting."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.request_interval:
            await asyncio.sleep(self.request_interval - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def get_bars(
        self,
        symbol: str,
        days: int = 40,
        end_date: Optional[datetime] = None,
    ) -> BarData:
        """
        Get daily bars for a single symbol.

        Args:
            symbol: Stock symbol
            days: Number of days of history
            end_date: End date (default: today)

        Returns:
            BarData with list of bars
        """
        await self._rate_limit()

        session = await self._get_session()
        self._total_requests += 1
        self._total_symbols += 1

        if end_date is None:
            end_date = datetime.now()
        start_date = end_date - timedelta(days=days + 10)  # Extra buffer for weekends

        url = f"{POLYGON_API_URL}/v2/aggs/ticker/{symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"apiKey": self.api_key, "limit": days + 10}

        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_response(symbol, data)
                elif response.status == 429:
                    logger.warning(f"Rate limited, waiting 60s...")
                    await asyncio.sleep(60)
                    return await self.get_bars(symbol, days, end_date)
                else:
                    text = await response.text()
                    logger.error(f"Polygon API error {response.status}: {text[:200]}")
                    self._errors += 1
                    return BarData(symbol=symbol, bars=[])
        except Exception as e:
            logger.error(f"Polygon request failed for {symbol}: {e}")
            self._errors += 1
            return BarData(symbol=symbol, bars=[])

    async def get_bars_batch(
        self,
        symbols: List[str],
        days: int = 40,
        end_date: Optional[datetime] = None,
        max_concurrent: int = 3,
    ) -> Dict[str, BarData]:
        """
        Get daily bars for multiple symbols.

        Args:
            symbols: List of stock symbols
            days: Number of days of history
            end_date: End date (default: today)
            max_concurrent: Max concurrent requests

        Returns:
            Dict mapping symbol to BarData
        """
        if not symbols:
            return {}

        results = {}
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(sym: str) -> tuple:
            async with semaphore:
                data = await self.get_bars(sym, days, end_date)
                return sym, data

        tasks = [fetch_with_semaphore(sym) for sym in symbols]
        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for result in completed:
            if isinstance(result, Exception):
                logger.error(f"Batch fetch error: {result}")
            else:
                sym, data = result
                results[sym] = data

        return results

    def _parse_response(self, symbol: str, data: dict) -> BarData:
        """Parse Polygon bars response."""
        bars = []

        results = data.get("results", [])
        for r in results:
            # Polygon timestamp is in milliseconds
            ts = datetime.fromtimestamp(r["t"] / 1000)
            bars.append(Bar(
                symbol=symbol,
                timestamp=ts,
                open=float(r["o"]),
                high=float(r["h"]),
                low=float(r["l"]),
                close=float(r["c"]),
                volume=int(r["v"]),
                vwap=float(r.get("vw")) if r.get("vw") else None,
                trade_count=int(r.get("n")) if r.get("n") else None,
            ))

        # Sort by timestamp
        bars.sort(key=lambda x: x.timestamp)

        bar_data = BarData(symbol=symbol, bars=bars)
        if bars:
            bar_data.latest_price = bars[-1].close
            bar_data.latest_volume = bars[-1].volume

        return bar_data

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def get_metrics(self) -> dict:
        """Get fetcher metrics."""
        return {
            "total_requests": self._total_requests,
            "total_symbols": self._total_symbols,
            "errors": self._errors,
        }


if __name__ == "__main__":
    import os

    async def test():
        api_key = os.environ.get("POLYGON_API_KEY")
        if not api_key:
            print("POLYGON_API_KEY required")
            return

        fetcher = PolygonBarsFetcher(api_key)

        print("Testing single symbol...")
        aapl = await fetcher.get_bars("AAPL", days=10)
        print(f"AAPL: {len(aapl.bars)} bars")
        if aapl.bars:
            print(f"  Latest: {aapl.bars[-1].timestamp.date()} close=${aapl.bars[-1].close:.2f}")

        print("\nTesting batch...")
        batch = await fetcher.get_bars_batch(["AAPL", "TSLA", "NVDA"], days=10)
        for sym, data in batch.items():
            print(f"{sym}: {len(data.bars)} bars, latest=${data.latest_price:.2f if data.latest_price else 0}")

        print(f"\nMetrics: {fetcher.get_metrics()}")
        await fetcher.close()

    asyncio.run(test())
