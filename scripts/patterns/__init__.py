"""
Cameron Pattern Detection Modules

Follows DayTrading engulfing-scanner interface convention:
  detect_[pattern](symbol: str, df: pd.DataFrame, interval: str) -> Dataclass | None

Input df columns: ts, open, high, low, close, volume (sorted ascending by ts)
"""

from scripts.patterns.bull_flag import detect_bull_flag, BullFlagPattern
from scripts.patterns.consolidation_breakout import detect_consolidation_breakout, ConsolidationBreakout
from scripts.patterns.vwap_reclaim import detect_vwap_reclaim, VWAPReclaim
