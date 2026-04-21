"""
RunningState — Persistent cross-day state for month-long emulator training.

Maintains symbol continuity across days by:
1. Tracking daily OHLCV history per real symbol
2. Generating a consistent anonymization map (real → 6-char code) for the entire run
3. Carrying portfolio equity across days
"""

import random
import string
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DailyBar:
    date: str
    o: float
    h: float
    l: float
    c: float
    v: int


class RunningState:
    """Cross-day state for a full-month emulator training run."""

    def __init__(self, starting_balance: float = 100_000.0, seed: int = 42):
        self.daily_history: dict[str, list[DailyBar]] = {}  # real symbol → daily bars
        self.anon_map: dict[str, str] = {}    # real → anon
        self.reverse_map: dict[str, str] = {}  # anon → real
        self.running_equity: float = starting_balance
        self.starting_balance: float = starting_balance
        self._rng = random.Random(seed)
        self._used_codes: set[str] = set()

    def _generate_code(self) -> str:
        """Generate a unique random 6-char uppercase code."""
        while True:
            code = "".join(self._rng.choices(string.ascii_uppercase, k=6))
            if code not in self._used_codes:
                self._used_codes.add(code)
                return code

    def ensure_mapped(self, symbols: list[str]) -> None:
        """Assign anon codes to any new real symbols. Existing mappings are preserved."""
        for sym in symbols:
            if sym not in self.anon_map:
                code = self._generate_code()
                self.anon_map[sym] = code
                self.reverse_map[code] = sym

    def anonymize_symbol(self, real: str) -> str:
        """Translate a real symbol to its anonymous code."""
        return self.anon_map.get(real, real)

    def deanonymize_symbol(self, anon: str) -> str:
        """Translate an anonymous code back to the real symbol."""
        return self.reverse_map.get(anon, anon)

    def anonymize_bars(self, real_bars: dict[str, dict]) -> dict[str, dict]:
        """Replace real symbol keys with anonymous codes."""
        return {self.anonymize_symbol(sym): bar for sym, bar in real_bars.items()}

    def deanonymize_bars(self, anon_bars: dict[str, dict]) -> dict[str, dict]:
        """Replace anonymous code keys with real symbols."""
        return {self.deanonymize_symbol(sym): bar for sym, bar in anon_bars.items()}

    def record_day_close(self, real_bars: dict[str, dict], trade_date: str) -> None:
        """Append today's EOD bar to daily history for each symbol."""
        for sym, bar in real_bars.items():
            if bar.get("c", 0) <= 0:
                continue
            daily = DailyBar(
                date=trade_date,
                o=bar.get("o", 0),
                h=bar.get("h", 0),
                l=bar.get("l", 0),
                c=bar.get("c", 0),
                v=int(bar.get("v", 0)),
            )
            if sym not in self.daily_history:
                self.daily_history[sym] = []
            self.daily_history[sym].append(daily)

    def get_prev_close(self, real_symbol: str) -> Optional[float]:
        """Get yesterday's close for a real symbol. None if no history."""
        hist = self.daily_history.get(real_symbol)
        if hist and len(hist) > 0:
            return hist[-1].c
        return None

    def get_daily_closes(self, real_symbol: str) -> list[float]:
        """Get list of daily close prices for a real symbol."""
        hist = self.daily_history.get(real_symbol, [])
        return [b.c for b in hist]

    def get_daily_volumes(self, real_symbol: str) -> list[int]:
        """Get list of daily volumes for a real symbol."""
        hist = self.daily_history.get(real_symbol, [])
        return [b.v for b in hist]

    def get_daily_ohlcv(self, real_symbol: str) -> list[DailyBar]:
        """Get full daily OHLCV history for a real symbol."""
        return self.daily_history.get(real_symbol, [])

    def update_equity(self, final_equity: float) -> None:
        """Update running equity from session's final account."""
        self.running_equity = final_equity

    def history_depth(self, real_symbol: str) -> int:
        """Number of daily bars available for a symbol."""
        return len(self.daily_history.get(real_symbol, []))
