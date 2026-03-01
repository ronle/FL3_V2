"""
Momentum Screener (v58)

Screens for beaten-down stocks at ~3:50 PM ET using D-1 data from orats_daily.
Buys top candidates at 3:56 PM, holds overnight, exits next day at 3:55 PM.

V6 research validated: price_momentum_20d < -0.10, top 10 most beaten-down,
$10+, ADV>=1K, -3% stop = Sharpe 1.03 (minute bars, slippage, 3/3 years+).
"""

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class MomentumCandidate:
    """A stock that passed the momentum screen."""
    symbol: str
    momentum: float  # price_momentum_20d from orats_daily (negative = more beaten down)
    price: float
    adv: float  # avg_daily_volume from orats_daily


class MomentumScreener:
    """
    EOD screener: finds beaten-down stocks (momentum < threshold) from orats_daily.

    Uses D-1 convention (most recent asof_date < today) to match backtest
    methodology and avoid look-ahead bias.
    """

    def __init__(self, db_pool, momentum_threshold: float = -0.10,
                 price_floor: float = 10.0, min_adv: int = 1000,
                 max_candidates: int = 10):
        self.db_pool = db_pool
        self.momentum_threshold = momentum_threshold
        self.price_floor = price_floor
        self.min_adv = min_adv
        self.max_candidates = max_candidates

    async def screen(self) -> List[MomentumCandidate]:
        """
        Query orats_daily for D-1 data, filter to momentum < threshold +
        price >= floor + ADV >= min.
        Rank by momentum ascending (most beaten-down first).
        Return top max_candidates.
        """
        try:
            candidates = await self._fetch_and_filter()
            if candidates:
                top3 = candidates[:3]
                logger.info(
                    f"Momentum screen: {len(candidates)} candidates | "
                    f"Top 3: {', '.join(f'{c.symbol} mom={c.momentum:.1%}' for c in top3)}"
                )
            else:
                logger.info("Momentum screen: 0 candidates")
            return candidates

        except Exception as e:
            logger.error(f"Momentum screen failed: {e}", exc_info=True)
            return []

    async def _fetch_and_filter(self) -> List[MomentumCandidate]:
        """
        Single query: fetch D-1 symbols with momentum < threshold,
        price >= floor, ADV >= min. Already sorted by momentum ascending.

        Uses a subquery for max(asof_date) to avoid DISTINCT ON across 8M+ rows
        (the old query timed out on Cloud Run 2026-02-26).
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT symbol, price_momentum_20d, stock_price, avg_daily_volume
                FROM orats_daily
                WHERE asof_date = (
                    SELECT MAX(asof_date) FROM orats_daily
                    WHERE asof_date < CURRENT_DATE
                )
                  AND stock_price >= $1
                  AND avg_daily_volume >= $2
                  AND price_momentum_20d IS NOT NULL
                  AND price_momentum_20d < $3
                ORDER BY price_momentum_20d ASC
            """, self.price_floor, self.min_adv, self.momentum_threshold)

        candidates = [
            MomentumCandidate(
                symbol=r['symbol'],
                momentum=float(r['price_momentum_20d']),
                price=float(r['stock_price']),
                adv=float(r['avg_daily_volume']),
            )
            for r in rows
        ]

        # Sort by momentum ascending (most beaten-down first)
        candidates.sort(key=lambda c: c.momentum)

        # Return top N
        return candidates[:self.max_candidates]
