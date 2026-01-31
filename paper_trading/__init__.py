"""
Paper Trading Package

Components:
- config: Trading configuration
- alpaca_trader: Alpaca API client
- position_manager: Position tracking and limits
- signal_filter: Entry rule filtering
- eod_closer: End-of-day position closing
- main: Main orchestrator
"""

from .config import TradingConfig, DEFAULT_CONFIG
from .alpaca_trader import AlpacaTrader, Position, Order, Account
from .position_manager import PositionManager, TradeRecord
from .signal_filter import SignalFilter, Signal, SignalGenerator, FilterResult
from .eod_closer import EODCloser
from .trade_aggregator import TradeAggregator

__all__ = [
    "TradingConfig",
    "DEFAULT_CONFIG",
    "AlpacaTrader",
    "Position",
    "Order",
    "Account",
    "PositionManager",
    "TradeRecord",
    "SignalFilter",
    "Signal",
    "SignalGenerator",
    "FilterResult",
    "EODCloser",
    "TradeAggregator",
]
