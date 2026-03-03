"""
Pattern Poller for Account C — Cameron B2 Pattern Trader

Polls cameron_scores table for qualifying patterns and returns
CameronTradeSetup objects with entry, stop, target.

B2 filter stack (from E2E backtest, Sharpe 2.51, WR 57.3%):
- moderate-only strength
- RVOL >= 10
- 9:45-11:00 AM scan window (enforced by scanner, not here)
- Bull flag max 1/day
- target_1 exit
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Priority order: lower = higher priority
PATTERN_PRIORITY = {
    "consolidation_breakout": 0,
    "vwap_reclaim": 1,
    "bull_flag": 2,
}


@dataclass
class CameronTradeSetup:
    """A qualifying Cameron pattern ready for order submission."""
    symbol: str
    pattern_type: str        # 'consolidation_breakout', 'vwap_reclaim', 'bull_flag'
    pattern_strength: str    # always 'moderate' for B2
    entry_price: float
    stop_loss: float
    target_1: float
    risk_per_share: float    # abs(entry_price - stop_loss)
    gap_pct: float
    rvol: float
    scan_ts: datetime
    pattern_date: datetime
    direction: str = "bullish"  # Cameron is long-only
    candle_range: Optional[float] = None  # Not used by Cameron, but required by open_limit_position
    # Article enrichment (data collection, not a filter)
    has_news: bool = False
    article_count: int = 0


class CameronChecker:
    """
    Polls cameron_scores for patterns matching B2 filter stack.

    Filters applied:
    - pattern_strength = 'moderate'
    - scan_ts within lookback window
    - Not already seen this session
    - Bull flag daily cap (max_bf_per_day)
    - Priority sort: consol_breakout > vwap_reclaim > bull_flag, then rvol DESC
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        max_bf_per_day: int = 1,
        lookback_min: int = 10,
    ):
        self.database_url = database_url.strip() if database_url else database_url
        self.max_bf_per_day = max_bf_per_day
        self.lookback_min = lookback_min
        self._seen_patterns: Set[Tuple[str, str, str]] = set()  # (symbol, pattern_date, pattern_type)
        self._traded_today: Set[str] = set()  # symbols already traded today (survives redeploys via DB seed)
        self._bf_count_today: int = 0
        self._seeded_from_db: bool = False

    def reset_daily(self):
        """Clear seen patterns and bull flag count for a new trading day."""
        self._seen_patterns.clear()
        self._traded_today.clear()
        self._bf_count_today = 0
        self._seeded_from_db = False
        logger.info("CameronChecker: reset for new day")

    def _seed_seen_from_db(self):
        """Load today's traded symbols from paper_trades_log_c to prevent re-entries after restart."""
        if self._seeded_from_db or not self.database_url:
            return
        self._seeded_from_db = True
        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT symbol
                FROM paper_trades_log_c
                WHERE entry_time::date = CURRENT_DATE
            """)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            for (symbol,) in rows:
                self._traded_today.add(symbol)
            if rows:
                logger.info(
                    f"CameronChecker: seeded {len(rows)} symbols from today's trades "
                    f"(will not re-enter: {self._traded_today})"
                )
        except Exception as e:
            logger.warning(f"CameronChecker: failed to seed from DB: {e}")

    def poll_qualifying_patterns(self) -> List[CameronTradeSetup]:
        """
        Query cameron_scores for recent patterns that pass B2 filters.

        Returns list of CameronTradeSetup objects sorted by priority.
        """
        if not self.database_url:
            logger.warning("CameronChecker: no database_url configured")
            return []

        # On first poll, seed seen set from today's DB trades (survives redeploys)
        self._seed_seen_from_db()

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            cur.execute("""
                SELECT symbol, pattern_type, pattern_strength, entry_price,
                       stop_loss, target_1, gap_pct, rvol, scan_ts, pattern_date
                FROM cameron_scores
                WHERE scan_ts > NOW() - make_interval(mins := %s)
                  AND pattern_strength = 'moderate'
                  AND entry_price IS NOT NULL
                  AND stop_loss IS NOT NULL
                  AND target_1 IS NOT NULL
                ORDER BY scan_ts DESC
            """, (self.lookback_min,))

            rows = cur.fetchall()
            cur.close()
            conn.close()

        except Exception as e:
            logger.warning(f"CameronChecker: DB query failed: {e}")
            return []

        setups: List[CameronTradeSetup] = []
        skipped_seen = 0
        skipped_bf_cap = 0

        for row in rows:
            (symbol, pattern_type, pattern_strength, entry_price,
             stop_loss, target_1, gap_pct, rvol, scan_ts, pattern_date) = row

            entry_price = float(entry_price) if entry_price else 0
            stop_loss = float(stop_loss) if stop_loss else 0
            target_1 = float(target_1) if target_1 else 0
            gap_pct_val = float(gap_pct) if gap_pct else 0
            rvol_val = float(rvol) if rvol else 0

            # Skip symbols already traded today (persists across redeploys)
            if symbol in self._traded_today:
                skipped_seen += 1
                continue

            # Deduplicate by (symbol, pattern_date, pattern_type)
            key = (symbol, str(pattern_date), pattern_type)
            if key in self._seen_patterns:
                skipped_seen += 1
                continue
            self._seen_patterns.add(key)

            # Bull flag daily cap
            if pattern_type == "bull_flag" and self._bf_count_today >= self.max_bf_per_day:
                skipped_bf_cap += 1
                logger.debug(
                    f"CameronChecker SKIP {symbol}: bull_flag daily cap "
                    f"({self._bf_count_today}/{self.max_bf_per_day})"
                )
                continue

            risk_per_share = abs(entry_price - stop_loss)
            if risk_per_share < 0.01:
                continue  # degenerate pattern

            setups.append(CameronTradeSetup(
                symbol=symbol,
                pattern_type=pattern_type,
                pattern_strength=pattern_strength or "moderate",
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_1=target_1,
                risk_per_share=risk_per_share,
                gap_pct=gap_pct_val,
                rvol=rvol_val,
                scan_ts=scan_ts,
                pattern_date=pattern_date,
            ))

            # Track bull flag count
            if pattern_type == "bull_flag":
                self._bf_count_today += 1

        # Sort by priority (consol > vwap > bf), then rvol DESC
        setups.sort(key=lambda s: (
            PATTERN_PRIORITY.get(s.pattern_type, 99),
            -s.rvol,
        ))

        if rows:
            logger.info(
                f"CameronChecker: {len(rows)} raw, {len(setups)} qualified, "
                f"skipped: {skipped_seen} seen, {skipped_bf_cap} bf_cap"
            )

        return setups
