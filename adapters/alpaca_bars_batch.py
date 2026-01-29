"""
Alpaca Batched Bars Fetcher (Component 4.2)

Fetches price bars from Alpaca in batches to stay within rate limits.
Free tier: 200 requests/minute
Strategy: Batch 50-100 symbols per request

Usage:
    fetcher = AlpacaBarsFetcher(api_key, secret_key)
    bars = await fetcher.get_bars_batch(["AAPL", "TSLA", "NVDA"], timeframe="5Min", limit=50)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# Alpaca API endpoints
ALPACA_DATA_URL = "https://data.alpaca.markets/v2"

# Rate limiting
MAX_REQUESTS_PER_MINUTE = 200
REQUEST_INTERVAL = 60 / MAX_REQUESTS_PER_MINUTE  # ~0.3 seconds
MAX_SYMBOLS_PER_REQUEST = 100


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
    bars: list[Bar]
    latest_price: Optional[float] = None
    latest_volume: Optional[int] = None

    @property
    def has_data(self) -> bool:
        return len(self.bars) > 0


class AlpacaBarsFetcher:
    """
    Fetches price bars from Alpaca Market Data API.

    Supports batched requests to maximize throughput while
    staying within rate limits (200 req/min for free tier).

    Usage:
        fetcher = AlpacaBarsFetcher(api_key, secret_key)

        # Get bars for multiple symbols
        bars = await fetcher.get_bars_batch(
            symbols=["AAPL", "TSLA", "NVDA"],
            timeframe="5Min",
            limit=50
        )

        # Get bars for a single symbol
        bar_data = await fetcher.get_bars("AAPL", timeframe="1Day", limit=20)
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        max_symbols_per_request: int = MAX_SYMBOLS_PER_REQUEST,
    ):
        """
        Initialize fetcher.

        Args:
            api_key: Alpaca API key
            secret_key: Alpaca secret key
            max_symbols_per_request: Max symbols per batch request
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.max_symbols_per_request = max_symbols_per_request

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time = 0

        # Metrics
        self._total_requests = 0
        self._total_symbols = 0
        self._errors = 0

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

    async def _rate_limit(self) -> None:
        """Apply rate limiting."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < REQUEST_INTERVAL:
            await asyncio.sleep(REQUEST_INTERVAL - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "5Min",
        limit: int = 50,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> BarData:
        """
        Get bars for a single symbol.

        Args:
            symbol: Stock symbol
            timeframe: Bar timeframe (1Min, 5Min, 15Min, 1Hour, 1Day)
            limit: Number of bars to fetch
            start: Start datetime (optional)
            end: End datetime (optional)

        Returns:
            BarData with list of bars
        """
        result = await self.get_bars_batch([symbol], timeframe, limit, start, end)
        return result.get(symbol, BarData(symbol=symbol, bars=[]))

    async def get_bars_batch(
        self,
        symbols: list[str],
        timeframe: str = "5Min",
        limit: int = 50,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> dict[str, BarData]:
        """
        Get bars for multiple symbols in a single request.

        Alpaca supports up to ~100 symbols per request.

        Args:
            symbols: List of stock symbols
            timeframe: Bar timeframe (1Min, 5Min, 15Min, 1Hour, 1Day)
            limit: Number of bars per symbol
            start: Start datetime (optional)
            end: End datetime (optional)

        Returns:
            Dict mapping symbol to BarData
        """
        if not symbols:
            return {}

        # Split into batches if needed
        if len(symbols) > self.max_symbols_per_request:
            result = {}
            for i in range(0, len(symbols), self.max_symbols_per_request):
                batch = symbols[i:i + self.max_symbols_per_request]
                batch_result = await self._fetch_bars_batch(
                    batch, timeframe, limit, start, end
                )
                result.update(batch_result)
            return result

        return await self._fetch_bars_batch(symbols, timeframe, limit, start, end)

    async def _fetch_bars_batch(
        self,
        symbols: list[str],
        timeframe: str,
        limit: int,
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> dict[str, BarData]:
        """Internal batch fetch implementation."""
        await self._rate_limit()

        session = await self._get_session()
        self._total_requests += 1
        self._total_symbols += len(symbols)

        # Build request params
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "limit": limit,
            "adjustment": "raw",
            "feed": "iex",  # Use IEX for free tier
        }

        if start:
            params["start"] = start.isoformat() + "Z"
        if end:
            params["end"] = end.isoformat() + "Z"

        url = f"{ALPACA_DATA_URL}/stocks/bars"

        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_bars_response(data, symbols)

                elif response.status == 429:
                    # Rate limited - wait and retry
                    logger.warning("Rate limited by Alpaca, waiting 60s...")
                    await asyncio.sleep(60)
                    return await self._fetch_bars_batch(
                        symbols, timeframe, limit, start, end
                    )

                else:
                    text = await response.text()
                    logger.error(f"Alpaca API error {response.status}: {text}")
                    self._errors += 1
                    return {s: BarData(symbol=s, bars=[]) for s in symbols}

        except Exception as e:
            logger.error(f"Alpaca request failed: {e}")
            self._errors += 1
            return {s: BarData(symbol=s, bars=[]) for s in symbols}

    def _parse_bars_response(
        self,
        data: dict,
        symbols: list[str],
    ) -> dict[str, BarData]:
        """Parse Alpaca bars response."""
        result = {}

        bars_data = data.get("bars", {})

        for symbol in symbols:
            symbol_bars = bars_data.get(symbol, [])
            bars = []

            for bar in symbol_bars:
                bars.append(Bar(
                    symbol=symbol,
                    timestamp=datetime.fromisoformat(bar["t"].replace("Z", "+00:00")),
                    open=float(bar["o"]),
                    high=float(bar["h"]),
                    low=float(bar["l"]),
                    close=float(bar["c"]),
                    volume=int(bar["v"]),
                    vwap=float(bar["vw"]) if "vw" in bar else None,
                    trade_count=int(bar["n"]) if "n" in bar else None,
                ))

            bar_data = BarData(symbol=symbol, bars=bars)
            if bars:
                bar_data.latest_price = bars[-1].close
                bar_data.latest_volume = bars[-1].volume

            result[symbol] = bar_data

        return result

    async def get_latest_bars(
        self,
        symbols: list[str],
    ) -> dict[str, Optional[Bar]]:
        """
        Get the most recent bar for each symbol.

        Args:
            symbols: List of stock symbols

        Returns:
            Dict mapping symbol to latest Bar (or None)
        """
        # Fetch just 1 bar per symbol
        bars_data = await self.get_bars_batch(symbols, timeframe="1Min", limit=1)

        result = {}
        for symbol in symbols:
            bar_data = bars_data.get(symbol)
            if bar_data and bar_data.bars:
                result[symbol] = bar_data.bars[-1]
            else:
                result[symbol] = None

        return result

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
            "avg_symbols_per_request": (
                self._total_symbols / self._total_requests
                if self._total_requests > 0 else 0
            ),
        }


if __name__ == "__main__":
    import os

    print("Alpaca Bars Fetcher Tests")
    print("=" * 60)

    async def test_fetcher():
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            print("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
            print("Skipping live test, showing mock behavior...")

            # Mock test without API
            fetcher = AlpacaBarsFetcher("mock_key", "mock_secret")

            # Test batching logic
            symbols = [f"SYM{i}" for i in range(150)]
            batches = []
            for i in range(0, len(symbols), fetcher.max_symbols_per_request):
                batches.append(symbols[i:i + fetcher.max_symbols_per_request])

            print(f"\n150 symbols would create {len(batches)} batches:")
            for i, batch in enumerate(batches):
                print(f"  Batch {i+1}: {len(batch)} symbols")

            return

        print(f"\nAPI Key: {api_key[:8]}...")

        fetcher = AlpacaBarsFetcher(api_key, secret_key)

        try:
            # Test single symbol
            print("\nFetching AAPL 5-min bars...")
            aapl = await fetcher.get_bars("AAPL", timeframe="5Min", limit=10)
            print(f"  Got {len(aapl.bars)} bars")
            if aapl.bars:
                latest = aapl.bars[-1]
                print(f"  Latest: {latest.timestamp} close=${latest.close:.2f}")

            # Test batch
            print("\nFetching batch [AAPL, TSLA, NVDA]...")
            batch = await fetcher.get_bars_batch(
                ["AAPL", "TSLA", "NVDA"],
                timeframe="5Min",
                limit=5
            )
            for symbol, data in batch.items():
                print(f"  {symbol}: {len(data.bars)} bars, latest=${data.latest_price:.2f if data.latest_price else 0}")

            # Test latest
            print("\nFetching latest bars...")
            latest = await fetcher.get_latest_bars(["AAPL", "MSFT"])
            for symbol, bar in latest.items():
                if bar:
                    print(f"  {symbol}: ${bar.close:.2f} @ {bar.timestamp}")
                else:
                    print(f"  {symbol}: No data")

            print(f"\nMetrics: {fetcher.get_metrics()}")

        finally:
            await fetcher.close()

    asyncio.run(test_fetcher())
