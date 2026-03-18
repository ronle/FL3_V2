"""
Pattern Poller for Account B — Big-Hitter Pattern Trader

Polls engulfing_scores table for qualifying 5-min patterns and returns
TradeSetup objects with entry, stop, target, and direction.

Replaces the old EngulfingChecker (v59 and earlier).

## Account B Filter Stack (v79 — 2026-03-18)

Filters are applied in three stages across two files.

### Stage 1 — DB query + post-query (this file, PatternPoller)
  - timeframe = '5min'
  - scan_ts within last N minutes (ACCOUNT_B_LOOKBACK_MIN, default 10)
  - volume_confirmed = TRUE
  - trend_context IN ('uptrend', 'downtrend')
  - candle_range <= ACCOUNT_B_MAX_CANDLE_RANGE (default 0.57)
  - risk_per_share >= ACCOUNT_B_MIN_RISK_PER_SHARE (default $1.00)
  - pattern_strength != 'weak' (if ACCOUNT_B_FILTER_WEAK=True)
  - Not already seen this session (dedup set)

### Stage 2 — TA filters (main.py / _poll_account_b_patterns)
  FAIL-CLOSED (v79 fix): if TA data missing for symbol → trade SKIPPED.
  Before v79, the filter was fail-open: trades passed silently when RSI was
  unavailable, causing signal_rsi=0 in paper_trades_log_b across all 246
  live trades (Feb 18 – Mar 18 2026). This was the primary driver of poor
  live performance vs backtest expectations.

  - RSI momentum gate (ACCOUNT_B_REQUIRE_MOMENTUM_RSI=True):
      Bullish: RSI >= ACCOUNT_B_RSI_BULL_MIN (55)
      Bearish: RSI <= ACCOUNT_B_RSI_BEAR_MAX (45)
      Backtest: 75.8% WR with filter vs 40.9% without (99-trade sample)
  - Trend alignment (ACCOUNT_B_REQUIRE_TREND_ALIGNMENT=True):
      Bullish: SMA20 > SMA50
      Bearish: SMA20 < SMA50
      Backtest: 67.4% WR with filter vs 42.4% without (99-trade sample)

### Stage 3 — Time gate (main.py / _poll_account_b_patterns)
  - No entries before 9:35 AM ET (v79: buffer for open-bar noise. Live data
    showed 39% WR in the 9am hour vs 58% WR at 10am, partly from patterns
    firing on incomplete first candles at 9:30)
  - No entries after 11:00 AM ET (v73: 3yr backtest morning +$17K, afternoon -$209)

## Live Performance Notes (Feb 18 – Mar 18 2026, 246 real trades)
  - RSI filter NOT applied due to fail-open bug — all signal_rsi = 0
  - 9:30-9:35 window: 39% WR (worst); 10am: 58% WR (best)
  - EOD exits were entire profit engine (+$5,731 of +$4,873 total)
  - Stop exits: -$14,531 (primary drag — system entered bad setups freely)
  - v79 fixes: fail-closed TA gate + 9:35 AM buffer + RSI logged correctly

## Related files
  - paper_trading/main.py               Stage 2+3 filters (_poll_account_b_patterns)
  - paper_trading/config.py             All ACCOUNT_B_* settings
  - docs/ACCOUNT_B_TRADING_LOGIC.md     Human-readable spec
  - Projects/DayTrading/ACCOUNT_B_TRADING_LOGIC.md  DayTrading-side spec
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TradeSetup:
    """
    A qualifying pattern ready for order submission.

    Core fields populated by PatternPoller (Stage 1):
      symbol, direction, entry_price, stop_loss, target_1,
      risk_per_share, candle_range, pattern_strength, pattern_date, scan_ts

    TA fields populated by _poll_account_b_patterns after TA lookup (Stage 2):
      rsi_14, sma_20, sma_50

    These TA fields are written to paper_trades_log_b.signal_rsi so that
    post-hoc analysis can verify filters actually ran. If TA is unavailable,
    the trade is REJECTED before TradeSetup reaches the order stage — so
    rsi_14=None in a live TradeSetup means TA lookup hasn't happened yet,
    not that TA was missing and the trade slipped through.
    """
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
    rsi_14: Optional[float] = field(default=None)
    sma_20: Optional[float] = field(default=None)
    sma_50: Optional[float] = field(default=None)


class PatternPoller:
    """
    Polls engulfing_scores for 5-min patterns matching big-hitter profile.

    Applies Stage 1 filters only (DB query + post-query).
    Stage 2 (TA fail-closed) and Stage 3 (time gate) are in main.py.
    See module docstring for complete filter stack and performance history.
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
