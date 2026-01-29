"""
Firehose Websocket Client (Component 3.1)

Connects to Polygon websocket firehose (T.*) and receives all options trades.
Features:
- Automatic reconnection with exponential backoff
- Heartbeat/ping-pong handling
- Metrics collection (messages/sec, reconnects, lag)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

POLYGON_WS_URL = "wss://socket.polygon.io/options"
RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]  # Exponential backoff


@dataclass
class Trade:
    """Parsed options trade from firehose."""
    symbol: str          # OCC symbol (e.g., O:AAPL250117C00150000)
    price: float         # Trade price
    size: int            # Number of contracts
    timestamp: int       # Unix timestamp (ms)
    conditions: list     # Trade conditions
    exchange: int        # Exchange ID

    @property
    def notional(self) -> float:
        """Calculate notional value (price * size * 100)."""
        return self.price * self.size * 100

    @property
    def timestamp_dt(self) -> datetime:
        """Convert timestamp to datetime."""
        return datetime.fromtimestamp(self.timestamp / 1000)


@dataclass
class FirehoseMetrics:
    """Metrics for firehose connection."""
    start_time: float = field(default_factory=time.time)
    messages_received: int = 0
    trades_received: int = 0
    parse_errors: int = 0
    reconnect_count: int = 0
    last_message_time: float = 0
    max_lag_ms: float = 0
    symbols_seen: set = field(default_factory=set)

    def messages_per_second(self) -> float:
        """Calculate average messages per second."""
        elapsed = time.time() - self.start_time
        return self.messages_received / elapsed if elapsed > 0 else 0

    def trades_per_second(self) -> float:
        """Calculate average trades per second."""
        elapsed = time.time() - self.start_time
        return self.trades_received / elapsed if elapsed > 0 else 0


class FirehoseClient:
    """
    Polygon options firehose websocket client.

    Usage:
        client = FirehoseClient(api_key)
        async for trade in client.stream():
            process_trade(trade)
    """

    def __init__(
        self,
        api_key: str,
        on_trade: Optional[Callable[[Trade], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize firehose client.

        Args:
            api_key: Polygon API key
            on_trade: Optional callback for each trade
            on_status: Optional callback for status messages
        """
        self.api_key = api_key
        self.on_trade = on_trade
        self.on_status = on_status
        self.metrics = FirehoseMetrics()
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    async def connect(self) -> bool:
        """
        Connect to Polygon websocket.

        Returns:
            True if connected successfully
        """
        try:
            self._ws = await websockets.connect(
                POLYGON_WS_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )

            # Authenticate
            auth_msg = {"action": "auth", "params": self.api_key}
            await self._ws.send(json.dumps(auth_msg))
            response = await self._ws.recv()
            data = json.loads(response)

            if isinstance(data, list) and data[0].get("status") == "connected":
                logger.info("Connected to Polygon websocket")
            else:
                logger.warning(f"Unexpected auth response: {response[:100]}")

            # Wait for auth success
            response = await self._ws.recv()
            data = json.loads(response)
            if isinstance(data, list) and data[0].get("status") == "auth_success":
                logger.info("Authentication successful")
            else:
                logger.error(f"Authentication failed: {response[:100]}")
                return False

            # Subscribe to all options trades
            sub_msg = {"action": "subscribe", "params": "T.*"}
            await self._ws.send(json.dumps(sub_msg))
            response = await self._ws.recv()
            logger.info(f"Subscribed to T.*: {response[:100]}")

            if self.on_status:
                self.on_status("connected")

            return True

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from websocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    def _parse_trade(self, data: dict) -> Optional[Trade]:
        """Parse a trade message from Polygon."""
        try:
            if data.get("ev") != "T":
                return None

            return Trade(
                symbol=data.get("sym", ""),
                price=float(data.get("p", 0)),
                size=int(data.get("s", 0)),
                timestamp=int(data.get("t", 0)),
                conditions=data.get("c", []),
                exchange=int(data.get("x", 0)),
            )
        except Exception as e:
            self.metrics.parse_errors += 1
            logger.debug(f"Parse error: {e}")
            return None

    async def stream(self) -> AsyncIterator[Trade]:
        """
        Stream trades from firehose.

        Yields:
            Trade objects as they arrive

        Handles reconnection automatically.
        """
        self._running = True
        reconnect_attempt = 0

        while self._running:
            try:
                if not self._ws or self._ws.closed:
                    connected = await self.connect()
                    if not connected:
                        delay = RECONNECT_DELAYS[min(reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
                        logger.warning(f"Reconnecting in {delay}s (attempt {reconnect_attempt + 1})")
                        await asyncio.sleep(delay)
                        reconnect_attempt += 1
                        self.metrics.reconnect_count += 1
                        continue
                    reconnect_attempt = 0

                # Receive messages
                async for message in self._ws:
                    self.metrics.messages_received += 1
                    self.metrics.last_message_time = time.time()

                    try:
                        data = json.loads(message)

                        # Handle batch messages
                        if isinstance(data, list):
                            for item in data:
                                trade = self._parse_trade(item)
                                if trade:
                                    self.metrics.trades_received += 1
                                    self.metrics.symbols_seen.add(trade.symbol)

                                    # Track lag
                                    if trade.timestamp > 0:
                                        lag_ms = (time.time() * 1000) - trade.timestamp
                                        self.metrics.max_lag_ms = max(self.metrics.max_lag_ms, lag_ms)

                                    # Callback
                                    if self.on_trade:
                                        self.on_trade(trade)

                                    yield trade

                    except json.JSONDecodeError:
                        self.metrics.parse_errors += 1

            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}")
                self.metrics.reconnect_count += 1
                if self.on_status:
                    self.on_status("disconnected")

            except Exception as e:
                logger.error(f"Stream error: {e}")
                self.metrics.reconnect_count += 1
                await asyncio.sleep(1)

    async def run_for_duration(
        self,
        duration_seconds: int,
        callback: Optional[Callable[[Trade], None]] = None
    ) -> FirehoseMetrics:
        """
        Run firehose for a specific duration.

        Args:
            duration_seconds: How long to run
            callback: Optional callback for each trade

        Returns:
            Metrics after running
        """
        self.metrics = FirehoseMetrics()
        end_time = time.time() + duration_seconds

        async for trade in self.stream():
            if callback:
                callback(trade)

            if time.time() >= end_time:
                break

        await self.disconnect()
        return self.metrics

    def get_metrics(self) -> dict:
        """Get current metrics as dict."""
        return {
            "uptime_seconds": time.time() - self.metrics.start_time,
            "messages_received": self.metrics.messages_received,
            "trades_received": self.metrics.trades_received,
            "messages_per_second": self.metrics.messages_per_second(),
            "trades_per_second": self.metrics.trades_per_second(),
            "parse_errors": self.metrics.parse_errors,
            "reconnect_count": self.metrics.reconnect_count,
            "unique_symbols": len(self.metrics.symbols_seen),
            "max_lag_ms": self.metrics.max_lag_ms,
        }


async def quick_test(api_key: str, duration: int = 30):
    """Quick connectivity test."""
    print(f"\nFirehose Client Test ({duration}s)")
    print("=" * 60)

    client = FirehoseClient(api_key)
    trade_count = 0

    def on_trade(trade: Trade):
        nonlocal trade_count
        trade_count += 1
        if trade_count <= 5:
            print(f"  Trade: {trade.symbol} {trade.size}@{trade.price}")
        elif trade_count == 6:
            print("  ...")

    metrics = await client.run_for_duration(duration, callback=on_trade)

    print(f"\nResults:")
    print(f"  Total trades: {metrics.trades_received:,}")
    print(f"  Trade rate: {metrics.trades_per_second():.1f}/sec")
    print(f"  Unique symbols: {len(metrics.symbols_seen):,}")
    print(f"  Max lag: {metrics.max_lag_ms:.0f}ms")
    print(f"  Reconnects: {metrics.reconnect_count}")

    return metrics


if __name__ == "__main__":
    import os

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        print("POLYGON_API_KEY not set")
        exit(1)

    asyncio.run(quick_test(api_key, duration=30))
