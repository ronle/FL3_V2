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
        feed: Optional[str] = None,
    ) -> BarData:
        """
        Get bars for a single symbol.

        Args:
            symbol: Stock symbol
            timeframe: Bar timeframe (1Min, 5Min, 15Min, 1Hour, 1Day)
            limit: Number of bars to fetch
            start: Start datetime (optional)
            end: End datetime (optional)
            feed: Data feed ("sip" for full coverage, "iex" default)

        Returns:
            BarData with list of bars
        """
        result = await self.get_bars_batch([symbol], timeframe, limit, start, end, feed)
        return result.get(symbol, BarData(symbol=symbol, bars=[]))

    async def get_bars_batch(
        self,
        symbols: list[str],
        timeframe: str = "5Min",
        limit: int = 50,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        feed: Optional[str] = None,
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
            feed: Data feed override (default "iex"; use "sip" for full coverage)

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
                    batch, timeframe, limit, start, end, feed
                )
                result.update(batch_result)
            return result

        return await self._fetch_bars_batch(symbols, timeframe, limit, start, end, feed)

    async def _fetch_bars_batch(
        self,
        symbols: list[str],
        timeframe: str,
        limit: int,
        start: Optional[datetime],
        end: Optional[datetime],
        feed: Optional[str] = None,
    ) -> dict[str, BarData]:
        """
        Internal batch fetch implementation with pagination.

        Alpaca's multi-symbol bars API paginates by total bar count across all symbols.
        We need to follow next_page_token until we have 'limit' bars per symbol.
        """
        session = await self._get_session()
        self._total_requests += 1
        self._total_symbols += len(symbols)

        # Build request params
        # Use limit=10000 per request to minimize API calls (max is 10000)
        # We'll accumulate until each symbol has 'limit' bars
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "limit": 10000,  # Max per request - we paginate to get all
            "adjustment": "raw",
            "feed": feed or "iex",
        }

        if start:
            # RFC3339 format: append Z only if naive (no timezone info)
            if start.tzinfo is None:
                params["start"] = start.isoformat() + "Z"
            else:
                params["start"] = start.isoformat()
        if end:
            if end.tzinfo is None:
                params["end"] = end.isoformat() + "Z"
            else:
                params["end"] = end.isoformat()

        url = f"{ALPACA_DATA_URL}/stocks/bars"
        feed_used = params.get("feed", "iex")

        logger.info(f"Alpaca bars request: {len(symbols)} symbols, feed={feed_used}, timeframe={timeframe}, limit_per_symbol={limit}")

        # Accumulate bars across pages
        all_bars: dict[str, list] = {s: [] for s in symbols}
        page_count = 0
        max_pages = 500  # Safety limit (500 pages * 10000 bars = 5M bars max)

        try:
            while page_count < max_pages:
                page_count += 1
                await self._rate_limit()

                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        bars_data = data.get("bars", {})

                        # Accumulate bars for each symbol
                        for sym, bars in bars_data.items():
                            if sym in all_bars:
                                all_bars[sym].extend(bars)

                        # Check if we have enough bars for all symbols
                        symbols_complete = sum(1 for s in symbols if len(all_bars[s]) >= limit)

                        # Check for next page
                        next_token = data.get("next_page_token")
                        if not next_token:
                            # No more pages
                            break

                        # If all symbols have enough bars, stop
                        if symbols_complete == len(symbols):
                            break

                        # Continue to next page
                        params["page_token"] = next_token

                    elif response.status == 429:
                        # Rate limited - wait and retry
                        logger.warning("Rate limited by Alpaca, waiting 60s...")
                        await asyncio.sleep(60)
                        continue
                    else:
                        text = await response.text()
                        logger.error(f"Alpaca API error {response.status}: {text}")
                        self._errors += 1
                        break

            # Log final stats
            symbols_with_data = sum(1 for s in symbols if len(all_bars[s]) > 0)
            total_bars = sum(len(bars) for bars in all_bars.values())
            logger.info(f"Alpaca fetch complete: {symbols_with_data}/{len(symbols)} symbols, {total_bars} total bars, {page_count} pages")

            # Trim to requested limit per symbol and convert to BarData
            return self._parse_accumulated_bars(all_bars, symbols, limit)

        except Exception as e:
            logger.error(f"Alpaca request failed: {e}")
            self._errors += 1
            return {s: BarData(symbol=s, bars=[]) for s in symbols}

    def _parse_accumulated_bars(
        self,
        all_bars: dict[str, list],
        symbols: list[str],
        limit: int,
    ) -> dict[str, BarData]:
        """Parse accumulated bars from pagination, trimming to limit."""
        result = {}

        for symbol in symbols:
            raw_bars = all_bars.get(symbol, [])
            # Trim to limit - take most recent bars (end of list)
            if len(raw_bars) > limit:
                raw_bars = raw_bars[-limit:]

            bars = []
            for bar in raw_bars:
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
