#!/usr/bin/env python3
"""
Outcome Labeler (Component 6.1)

Labels historical triggers with forward returns for backtest validation.
Joins UOA triggers with orats_daily_returns to compute:
- 1-day, 3-day, 5-day, 10-day forward returns
- Success labels (e.g., >5% return within 5 days)

Usage:
    python -m scripts.label_outcomes
    python -m scripts.label_outcomes --days 30  # Last 30 days
"""

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class LabeledOutcome:
    """Labeled outcome for a trigger."""
    symbol: str
    trigger_ts: datetime
    trigger_type: str
    volume_ratio: float

    # Forward returns
    return_1d: Optional[float] = None
    return_3d: Optional[float] = None
    return_5d: Optional[float] = None
    return_10d: Optional[float] = None

    # Success labels
    success_5pct_5d: bool = False  # >5% return within 5 days
    success_10pct_10d: bool = False  # >10% return within 10 days
    max_return: Optional[float] = None
    max_return_day: Optional[int] = None


@dataclass
class LabelingSummary:
    """Summary of labeling results."""
    total_triggers: int = 0
    labeled_triggers: int = 0
    unlabeled_triggers: int = 0

    # Success rates
    success_5pct_5d_count: int = 0
    success_5pct_5d_rate: float = 0.0
    success_10pct_10d_count: int = 0
    success_10pct_10d_rate: float = 0.0

    # Return statistics
    avg_return_1d: float = 0.0
    avg_return_5d: float = 0.0
    avg_return_10d: float = 0.0
    median_max_return: float = 0.0


class OutcomeLabeler:
    """
    Labels UOA triggers with forward returns.

    Uses orats_daily_returns table to compute forward returns
    for each trigger, then applies success criteria.

    Success Criteria:
    - success_5pct_5d: Max return >= 5% within 5 trading days
    - success_10pct_10d: Max return >= 10% within 10 trading days

    Usage:
        labeler = OutcomeLabeler(db_pool)
        outcomes = await labeler.label_triggers(days=30)
        summary = labeler.get_summary(outcomes)
    """

    def __init__(
        self,
        db_pool=None,
        success_threshold_5d: float = 0.05,  # 5%
        success_threshold_10d: float = 0.10,  # 10%
    ):
        """
        Initialize labeler.

        Args:
            db_pool: Database connection pool
            success_threshold_5d: Return threshold for 5-day success
            success_threshold_10d: Return threshold for 10-day success
        """
        self.db_pool = db_pool
        self.success_threshold_5d = success_threshold_5d
        self.success_threshold_10d = success_threshold_10d

    async def label_triggers(
        self,
        days: int = 30,
        symbol: Optional[str] = None,
    ) -> list[LabeledOutcome]:
        """
        Label triggers with forward returns.

        Args:
            days: Number of days of triggers to label
            symbol: Optional symbol filter

        Returns:
            List of LabeledOutcome
        """
        if not self.db_pool:
            logger.warning("No db_pool, returning mock data")
            return self._generate_mock_outcomes()

        # Query triggers
        triggers = await self._fetch_triggers(days, symbol)
        logger.info(f"Fetched {len(triggers)} triggers to label")

        # Label each trigger
        outcomes = []
        for trigger in triggers:
            outcome = await self._label_single_trigger(trigger)
            outcomes.append(outcome)

        labeled = sum(1 for o in outcomes if o.return_1d is not None)
        logger.info(f"Labeled {labeled}/{len(outcomes)} triggers")

        return outcomes

    async def _fetch_triggers(
        self,
        days: int,
        symbol: Optional[str],
    ) -> list[dict]:
        """Fetch triggers from database."""
        try:
            async with self.db_pool.acquire() as conn:
                if symbol:
                    rows = await conn.fetch("""
                        SELECT symbol, trigger_ts, trigger_type, volume_ratio
                        FROM uoa_triggers_v2
                        WHERE trigger_ts >= NOW() - INTERVAL '%s days'
                        AND symbol = $2
                        ORDER BY trigger_ts
                    """, days, symbol)
                else:
                    rows = await conn.fetch("""
                        SELECT symbol, trigger_ts, trigger_type, volume_ratio
                        FROM uoa_triggers_v2
                        WHERE trigger_ts >= NOW() - INTERVAL '%s days'
                        ORDER BY trigger_ts
                    """, days)

                return [dict(r) for r in rows]

        except Exception as e:
            logger.error(f"Failed to fetch triggers: {e}")
            return []

    async def _label_single_trigger(self, trigger: dict) -> LabeledOutcome:
        """Label a single trigger with forward returns."""
        outcome = LabeledOutcome(
            symbol=trigger['symbol'],
            trigger_ts=trigger['trigger_ts'],
            trigger_type=trigger.get('trigger_type', 'unknown'),
            volume_ratio=float(trigger.get('volume_ratio', 0)),
        )

        try:
            async with self.db_pool.acquire() as conn:
                # Get forward returns from orats_daily_returns
                # The table has return_1d, return_3d, etc. columns
                trigger_date = trigger['trigger_ts'].date()

                row = await conn.fetchrow("""
                    SELECT
                        return_1d, return_3d, return_5d, return_10d
                    FROM orats_daily_returns
                    WHERE ticker = $1
                    AND asof_date = $2
                """, trigger['symbol'], trigger_date)

                if row:
                    outcome.return_1d = float(row['return_1d']) if row['return_1d'] else None
                    outcome.return_3d = float(row['return_3d']) if row['return_3d'] else None
                    outcome.return_5d = float(row['return_5d']) if row['return_5d'] else None
                    outcome.return_10d = float(row['return_10d']) if row['return_10d'] else None

                    # Calculate max return and success labels
                    returns = [
                        (1, outcome.return_1d),
                        (3, outcome.return_3d),
                        (5, outcome.return_5d),
                        (10, outcome.return_10d),
                    ]
                    valid_returns = [(d, r) for d, r in returns if r is not None]

                    if valid_returns:
                        max_day, max_ret = max(valid_returns, key=lambda x: x[1] if x[1] else -999)
                        outcome.max_return = max_ret
                        outcome.max_return_day = max_day

                        # Check 5-day success (using max of 1d, 3d, 5d)
                        returns_5d = [r for d, r in valid_returns if d <= 5 and r is not None]
                        if returns_5d and max(returns_5d) >= self.success_threshold_5d:
                            outcome.success_5pct_5d = True

                        # Check 10-day success
                        if outcome.max_return and outcome.max_return >= self.success_threshold_10d:
                            outcome.success_10pct_10d = True

        except Exception as e:
            logger.debug(f"Could not label {trigger['symbol']}: {e}")

        return outcome

    def get_summary(self, outcomes: list[LabeledOutcome]) -> LabelingSummary:
        """
        Generate summary statistics from labeled outcomes.

        Args:
            outcomes: List of LabeledOutcome

        Returns:
            LabelingSummary with statistics
        """
        summary = LabelingSummary(total_triggers=len(outcomes))

        labeled = [o for o in outcomes if o.return_1d is not None]
        summary.labeled_triggers = len(labeled)
        summary.unlabeled_triggers = len(outcomes) - len(labeled)

        if not labeled:
            return summary

        # Success counts
        summary.success_5pct_5d_count = sum(1 for o in labeled if o.success_5pct_5d)
        summary.success_10pct_10d_count = sum(1 for o in labeled if o.success_10pct_10d)

        # Success rates
        summary.success_5pct_5d_rate = summary.success_5pct_5d_count / len(labeled)
        summary.success_10pct_10d_rate = summary.success_10pct_10d_count / len(labeled)

        # Average returns
        returns_1d = [o.return_1d for o in labeled if o.return_1d is not None]
        returns_5d = [o.return_5d for o in labeled if o.return_5d is not None]
        returns_10d = [o.return_10d for o in labeled if o.return_10d is not None]
        max_returns = [o.max_return for o in labeled if o.max_return is not None]

        if returns_1d:
            summary.avg_return_1d = sum(returns_1d) / len(returns_1d)
        if returns_5d:
            summary.avg_return_5d = sum(returns_5d) / len(returns_5d)
        if returns_10d:
            summary.avg_return_10d = sum(returns_10d) / len(returns_10d)
        if max_returns:
            sorted_max = sorted(max_returns)
            summary.median_max_return = sorted_max[len(sorted_max) // 2]

        return summary

    async def store_labels(self, outcomes: list[LabeledOutcome]) -> int:
        """
        Store labels back to database.

        Updates uoa_triggers_v2 with return columns or stores to separate table.

        Args:
            outcomes: Labeled outcomes to store

        Returns:
            Number of rows updated
        """
        if not self.db_pool:
            return 0

        # For now, we'll create a separate outcomes table
        # In production, could add columns to uoa_triggers_v2
        try:
            async with self.db_pool.acquire() as conn:
                # Create outcomes table if not exists
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS uoa_trigger_outcomes (
                        symbol TEXT NOT NULL,
                        trigger_ts TIMESTAMPTZ NOT NULL,
                        return_1d NUMERIC,
                        return_3d NUMERIC,
                        return_5d NUMERIC,
                        return_10d NUMERIC,
                        max_return NUMERIC,
                        max_return_day INTEGER,
                        success_5pct_5d BOOLEAN,
                        success_10pct_10d BOOLEAN,
                        labeled_at TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (symbol, trigger_ts)
                    )
                """)

                # Batch insert
                rows = [
                    (o.symbol, o.trigger_ts, o.return_1d, o.return_3d, o.return_5d,
                     o.return_10d, o.max_return, o.max_return_day,
                     o.success_5pct_5d, o.success_10pct_10d)
                    for o in outcomes if o.return_1d is not None
                ]

                if rows:
                    await conn.executemany("""
                        INSERT INTO uoa_trigger_outcomes
                        (symbol, trigger_ts, return_1d, return_3d, return_5d,
                         return_10d, max_return, max_return_day,
                         success_5pct_5d, success_10pct_10d)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (symbol, trigger_ts) DO UPDATE SET
                            return_1d = EXCLUDED.return_1d,
                            return_3d = EXCLUDED.return_3d,
                            return_5d = EXCLUDED.return_5d,
                            return_10d = EXCLUDED.return_10d,
                            max_return = EXCLUDED.max_return,
                            max_return_day = EXCLUDED.max_return_day,
                            success_5pct_5d = EXCLUDED.success_5pct_5d,
                            success_10pct_10d = EXCLUDED.success_10pct_10d,
                            labeled_at = NOW()
                    """, rows)

                logger.info(f"Stored {len(rows)} outcome labels")
                return len(rows)

        except Exception as e:
            logger.error(f"Failed to store labels: {e}")
            return 0

    def _generate_mock_outcomes(self) -> list[LabeledOutcome]:
        """Generate mock outcomes for testing."""
        import random

        outcomes = []
        symbols = ["AAPL", "TSLA", "NVDA", "AMD", "MSFT", "GME", "AMC"]

        for i in range(20):
            symbol = random.choice(symbols)
            return_1d = random.gauss(0.02, 0.03)
            return_3d = random.gauss(0.03, 0.05)
            return_5d = random.gauss(0.04, 0.07)
            return_10d = random.gauss(0.05, 0.10)

            max_ret = max(return_1d, return_3d, return_5d, return_10d)

            outcome = LabeledOutcome(
                symbol=symbol,
                trigger_ts=datetime.now() - timedelta(days=random.randint(1, 30)),
                trigger_type="notional",
                volume_ratio=random.uniform(3.0, 8.0),
                return_1d=return_1d,
                return_3d=return_3d,
                return_5d=return_5d,
                return_10d=return_10d,
                max_return=max_ret,
                max_return_day=10 if max_ret == return_10d else 5 if max_ret == return_5d else 3 if max_ret == return_3d else 1,
                success_5pct_5d=max(return_1d, return_3d, return_5d) >= 0.05,
                success_10pct_10d=max_ret >= 0.10,
            )
            outcomes.append(outcome)

        return outcomes


def print_summary(summary: LabelingSummary) -> None:
    """Print summary in readable format."""
    print("\n" + "=" * 60)
    print("OUTCOME LABELING SUMMARY")
    print("=" * 60)

    print(f"\nTriggers:")
    print(f"  Total:     {summary.total_triggers}")
    print(f"  Labeled:   {summary.labeled_triggers}")
    print(f"  Unlabeled: {summary.unlabeled_triggers}")

    print(f"\nSuccess Rates:")
    print(f"  >5% in 5 days:   {summary.success_5pct_5d_count}/{summary.labeled_triggers} ({summary.success_5pct_5d_rate*100:.1f}%)")
    print(f"  >10% in 10 days: {summary.success_10pct_10d_count}/{summary.labeled_triggers} ({summary.success_10pct_10d_rate*100:.1f}%)")

    print(f"\nAverage Returns:")
    print(f"  1-day:  {summary.avg_return_1d*100:+.2f}%")
    print(f"  5-day:  {summary.avg_return_5d*100:+.2f}%")
    print(f"  10-day: {summary.avg_return_10d*100:+.2f}%")
    print(f"  Median Max: {summary.median_max_return*100:+.2f}%")

    # Benchmark comparison (random baseline ~0%)
    print(f"\nSignal vs Random:")
    baseline_rate = 0.10  # ~10% of stocks move >5% in 5 days randomly
    edge = summary.success_5pct_5d_rate - baseline_rate
    print(f"  Baseline (random): ~10%")
    print(f"  Our triggers:      {summary.success_5pct_5d_rate*100:.1f}%")
    print(f"  Edge:              {edge*100:+.1f}%")


async def main():
    parser = argparse.ArgumentParser(description="Label UOA triggers with outcomes")
    parser.add_argument("--days", type=int, default=30, help="Days of triggers to label")
    parser.add_argument("--symbol", type=str, help="Filter by symbol")
    parser.add_argument("--store", action="store_true", help="Store labels to database")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("FL3_V2 Outcome Labeler")
    logger.info("=" * 60)
    logger.info(f"Days: {args.days}")
    if args.symbol:
        logger.info(f"Symbol: {args.symbol}")

    labeler = OutcomeLabeler()

    # Label triggers
    outcomes = await labeler.label_triggers(days=args.days, symbol=args.symbol)

    # Generate summary
    summary = labeler.get_summary(outcomes)
    print_summary(summary)

    # Store if requested
    if args.store and labeler.db_pool:
        stored = await labeler.store_labels(outcomes)
        logger.info(f"Stored {stored} labels")

    # Print sample outcomes
    print("\nSample Outcomes:")
    for o in outcomes[:5]:
        print(f"  {o.symbol} @ {o.trigger_ts.strftime('%Y-%m-%d')}: "
              f"1d={o.return_1d*100 if o.return_1d else 0:+.1f}%, "
              f"5d={o.return_5d*100 if o.return_5d else 0:+.1f}%, "
              f"success={o.success_5pct_5d}")


if __name__ == "__main__":
    asyncio.run(main())
