"""
Stock Price WebSocket Monitor (PROD-1)

Real-time stock price monitoring via Alpaca SIP WebSocket for:
- Pre-entry price validation
- Hard stop monitoring
- Intraday signal triggers

Features:
- Dynamic subscription management (subscribe/unsubscribe as positions change)
- Automatic reconnection with exponential backoff
- Price callbacks for position monitoring
- Aggregated quotes (bid/ask) and trades
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)

ALPACA_SIP_WS_URL = "wss://stream.data.alpaca.markets/v2/sip"
RECONNECT_DELAYS = [1, 2, 5, 10, 30, 60]


@dataclass
class StockQuote:
    """Real-time stock quote."""
    symbol: str
    bid: float
    bid_size: int
    ask: float
    ask_size: int
    timestamp: int  # Unix timestamp (ms)

    @property
    def mid(self) -> float:
        """Mid price."""
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        """Bid-ask spread."""
        return self.ask - self.bid

    @property
    def timestamp_dt(self) -> datetime:
        """Convert timestamp to datetime."""
        return datetime.fromtimestamp(self.timestamp / 1000)


@dataclass
class StockTrade:
    """Real-time stock trade."""
    symbol: str
    price: float
    size: int
    timestamp: int  # Unix timestamp (ms)
    conditions: List[int] = field(default_factory=list)
    exchange: int = 0

    @property
    def timestamp_dt(self) -> datetime:
        """Convert timestamp to datetime."""
        return datetime.fromtimestamp(self.timestamp / 1000)


@dataclass
class StockPrice:
    """Current stock price state (aggregated)."""
    symbol: str
    last_trade: Optional[float] = None
    last_trade_time: Optional[datetime] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    last_update: Optional[datetime] = None

    @property
    def price(self) -> Optional[float]:
        """Best available price (trade > mid > bid)."""
        if self.last_trade:
            return self.last_trade
        if self.mid:
            return self.mid
        if self.bid:
            return self.bid
        return None


@dataclass
class MonitorMetrics:
    """Metrics for stock price monitor."""
    start_time: float = field(default_factory=time.time)
    trades_received: int = 0
    quotes_received: int = 0
    reconnect_count: int = 0
    last_message_time: float = 0
    symbols_subscribed: int = 0

    def messages_per_second(self) -> float:
        """Calculate average messages per second."""
        elapsed = time.time() - self.start_time
        total = self.trades_received + self.quotes_received
        return total / elapsed if elapsed > 0 else 0


# Type aliases for callbacks
PriceCallback = Callable[[str, float, datetime], None]
TradeCallback = Callable[[StockTrade], None]
QuoteCallback = Callable[[StockQuote], None]


class StockPriceMonitor:
    """
    Real-time stock price monitor using Alpaca SIP WebSocket.

    Supports dynamic subscription management for monitoring
    active positions and signal candidates.

    Usage:
        monitor = StockPriceMonitor(api_key, secret_key)
        monitor.on_price_update = lambda sym, price, ts: print(f"{sym}: ${price}")

        await monitor.start()
        await monitor.subscribe(["AAPL", "TSLA"])

        # Later...
        await monitor.unsubscribe(["AAPL"])
        current_price = monitor.get_price("TSLA")

        await monitor.stop()
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        subscribe_trades: bool = True,
        subscribe_quotes: bool = True,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.subscribe_trades = subscribe_trades
        self.subscribe_quotes = subscribe_quotes

        # Connection state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._connected = False
        self._authenticated = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

        # Subscription state
        self._subscribed_symbols: Set[str] = set()
        self._pending_subscribes: Set[str] = set()
        self._pending_unsubscribes: Set[str] = set()

        # Price state
        self._prices: Dict[str, StockPrice] = {}

        # Callbacks
        self.on_price_update: Optional[PriceCallback] = None
        self.on_trade: Optional[TradeCallback] = None
        self.on_quote: Optional[QuoteCallback] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # Metrics
        self.metrics = MonitorMetrics()

        # Lock for subscription changes
        self._sub_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if connected and authenticated."""
        return self._connected and self._authenticated

    @property
    def subscribed_symbols(self) -> Set[str]:
        """Get currently subscribed symbols."""
        return self._subscribed_symbols.copy()

    def get_price(self, symbol: str) -> Optional[StockPrice]:
        """Get current price state for a symbol."""
        return self._prices.get(symbol)

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get last known price for a symbol."""
        price_state = self._prices.get(symbol)
        return price_state.price if price_state else None

    async def start(self) -> bool:
        """
        Start the monitor and connect to WebSocket.

        Always starts the receive loop (which handles reconnection).
        Returns True if the initial connection succeeded, False if it will
        keep retrying in the background.
        """
        if self._running:
            logger.warning("Monitor already running")
            return self.is_connected

        self._running = True
        success = await self._connect()

        # Always start receive loop — it handles reconnection on transient failures
        self._receive_task = asyncio.create_task(self._receive_loop())

        if success:
            logger.info("Stock price monitor started")
        else:
            logger.warning("Initial connection failed — receive loop will retry")

        return success

    async def stop(self):
        """Stop the monitor and disconnect."""
        self._running = False

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self._ws.close()
            self._ws = None

        self._connected = False
        self._authenticated = False
        logger.info("Stock price monitor stopped")

    async def subscribe(self, symbols: List[str]) -> bool:
        """
        Subscribe to price updates for symbols.

        Args:
            symbols: List of stock symbols to subscribe to

        Returns:
            True if subscription request was sent
        """
        if not symbols:
            return True

        async with self._sub_lock:
            new_symbols = set(s.upper() for s in symbols) - self._subscribed_symbols

            if not new_symbols:
                return True

            if not self.is_connected:
                # Queue for when we connect
                self._pending_subscribes.update(new_symbols)
                logger.info(f"Queued subscription for {len(new_symbols)} symbols (not connected)")
                return True

            # Build Alpaca subscription message
            msg = {"action": "subscribe"}
            sym_list = sorted(new_symbols)
            if self.subscribe_trades:
                msg["trades"] = sym_list
            if self.subscribe_quotes:
                msg["quotes"] = sym_list

            try:
                await self._ws.send(json.dumps(msg))
                self._subscribed_symbols.update(new_symbols)
                self.metrics.symbols_subscribed = len(self._subscribed_symbols)

                # Initialize price state
                for sym in new_symbols:
                    if sym not in self._prices:
                        self._prices[sym] = StockPrice(symbol=sym)

                logger.info(f"Subscribed to {len(new_symbols)} symbols: {new_symbols}")
                return True
            except Exception as e:
                logger.error(f"Failed to subscribe: {e}")
                return False

        return True

    async def unsubscribe(self, symbols: List[str]) -> bool:
        """
        Unsubscribe from price updates for symbols.

        Args:
            symbols: List of stock symbols to unsubscribe from

        Returns:
            True if unsubscription request was sent
        """
        if not symbols:
            return True

        async with self._sub_lock:
            to_remove = set(s.upper() for s in symbols) & self._subscribed_symbols

            if not to_remove:
                return True

            if not self.is_connected:
                self._pending_unsubscribes.update(to_remove)
                self._subscribed_symbols -= to_remove
                return True

            # Build Alpaca unsubscription message
            msg = {"action": "unsubscribe"}
            sym_list = sorted(to_remove)
            if self.subscribe_trades:
                msg["trades"] = sym_list
            if self.subscribe_quotes:
                msg["quotes"] = sym_list

            try:
                await self._ws.send(json.dumps(msg))
                self._subscribed_symbols -= to_remove
                self.metrics.symbols_subscribed = len(self._subscribed_symbols)

                # Optionally clear price state
                for sym in to_remove:
                    self._prices.pop(sym, None)

                logger.info(f"Unsubscribed from {len(to_remove)} symbols")
                return True
            except Exception as e:
                logger.error(f"Failed to unsubscribe: {e}")
                return False

        return True

    async def set_symbols(self, symbols: List[str]) -> bool:
        """
        Set the exact list of symbols to subscribe to.
        Subscribes to new symbols and unsubscribes from removed ones.

        Args:
            symbols: Complete list of symbols to monitor

        Returns:
            True if successful
        """
        target = set(s.upper() for s in symbols)
        current = self._subscribed_symbols

        to_add = target - current
        to_remove = current - target

        success = True
        if to_remove:
            success = await self.unsubscribe(list(to_remove)) and success
        if to_add:
            success = await self.subscribe(list(to_add)) and success

        return success

    async def _connect(self) -> bool:
        """Connect and authenticate to Alpaca SIP WebSocket."""
        try:
            self._ws = await websockets.connect(
                ALPACA_SIP_WS_URL,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )

            # Receive connected message
            # Alpaca sends: [{"T":"success","msg":"connected"}]
            response = await self._ws.recv()
            data = json.loads(response)

            if isinstance(data, list) and len(data) > 0:
                first = data[0]
                if first.get("T") == "success" and first.get("msg") == "connected":
                    logger.info("Connected to Alpaca SIP WebSocket")
                    self._connected = True
                else:
                    logger.warning(f"Unexpected connection response: {response[:200]}")
                    return False
            else:
                logger.warning(f"Unexpected connection response: {response[:200]}")
                return False

            # Authenticate
            # Alpaca expects: {"action":"auth","key":"...","secret":"..."}
            auth_msg = {"action": "auth", "key": self.api_key, "secret": self.secret_key}
            await self._ws.send(json.dumps(auth_msg))

            # Wait for auth response
            # Alpaca sends: [{"T":"success","msg":"authenticated"}]
            response = await self._ws.recv()
            data = json.loads(response)

            auth_success = False
            if isinstance(data, list):
                for msg in data:
                    if msg.get("T") == "success" and msg.get("msg") == "authenticated":
                        auth_success = True
                        break

            if auth_success:
                logger.info("Authentication successful")
                self._authenticated = True

                # Process any pending subscriptions
                if self._pending_subscribes:
                    await self.subscribe(list(self._pending_subscribes))
                    self._pending_subscribes.clear()

                # Re-subscribe to existing symbols after reconnect
                if self._subscribed_symbols:
                    symbols = list(self._subscribed_symbols)
                    self._subscribed_symbols.clear()
                    await self.subscribe(symbols)

                if self.on_connect:
                    self.on_connect()

                return True
            else:
                logger.error(f"Authentication failed: {response[:200]}")
                return False

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def _receive_loop(self):
        """Main receive loop for WebSocket messages."""
        reconnect_attempt = 0

        while self._running:
            try:
                if not self._ws or not self.is_connected:
                    # Try to reconnect
                    delay = RECONNECT_DELAYS[min(reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
                    logger.info(f"Reconnecting in {delay}s (attempt {reconnect_attempt + 1})")
                    await asyncio.sleep(delay)

                    if await self._connect():
                        reconnect_attempt = 0
                        self.metrics.reconnect_count += 1
                    else:
                        reconnect_attempt += 1
                        continue

                # Receive and process messages
                async for message in self._ws:
                    self.metrics.last_message_time = time.time()
                    await self._process_message(message)

            except ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self._connected = False
                self._authenticated = False
                if self.on_disconnect:
                    self.on_disconnect()
                reconnect_attempt += 1

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"Error in receive loop: {e}")
                self._connected = False
                self._authenticated = False
                reconnect_attempt += 1

    async def _process_message(self, message: str):
        """Process a WebSocket message."""
        try:
            data = json.loads(message)

            if not isinstance(data, list):
                return

            for item in data:
                msg_type = item.get("T")

                if msg_type == "t":  # Trade
                    trade = self._parse_trade(item)
                    if trade:
                        self._update_price_from_trade(trade)
                        self.metrics.trades_received += 1

                        if self.on_trade:
                            self.on_trade(trade)

                elif msg_type == "q":  # Quote
                    quote = self._parse_quote(item)
                    if quote:
                        self._update_price_from_quote(quote)
                        self.metrics.quotes_received += 1

                        if self.on_quote:
                            self.on_quote(quote)

                elif msg_type == "success":
                    logger.debug(f"Success message: {item.get('msg')}")

                elif msg_type == "subscription":
                    trades = item.get("trades", [])
                    quotes = item.get("quotes", [])
                    logger.info(f"Subscription confirmed — trades: {len(trades)}, quotes: {len(quotes)}")

                elif msg_type == "error":
                    logger.error(f"Alpaca WS error: code={item.get('code')} msg={item.get('msg')}")

        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON: {message[:100]}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _parse_timestamp(self, ts_str: str) -> int:
        """Parse Alpaca RFC-3339 timestamp to unix ms."""
        # Alpaca sends e.g. "2026-02-19T14:30:00.123456Z"
        # Python 3.11+ handles fromisoformat with Z
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    def _parse_trade(self, data: dict) -> Optional[StockTrade]:
        """Parse an Alpaca trade message."""
        try:
            return StockTrade(
                symbol=data.get("S", ""),
                price=float(data.get("p", 0)),
                size=int(data.get("s", 0)),
                timestamp=self._parse_timestamp(data.get("t", "")),
                conditions=data.get("c", []),
                exchange=data.get("x", 0),
            )
        except Exception as e:
            logger.warning(f"Failed to parse trade: {e}")
            return None

    def _parse_quote(self, data: dict) -> Optional[StockQuote]:
        """Parse an Alpaca quote message."""
        try:
            return StockQuote(
                symbol=data.get("S", ""),
                bid=float(data.get("bp", 0)),
                bid_size=int(data.get("bs", 0)),
                ask=float(data.get("ap", 0)),
                ask_size=int(data.get("as", 0)),
                timestamp=self._parse_timestamp(data.get("t", "")),
            )
        except Exception as e:
            logger.warning(f"Failed to parse quote: {e}")
            return None

    def _update_price_from_trade(self, trade: StockTrade):
        """Update price state from a trade."""
        if trade.symbol not in self._prices:
            self._prices[trade.symbol] = StockPrice(symbol=trade.symbol)

        price_state = self._prices[trade.symbol]
        price_state.last_trade = trade.price
        price_state.last_trade_time = trade.timestamp_dt
        price_state.last_update = datetime.now()

        # Fire callback
        if self.on_price_update:
            self.on_price_update(trade.symbol, trade.price, trade.timestamp_dt)

    def _update_price_from_quote(self, quote: StockQuote):
        """Update price state from a quote."""
        if quote.symbol not in self._prices:
            self._prices[quote.symbol] = StockPrice(symbol=quote.symbol)

        price_state = self._prices[quote.symbol]
        price_state.bid = quote.bid
        price_state.ask = quote.ask
        price_state.mid = quote.mid
        price_state.last_update = datetime.now()

        # Fire callback with mid price if no recent trade
        if self.on_price_update and not price_state.last_trade:
            self.on_price_update(quote.symbol, quote.mid, quote.timestamp_dt)

    def get_metrics(self) -> dict:
        """Get current metrics."""
        return {
            "uptime_seconds": time.time() - self.metrics.start_time,
            "trades_received": self.metrics.trades_received,
            "quotes_received": self.metrics.quotes_received,
            "messages_per_second": self.metrics.messages_per_second(),
            "reconnect_count": self.metrics.reconnect_count,
            "symbols_subscribed": self.metrics.symbols_subscribed,
            "connected": self.is_connected,
        }
