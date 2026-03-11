"""
Pattern Poller for Account B — Big-Hitter Pattern Trader

Polls engulfing_scores table for qualifying 5-min patterns and returns
TradeSetup objects with entry, stop, target, and direction.

Replaces the old EngulfingChecker (v59 and earlier).
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeSetup:
    """A qualifying pattern ready for order submission."""
    symbol: str
    direction: str           # 'bullish' or 'bearish'
    entry_price: float
    stop_loss: float
    target_1: float
    risk_per_share: float    # abs(entry_price - stop_loss)
    candle_range: float
    pattern_strength: str    # 'strong', 'moderate', 'weak'
    pattern_date: datetime
    scan_ts: datetime


class PatternPoller:
    """
    Polls engulfing_scores for 5-min patterns matching big-hitter profile.

    Filters applied:
    - timeframe = '5min'
    - scan_ts within lookback window
    - volume_confirmed = TRUE
    - trend_context IN ('uptrend', 'downtrend')
    - candle_range <= max_candle_range
    - risk_per_share >= min_risk_per_share
    - Not already seen this session
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        max_candle_range: float = 0.57,
        min_risk_per_share: float = 1.00,
        lookback_min: int = 10,
        filter_weak: bool = True,
    ):
        self.database_url = database_url.strip() if database_url else database_url
        self.max_candle_range = max_candle_range
        self.min_risk_per_share = min_risk_per_share
        self.lookback_min = lookback_min
        self.filter_weak = filter_weak
        self._seen_patterns: Set[Tuple[str, str]] = set()  # (symbol, pattern_date_str)

    def reset_daily(self):
        """Clear seen patterns for a new trading day."""
        self._seen_patterns.clear()
        logger.info("PatternPoller: reset seen patterns for new day")

    def poll_qualifying_patterns(self) -> List[TradeSetup]:
        """
        Query engulfing_scores for recent 5-min patterns that pass big-hitter filters.

        Returns list of TradeSetup objects for patterns not yet seen this session.
        """
        if not self.database_url:
            logger.warning("PatternPoller: no database_url configured")
            return []

        try:
            import psycopg2
            conn = psycopg2.connect(self.database_url)
            cur = conn.cursor()

            cur.execute("""
                SELECT symbol, direction, entry_price, stop_loss, target_1,
                       candle_range, pattern_strength, pattern_date, scan_ts
                FROM engulfing_scores
                WHERE timeframe = '5min'
                  AND scan_ts > NOW() - make_interval(mins := %s)
                  AND volume_confirmed = TRUE
                  AND trend_context IN ('uptrend', 'downtrend')
                  AND entry_price IS NOT NULL
                  AND stop_loss IS NOT NULL
                  AND target_1 IS NOT NULL
                ORDER BY scan_ts DESC
            """, (self.lookback_min,))

            rows = cur.fetchall()
            cur.close()
            conn.close()

        except Exception as e:
            logger.warning(f"PatternPoller: DB query failed: {e}")
            return []

        setups: List[TradeSetup] = []
        skipped_seen = 0
        skipped_range = 0
        skipped_risk = 0
        skipped_weak = 0

        for row in rows:
            symbol, direction, entry_price, stop_loss, target_1, \
                candle_range, pattern_strength, pattern_date, scan_ts = row

            entry_price = float(entry_price) if entry_price else 0
            stop_loss = float(stop_loss) if stop_loss else 0
            target_1 = float(target_1) if target_1 else 0
            candle_range_val = float(candle_range) if candle_range else 0

            # Deduplicate: skip if we've already evaluated this pattern
            key = (symbol, str(pattern_date))
            if key in self._seen_patterns:
                skipped_seen += 1
                continue
            self._seen_patterns.add(key)

            # Filter: candle range
            if candle_range_val > self.max_candle_range:
                skipped_range += 1
                logger.debug(
                    f"PatternPoller SKIP {symbol}: candle_range={candle_range_val:.2f} "
                    f"> max {self.max_candle_range}"
                )
                continue

            # Compute risk per share
            risk_per_share = abs(entry_price - stop_loss)

            # Filter: minimum risk per share (avoid tiny stops that get clipped)
            if risk_per_share < self.min_risk_per_share:
                skipped_risk += 1
                logger.debug(
                    f"PatternPoller SKIP {symbol}: risk_per_share=${risk_per_share:.2f} "
                    f"< min ${self.min_risk_per_share}"
                )
                continue

            # Filter: pattern strength (v78 — weak patterns avg -$54/trade)
            if self.filter_weak and (pattern_strength or "").lower() == "weak":
                skipped_weak += 1
                logger.debug(f"PatternPoller SKIP {symbol}: weak pattern")
                continue

            setups.append(TradeSetup(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_1=target_1,
                risk_per_share=risk_per_share,
                candle_range=candle_range_val,
                pattern_strength=pattern_strength or "unknown",
                pattern_date=pattern_date,
                scan_ts=scan_ts,
            ))

        if rows:
            logger.info(
                f"PatternPoller: {len(rows)} raw, {len(setups)} qualified, "
                f"skipped: {skipped_seen} seen, {skipped_range} range, {skipped_risk} risk, {skipped_weak} weak"
            )

        return setups
