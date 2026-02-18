"""
Engulfing Pattern Checker for Account B

Checks the engulfing_scores table for bullish engulfing pattern confirmation.
Written by a separate DayTrading scanner agent; we only read from it.

Architecture: Daily watchlist (bulk-loaded, O(1) lookups) + per-query 5-min fallback.
Account B checks engulfing BEFORE the filter chain — it's the primary gate.
"""

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class EngulfingChecker:
    """
    Engulfing pattern checker with daily watchlist cache + 5-min fallback.

    Daily patterns (timeframe='1D') are bulk-loaded into memory at startup
    and daily reset. 5-min patterns are queried per-symbol as fallback.
    """

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url.strip() if database_url else database_url
        self._daily_watchlist: Dict[str, Dict] = {}

    def load_daily_watchlist(self, lookback_hours: int = 20):
        """
        Bulk-load daily bullish engulfing patterns from last N hours into memory.
        Called at startup and daily reset.

        Args:
            lookback_hours: How far back to look for daily patterns (default 20h)
        """
        if not self.database_url:
            logger.warning("EngulfingChecker: no database_url, skipping daily watchlist load")
            return

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, pattern_strength, scan_ts, pattern_date
                FROM engulfing_scores
                WHERE direction = 'bullish'
                  AND timeframe IN ('1D', 'daily')
                  AND scan_ts > NOW() - make_interval(hours := %s)
                ORDER BY scan_ts DESC
            """, (lookback_hours,))

            self._daily_watchlist = {}
            for row in cur.fetchall():
                symbol = row[0]
                # Keep first (most recent) entry per symbol
                if symbol not in self._daily_watchlist:
                    self._daily_watchlist[symbol] = {
                        "pattern_strength": row[1],
                        "scan_ts": row[2],
                        "pattern_date": row[3],
                    }

            cur.close()
            conn.close()

            logger.info(
                f"Engulfing daily watchlist loaded: {len(self._daily_watchlist)} symbols "
                f"(lookback={lookback_hours}h)"
            )
            if self._daily_watchlist:
                symbols = sorted(self._daily_watchlist.keys())[:10]
                logger.info(f"  Sample: {', '.join(symbols)}{'...' if len(self._daily_watchlist) > 10 else ''}")

        except Exception as e:
            logger.warning(f"Failed to load daily engulfing watchlist: {e}")
            self._daily_watchlist = {}

    def has_engulfing_confirmation(
        self, symbol: str, lookback_minutes: int = 30
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Check if symbol has a recent bullish engulfing pattern.

        Checks daily watchlist first (O(1)), then falls back to per-query
        5-min check.

        Args:
            symbol: Ticker to check
            lookback_minutes: How recent the 5-min pattern must be (default 30)

        Returns:
            (True, {"pattern_strength": ..., "scan_ts": ..., "pattern_date": ...}) or
            (False, None)
        """
        # 1. Check daily watchlist (O(1) dict lookup)
        if symbol in self._daily_watchlist:
            data = self._daily_watchlist[symbol]
            logger.info(
                f"Engulfing confirmed (daily): {symbol} "
                f"strength={data['pattern_strength']} scan_ts={data['scan_ts']}"
            )
            return True, data

        # 2. Fallback: per-query 5-min check
        if not self.database_url:
            return False, None

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, direction, pattern_date, pattern_strength, scan_ts
                FROM engulfing_scores
                WHERE symbol = %s
                  AND direction = 'bullish'
                  AND timeframe = '5min'
                  AND scan_ts > NOW() - make_interval(mins := %s)
                ORDER BY scan_ts DESC
                LIMIT 1
            """, (symbol, lookback_minutes))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                data = {
                    "pattern_strength": row[3],
                    "scan_ts": row[4],
                    "pattern_date": row[2],
                }
                logger.info(
                    f"Engulfing confirmed (5min): {symbol} "
                    f"strength={data['pattern_strength']} scan_ts={data['scan_ts']}"
                )
                return True, data

            return False, None

        except Exception as e:
            logger.warning(f"Engulfing check failed for {symbol}: {e}")
            return False, None

    def get_volume_ratio(self, symbol: str) -> Optional[float]:
        """Fetch volume_vs_ema30 from orats_daily for display/logging (not filtering)."""
        if not self.database_url:
            return None

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT total_volume, volume_ema_30d
                FROM orats_daily
                WHERE symbol = %s AND total_volume IS NOT NULL
                ORDER BY asof_date DESC
                LIMIT 1
            """, (symbol,))

            row = cur.fetchone()
            cur.close()
            conn.close()

            if row and row[1] and row[1] > 0:
                return float(row[0]) / float(row[1])
            return None

        except Exception as e:
            logger.warning(f"Volume ratio fetch failed for {symbol}: {e}")
            return None
