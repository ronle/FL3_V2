"""
Polygon Snapshot Fetcher (Component 2.5)

Fetches option chain snapshots from Polygon REST API on UOA trigger.
Used to get OI, IV, and Greeks for GEX calculation.

Rate limiting: Respects Polygon limits (50,000 REST calls/day).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

POLYGON_BASE_URL = "https://api.polygon.io"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 2, 5]  # Seconds between retries


@dataclass
class OptionContract:
    """Option contract from Polygon snapshot."""
    symbol: str           # OCC symbol (e.g., O:AAPL250117C00150000)
    underlying: str       # Underlying ticker
    strike: float
    expiry: date
    is_call: bool
    open_interest: int
    implied_volatility: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    last_price: Optional[float]
    volume: int


@dataclass
class SnapshotResult:
    """Result of option chain snapshot fetch."""
    underlying: str
    spot_price: Optional[float]
    contracts: list[OptionContract]
    fetch_time: datetime
    success: bool
    error: Optional[str] = None


class PolygonSnapshotFetcher:
    """
    Async fetcher for Polygon option chain snapshots.

    Usage:
        fetcher = PolygonSnapshotFetcher(api_key)
        snapshot = await fetcher.get_option_chain("AAPL")
    """

    def __init__(
        self,
        api_key: str,
        max_concurrent: int = 5,
        cache_ttl_seconds: int = 60
    ):
        """
        Initialize fetcher.

        Args:
            api_key: Polygon API key
            max_concurrent: Max concurrent requests
            cache_ttl_seconds: How long to cache snapshots
        """
        self.api_key = api_key
        self.max_concurrent = max_concurrent
        self.cache_ttl = cache_ttl_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cache: dict[str, tuple[SnapshotResult, float]] = {}
        self._request_count = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _check_cache(self, underlying: str) -> Optional[SnapshotResult]:
        """Check if we have a valid cached snapshot."""
        if underlying in self._cache:
            result, timestamp = self._cache[underlying]
            if time.time() - timestamp < self.cache_ttl:
                return result
            else:
                del self._cache[underlying]
        return None

    def _update_cache(self, underlying: str, result: SnapshotResult):
        """Update the cache with a new result."""
        self._cache[underlying] = (result, time.time())

    async def get_option_chain(
        self,
        underlying: str,
        use_cache: bool = True
    ) -> SnapshotResult:
        """
        Fetch option chain snapshot for underlying.

        Args:
            underlying: Ticker symbol (e.g., "AAPL")
            use_cache: Whether to use cached data if available

        Returns:
            SnapshotResult with all contracts
        """
        # Check cache first
        if use_cache:
            cached = self._check_cache(underlying)
            if cached:
                logger.debug(f"Cache hit for {underlying}")
                return cached

        # Fetch from API
        async with self._semaphore:
            result = await self._fetch_snapshot(underlying)

        # Update cache on success
        if result.success:
            self._update_cache(underlying, result)

        return result

    async def _fetch_snapshot(self, underlying: str) -> SnapshotResult:
        """Internal method to fetch snapshot with retries."""
        session = await self._get_session()

        # Polygon option chain endpoint
        url = f"{POLYGON_BASE_URL}/v3/snapshot/options/{underlying}"
        params = {"apiKey": self.api_key}

        for attempt, backoff in enumerate(RETRY_BACKOFF):
            try:
                self._request_count += 1
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_snapshot(underlying, data)

                    elif response.status == 429:
                        # Rate limited
                        logger.warning(f"Rate limited for {underlying}, waiting {backoff}s")
                        await asyncio.sleep(backoff)
                        continue

                    elif response.status == 404:
                        # No options for this symbol
                        return SnapshotResult(
                            underlying=underlying,
                            spot_price=None,
                            contracts=[],
                            fetch_time=datetime.now(),
                            success=True,
                            error=f"No options available for {underlying}"
                        )

                    else:
                        error = f"HTTP {response.status}: {await response.text()}"
                        logger.error(f"Snapshot error for {underlying}: {error}")
                        if attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(backoff)
                            continue
                        return SnapshotResult(
                            underlying=underlying,
                            spot_price=None,
                            contracts=[],
                            fetch_time=datetime.now(),
                            success=False,
                            error=error
                        )

            except asyncio.TimeoutError:
                logger.warning(f"Timeout fetching {underlying}, attempt {attempt + 1}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    continue

            except Exception as e:
                logger.error(f"Error fetching {underlying}: {e}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(backoff)
                    continue
                return SnapshotResult(
                    underlying=underlying,
                    spot_price=None,
                    contracts=[],
                    fetch_time=datetime.now(),
                    success=False,
                    error=str(e)
                )

        return SnapshotResult(
            underlying=underlying,
            spot_price=None,
            contracts=[],
            fetch_time=datetime.now(),
            success=False,
            error="Max retries exceeded"
        )

    def _parse_snapshot(self, underlying: str, data: dict) -> SnapshotResult:
        """Parse Polygon snapshot response."""
        contracts = []
        spot_price = None

        results = data.get("results", [])

        for item in results:
            try:
                # Extract contract details
                details = item.get("details", {})
                greeks = item.get("greeks", {})
                day = item.get("day", {})
                underlying_asset = item.get("underlying_asset", {})

                # Get spot price from first contract
                if spot_price is None and underlying_asset:
                    spot_price = underlying_asset.get("price")

                # Parse expiry date
                exp_str = details.get("expiration_date", "")
                if exp_str:
                    expiry = datetime.strptime(exp_str, "%Y-%m-%d").date()
                else:
                    continue

                contract = OptionContract(
                    symbol=item.get("ticker", ""),
                    underlying=underlying,
                    strike=float(details.get("strike_price", 0)),
                    expiry=expiry,
                    is_call=details.get("contract_type", "").lower() == "call",
                    open_interest=int(item.get("open_interest", 0)),
                    implied_volatility=greeks.get("implied_volatility"),
                    delta=greeks.get("delta"),
                    gamma=greeks.get("gamma"),
                    theta=greeks.get("theta"),
                    vega=greeks.get("vega"),
                    bid=day.get("close"),  # Polygon uses 'close' for bid in day agg
                    ask=day.get("high"),   # Approximate
                    last_price=day.get("close"),
                    volume=int(day.get("volume", 0))
                )
                contracts.append(contract)

            except Exception as e:
                logger.warning(f"Error parsing contract: {e}")
                continue

        return SnapshotResult(
            underlying=underlying,
            spot_price=spot_price,
            contracts=contracts,
            fetch_time=datetime.now(),
            success=True
        )

    async def get_multiple(
        self,
        underlyings: list[str],
        use_cache: bool = True
    ) -> dict[str, SnapshotResult]:
        """
        Fetch snapshots for multiple underlyings concurrently.

        Args:
            underlyings: List of ticker symbols
            use_cache: Whether to use cached data

        Returns:
            Dict mapping underlying to SnapshotResult
        """
        tasks = [
            self.get_option_chain(u, use_cache)
            for u in underlyings
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = {}
        for underlying, result in zip(underlyings, results):
            if isinstance(result, Exception):
                output[underlying] = SnapshotResult(
                    underlying=underlying,
                    spot_price=None,
                    contracts=[],
                    fetch_time=datetime.now(),
                    success=False,
                    error=str(result)
                )
            else:
                output[underlying] = result

        return output

    def get_stats(self) -> dict:
        """Get fetcher statistics."""
        return {
            'request_count': self._request_count,
            'cache_size': len(self._cache),
            'cache_ttl': self.cache_ttl
        }

    def clear_cache(self):
        """Clear the snapshot cache."""
        self._cache.clear()


# Convenience function for one-off fetches
async def fetch_snapshot(
    api_key: str,
    underlying: str
) -> SnapshotResult:
    """
    One-off snapshot fetch.

    Usage:
        snapshot = await fetch_snapshot("your_api_key", "AAPL")
    """
    fetcher = PolygonSnapshotFetcher(api_key)
    try:
        return await fetcher.get_option_chain(underlying)
    finally:
        await fetcher.close()


if __name__ == "__main__":
    import os

    async def test_fetcher():
        api_key = os.environ.get("POLYGON_API_KEY")
        if not api_key:
            print("POLYGON_API_KEY not set, running mock test")
            # Mock test without API
            fetcher = PolygonSnapshotFetcher("test_key")
            print(f"Fetcher initialized: {fetcher.get_stats()}")
            return

        print("Polygon Snapshot Fetcher Test")
        print("=" * 60)

        fetcher = PolygonSnapshotFetcher(api_key)

        try:
            # Test single fetch
            print("\nFetching AAPL option chain...")
            result = await fetcher.get_option_chain("AAPL")

            if result.success:
                print(f"  Success! Got {len(result.contracts)} contracts")
                print(f"  Spot price: ${result.spot_price}")
                if result.contracts:
                    # Show sample contract
                    c = result.contracts[0]
                    print(f"  Sample: {c.symbol}")
                    print(f"    Strike: ${c.strike}, OI: {c.open_interest}")
                    print(f"    IV: {c.implied_volatility:.2%}" if c.implied_volatility else "    IV: N/A")
            else:
                print(f"  Failed: {result.error}")

            # Test cache
            print("\nTesting cache (second fetch should be instant)...")
            import time
            start = time.time()
            result2 = await fetcher.get_option_chain("AAPL")
            elapsed = time.time() - start
            print(f"  Second fetch took {elapsed*1000:.1f}ms")

            print(f"\nStats: {fetcher.get_stats()}")

        finally:
            await fetcher.close()

    asyncio.run(test_fetcher())
