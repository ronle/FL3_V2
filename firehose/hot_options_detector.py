"""
Hot Options Detector

Detects symbols with unusual options activity every 5 minutes by comparing
current volume against orats_daily baselines with time-of-day multipliers.

For each flagged symbol, finds the top contract by volume and computes
concentration metrics (unique_contracts, avg_vol_per_contract).

Writes to `hot_options` DB table for dashboard consumption.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, date, timezone
from typing import Optional

import pytz

from utils.occ_parser import extract_right

logger = logging.getLogger(__name__)

# 78 five-minute periods in a 6.5-hour trading day (9:30-16:00)
PERIODS_PER_DAY = 78

# Time-of-day volume multipliers
TOD_MULTIPLIERS = {
    "open": 2.0,    # 9:30-10:00
    "close": 2.0,   # 15:30-16:00
    "midday": 0.6,  # 11:00-14:00
    "normal": 1.0,  # everything else
}


def _get_tod_multiplier(hour: int, minute: int) -> float:
    """Time-of-day volume multiplier based on ET hour/minute."""
    if hour == 9 and minute >= 30:
        return TOD_MULTIPLIERS["open"]
    if hour == 10 and minute < 0:  # first 30 min already covered above
        return TOD_MULTIPLIERS["open"]
    if hour == 15 and minute >= 30:
        return TOD_MULTIPLIERS["close"]
    if 11 <= hour <= 13:
        return TOD_MULTIPLIERS["midday"]
    return TOD_MULTIPLIERS["normal"]


class HotOptionsDetector:
    """Detects symbols with unusual options activity every 5 minutes."""

    def __init__(
        self,
        rolling_agg,
        db_pool,
        min_volume_ratio: float = 3.0,
        min_contracts: int = 100,
        cooldown_seconds: int = 300,
    ):
        """
        Args:
            rolling_agg: RollingAggregator instance (5-min window)
            db_pool: asyncpg connection pool
            min_volume_ratio: Minimum volume/baseline ratio to flag
            min_contracts: Minimum contracts in window to flag
            cooldown_seconds: Min time between detections for same symbol
        """
        self.rolling_agg = rolling_agg
        self.db_pool = db_pool
        self.min_volume_ratio = min_volume_ratio
        self.min_contracts = min_contracts
        self.cooldown_seconds = cooldown_seconds

        self._baselines: dict[str, int] = {}  # symbol -> avg_daily_volume
        self._last_baseline_refresh: float = 0
        self._recent_detections: dict[str, float] = {}  # symbol -> last detection time

        # Metrics
        self._detect_count = 0
        self._symbols_checked = 0
        self._hot_found = 0

    async def refresh_baselines(self):
        """Load avg_daily_volume from orats_daily (call every 30 min)."""
        if not self.db_pool:
            return

        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT symbol, avg_daily_volume FROM orats_daily "
                    "WHERE avg_daily_volume IS NOT NULL AND avg_daily_volume > 0"
                )
                self._baselines = {r["symbol"]: int(r["avg_daily_volume"]) for r in rows}
                self._last_baseline_refresh = time.time()
                logger.info(f"Loaded {len(self._baselines)} orats baselines for hot detection")
        except Exception as e:
            logger.error(f"Failed to refresh baselines: {e}")

    def _get_expected_volume(self, symbol: str, hour: int, minute: int) -> Optional[float]:
        """Get expected 5-min volume for a symbol at this time of day."""
        adv = self._baselines.get(symbol)
        if not adv or adv <= 0:
            return None

        per_period = adv / PERIODS_PER_DAY
        multiplier = _get_tod_multiplier(hour, minute)
        return per_period * multiplier

    def _in_cooldown(self, symbol: str) -> bool:
        """Check if symbol was recently detected."""
        last = self._recent_detections.get(symbol)
        if last is None:
            return False
        return (time.time() - last) < self.cooldown_seconds

    def _get_top_contract(self, symbol: str) -> dict:
        """
        Scan rolling_agg raw trades to find top contract by volume.
        Also computes concentration metrics.

        Returns dict with: top_contract, top_contract_volume, top_contract_notional,
                          unique_contracts, avg_vol_per_contract, call_volume, put_volume
        """
        now = time.time()
        cutoff = now - self.rolling_agg.window_seconds

        contract_volume: dict[str, int] = defaultdict(int)
        contract_notional: dict[str, float] = defaultdict(float)
        call_vol = 0
        put_vol = 0
        prints = 0

        with self.rolling_agg._lock:
            trades = self.rolling_agg._trades.get(symbol, [])
            for ts, trade in trades:
                if ts < cutoff:
                    continue
                prints += 1
                contract_volume[trade.option_symbol] += trade.size
                contract_notional[trade.option_symbol] += trade.notional

                right = extract_right(trade.option_symbol)
                if right == "C":
                    call_vol += trade.size
                elif right == "P":
                    put_vol += trade.size

        if not contract_volume:
            return {
                "top_contract": None, "top_contract_volume": 0,
                "top_contract_notional": 0.0, "unique_contracts": 0,
                "avg_vol_per_contract": 0.0, "call_volume": 0,
                "put_volume": 0, "prints": 0,
            }

        # Find top contract by volume
        top_occ = max(contract_volume, key=contract_volume.get)
        unique = len(contract_volume)
        total_contracts = sum(contract_volume.values())
        total_notional = sum(contract_notional.values())

        return {
            "top_contract": top_occ,
            "top_contract_volume": contract_volume[top_occ],
            "top_contract_notional": contract_notional[top_occ],
            "unique_contracts": unique,
            "avg_vol_per_contract": total_contracts / unique if unique > 0 else 0.0,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "prints": prints,
            "total_contracts": total_contracts,
            "total_notional": total_notional,
        }

    def detect(self) -> list[dict]:
        """
        Run detection across all active symbols.
        Returns list of hot symbol dicts ready for DB insert.
        """
        self._detect_count += 1

        # Need baselines
        if not self._baselines:
            logger.debug("No baselines loaded, skipping hot detection")
            return []

        # Get current ET time for ToD multiplier
        now_et = datetime.now(pytz.timezone("US/Eastern"))
        hour, minute = now_et.hour, now_et.minute

        active_symbols = self.rolling_agg.get_all_active_symbols()
        self._symbols_checked += len(active_symbols)
        hot_symbols = []

        for symbol in active_symbols:
            if self._in_cooldown(symbol):
                continue

            stats = self.rolling_agg.get_stats(symbol)
            if not stats or stats.total_contracts < self.min_contracts:
                continue

            expected = self._get_expected_volume(symbol, hour, minute)
            if not expected or expected <= 0:
                continue

            volume_ratio = stats.total_contracts / expected
            if volume_ratio < self.min_volume_ratio:
                continue

            # This symbol is hot — get top contract details
            details = self._get_top_contract(symbol)
            self._recent_detections[symbol] = time.time()

            hot_symbols.append({
                "symbol": symbol,
                "detected_at": now_et,
                "contracts": details["total_contracts"],
                "notional": details["total_notional"],
                "prints": details["prints"],
                "call_volume": details["call_volume"],
                "put_volume": details["put_volume"],
                "unique_contracts": details["unique_contracts"],
                "avg_vol_per_contract": details["avg_vol_per_contract"],
                "volume_ratio": round(volume_ratio, 2),
                "baseline_volume": int(expected),
                "top_contract": details["top_contract"],
                "top_contract_volume": details["top_contract_volume"],
                "top_contract_notional": details["top_contract_notional"],
                "trade_date": now_et.date(),
            })

        self._hot_found += len(hot_symbols)
        return hot_symbols

    async def flush_to_db(self, hot_symbols: list[dict]):
        """Write detected hot symbols to hot_options table."""
        if not hot_symbols or not self.db_pool:
            return

        try:
            async with self.db_pool.acquire() as conn:
                await conn.executemany("""
                    INSERT INTO hot_options
                    (symbol, detected_at, contracts, notional, prints,
                     call_volume, put_volume, unique_contracts, avg_vol_per_contract,
                     volume_ratio, baseline_volume, top_contract, top_contract_volume,
                     top_contract_notional, trade_date)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (symbol, trade_date, detected_at) DO NOTHING
                """, [
                    (
                        h["symbol"], h["detected_at"], h["contracts"],
                        h["notional"], h["prints"], h["call_volume"],
                        h["put_volume"], h["unique_contracts"],
                        h["avg_vol_per_contract"], h["volume_ratio"],
                        h["baseline_volume"], h["top_contract"],
                        h["top_contract_volume"], h["top_contract_notional"],
                        h["trade_date"],
                    )
                    for h in hot_symbols
                ])
            logger.info(f"Flushed {len(hot_symbols)} hot options to DB")
        except Exception as e:
            logger.error(f"Failed to flush hot options: {e}")

    def get_metrics(self) -> dict:
        return {
            "detect_runs": self._detect_count,
            "symbols_checked": self._symbols_checked,
            "hot_found": self._hot_found,
            "baselines_loaded": len(self._baselines),
            "cooldown_active": len(self._recent_detections),
        }
