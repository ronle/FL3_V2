"""
Alpaca Paper Trading Client

Handles order submission, position tracking, and account management
for Alpaca paper trading.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum

import aiohttp

from .config import TradingConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    PENDING_NEW = "pending_new"


@dataclass
class Position:
    """Alpaca position."""
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    current_price: float
    side: str  # 'long' or 'short'


@dataclass
class Order:
    """Alpaca order."""
    id: str
    symbol: str
    qty: float
    side: OrderSide
    type: OrderType
    status: OrderStatus
    filled_qty: float
    filled_avg_price: Optional[float]
    submitted_at: datetime
    filled_at: Optional[datetime]


@dataclass
class Account:
    """Alpaca account info."""
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    pattern_day_trader: bool
    trading_blocked: bool
    account_blocked: bool


class AlpacaTrader:
    """
    Alpaca paper trading client.

    Handles order submission, position management, and account queries.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        config: TradingConfig = DEFAULT_CONFIG,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.config = config
        self.base_url = config.ALPACA_PAPER_URL
        self._session: Optional[aiohttp.ClientSession] = None

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

    async def close(self):
        """Close the session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_account(self) -> Optional[Account]:
        """Get account information."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/account"

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return Account(
                        equity=float(data.get("equity", 0)),
                        cash=float(data.get("cash", 0)),
                        buying_power=float(data.get("buying_power", 0)),
                        portfolio_value=float(data.get("portfolio_value", 0)),
                        pattern_day_trader=data.get("pattern_day_trader", False),
                        trading_blocked=data.get("trading_blocked", False),
                        account_blocked=data.get("account_blocked", False),
                    )
                else:
                    text = await response.text()
                    logger.error(f"Failed to get account: {response.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Error getting account: {e}")
            return None

    async def get_positions(self) -> List[Position]:
        """Get all open positions."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/positions"

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    positions = []
                    for p in data:
                        positions.append(Position(
                            symbol=p.get("symbol"),
                            qty=float(p.get("qty", 0)),
                            avg_entry_price=float(p.get("avg_entry_price", 0)),
                            market_value=float(p.get("market_value", 0)),
                            unrealized_pl=float(p.get("unrealized_pl", 0)),
                            unrealized_plpc=float(p.get("unrealized_plpc", 0)),
                            current_price=float(p.get("current_price", 0)),
                            side=p.get("side", "long"),
                        ))
                    return positions
                else:
                    text = await response.text()
                    logger.error(f"Failed to get positions: {response.status} - {text}")
                    return []
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    async def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/positions/{symbol}"

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    p = await response.json()
                    return Position(
                        symbol=p.get("symbol"),
                        qty=float(p.get("qty", 0)),
                        avg_entry_price=float(p.get("avg_entry_price", 0)),
                        market_value=float(p.get("market_value", 0)),
                        unrealized_pl=float(p.get("unrealized_pl", 0)),
                        unrealized_plpc=float(p.get("unrealized_plpc", 0)),
                        current_price=float(p.get("current_price", 0)),
                        side=p.get("side", "long"),
                    )
                elif response.status == 404:
                    return None  # No position
                else:
                    text = await response.text()
                    logger.error(f"Failed to get position {symbol}: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting position {symbol}: {e}")
            return None

    async def submit_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Optional[Order]:
        """
        Submit an order to Alpaca.

        Args:
            symbol: Stock symbol
            qty: Number of shares
            side: Buy or sell
            order_type: Market or limit
            limit_price: Required for limit orders
            time_in_force: day, gtc, ioc, fok

        Returns:
            Order object if successful
        """
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders"

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side.value,
            "type": order_type.value,
            "time_in_force": time_in_force,
        }

        if order_type == OrderType.LIMIT and limit_price:
            payload["limit_price"] = str(limit_price)

        try:
            async with session.post(url, json=payload) as response:
                if response.status in (200, 201):
                    data = await response.json()
                    logger.info(f"Order submitted: {side.value} {qty} {symbol}")
                    return self._parse_order(data)
                else:
                    text = await response.text()
                    logger.error(f"Order failed: {response.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Error submitting order: {e}")
            return None

    async def buy(
        self,
        symbol: str,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Submit a buy order."""
        return await self.submit_order(symbol, qty, OrderSide.BUY, order_type)

    async def sell(
        self,
        symbol: str,
        qty: float,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Submit a sell order."""
        return await self.submit_order(symbol, qty, OrderSide.SELL, order_type)

    async def close_position(self, symbol: str) -> Optional[Order]:
        """Close an entire position."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/positions/{symbol}"

        try:
            async with session.delete(url) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Position closed: {symbol}")
                    return self._parse_order(data)
                elif response.status == 404:
                    logger.warning(f"No position to close: {symbol}")
                    return None
                else:
                    text = await response.text()
                    logger.error(f"Failed to close position: {response.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Error closing position {symbol}: {e}")
            return None

    async def close_all_positions(self) -> List[Order]:
        """Close all open positions."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/positions"

        try:
            async with session.delete(url) as response:
                if response.status == 200:
                    data = await response.json()
                    orders = [self._parse_order(o) for o in data if o]
                    logger.info(f"Closed {len(orders)} positions")
                    return [o for o in orders if o]
                elif response.status == 207:  # Multi-status
                    data = await response.json()
                    orders = []
                    for item in data:
                        if item.get("status") == 200:
                            orders.append(self._parse_order(item.get("body")))
                    return [o for o in orders if o]
                else:
                    text = await response.text()
                    logger.error(f"Failed to close all: {response.status} - {text}")
                    return []
        except Exception as e:
            logger.error(f"Error closing all positions: {e}")
            return []

    async def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders/{order_id}"

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_order(data)
                else:
                    return None
        except Exception as e:
            logger.error(f"Error getting order {order_id}: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        session = await self._get_session()
        url = f"{self.base_url}/v2/orders/{order_id}"

        try:
            async with session.delete(url) as response:
                if response.status in (200, 204):
                    logger.info(f"Order cancelled: {order_id}")
                    return True
                else:
                    text = await response.text()
                    logger.error(f"Cancel failed: {response.status} - {text}")
                    return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    async def get_latest_price(self, symbol: str) -> Optional[float]:
        """Get latest price for a symbol."""
        session = await self._get_session()
        url = f"{self.config.ALPACA_DATA_URL}/stocks/{symbol}/trades/latest"

        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get("trade", {}).get("p", 0))
                else:
                    return None
        except Exception as e:
            logger.error(f"Error getting price {symbol}: {e}")
            return None

    def _parse_order(self, data: dict) -> Optional[Order]:
        """Parse order response."""
        if not data:
            return None

        try:
            submitted_at = None
            if data.get("submitted_at"):
                submitted_at = datetime.fromisoformat(
                    data["submitted_at"].replace("Z", "+00:00")
                )

            filled_at = None
            if data.get("filled_at"):
                filled_at = datetime.fromisoformat(
                    data["filled_at"].replace("Z", "+00:00")
                )

            return Order(
                id=data.get("id", ""),
                symbol=data.get("symbol", ""),
                qty=float(data.get("qty", 0)),
                side=OrderSide(data.get("side", "buy")),
                type=OrderType(data.get("type", "market")),
                status=OrderStatus(data.get("status", "new")),
                filled_qty=float(data.get("filled_qty", 0)),
                filled_avg_price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else None,
                submitted_at=submitted_at,
                filled_at=filled_at,
            )
        except Exception as e:
            logger.error(f"Error parsing order: {e}")
            return None


if __name__ == "__main__":
    import os

    async def test_client():
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")

        if not api_key or not secret_key:
            print("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
            return

        client = AlpacaTrader(api_key, secret_key)

        try:
            # Get account
            print("\n=== Account ===")
            account = await client.get_account()
            if account:
                print(f"Equity: ${account.equity:,.2f}")
                print(f"Cash: ${account.cash:,.2f}")
                print(f"Buying Power: ${account.buying_power:,.2f}")

            # Get positions
            print("\n=== Positions ===")
            positions = await client.get_positions()
            if positions:
                for p in positions:
                    print(f"{p.symbol}: {p.qty} shares @ ${p.avg_entry_price:.2f}")
                    print(f"  P&L: ${p.unrealized_pl:.2f} ({p.unrealized_plpc*100:.1f}%)")
            else:
                print("No open positions")

        finally:
            await client.close()

    asyncio.run(test_client())
