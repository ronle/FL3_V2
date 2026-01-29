"""
Trigger Handler (Component 3.4)

Handles UOA triggers:
1. Fetch option chain snapshot from Polygon
2. Calculate GEX/DEX/Vanna/Charm
3. Store trigger event to database
4. Store GEX metrics to database
5. Add symbol to permanent tracking
6. Emit event for phase detection

Async pipeline for high throughput.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, date
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    """Result of trigger handling."""
    symbol: str
    trigger_ts: datetime
    success: bool
    snapshot_fetched: bool
    gex_calculated: bool
    db_stored: bool
    tracking_updated: bool
    error: Optional[str] = None


class TriggerHandler:
    """
    Handles UOA trigger events asynchronously.

    Pipeline:
    1. Fetch snapshot (Polygon REST)
    2. Calculate GEX metrics
    3. Store to database
    4. Update tracking list
    5. Notify phase detector

    Usage:
        handler = TriggerHandler(snapshot_fetcher, gex_aggregator, db_pool)
        result = await handler.handle(trigger)
    """

    def __init__(
        self,
        snapshot_fetcher=None,
        gex_calculator=None,
        db_pool=None,
        on_trigger_complete: Optional[Callable] = None,
        max_concurrent: int = 5,
    ):
        """
        Initialize handler.

        Args:
            snapshot_fetcher: PolygonSnapshotFetcher instance
            gex_calculator: Function to calculate GEX from snapshot
            db_pool: Database connection pool
            on_trigger_complete: Callback when trigger is fully processed
            max_concurrent: Max concurrent trigger handling
        """
        self.snapshot_fetcher = snapshot_fetcher
        self.gex_calculator = gex_calculator
        self.db_pool = db_pool
        self.on_trigger_complete = on_trigger_complete
        self._semaphore = asyncio.Semaphore(max_concurrent)

        # Metrics
        self._total_handled = 0
        self._successful = 0
        self._failed = 0

    async def handle(self, trigger) -> TriggerResult:
        """
        Handle a single UOA trigger.

        Args:
            trigger: UOATrigger event

        Returns:
            TriggerResult with status
        """
        async with self._semaphore:
            return await self._process_trigger(trigger)

    async def _process_trigger(self, trigger) -> TriggerResult:
        """Internal trigger processing pipeline."""
        self._total_handled += 1
        symbol = trigger.symbol
        result = TriggerResult(
            symbol=symbol,
            trigger_ts=trigger.trigger_ts,
            success=False,
            snapshot_fetched=False,
            gex_calculated=False,
            db_stored=False,
            tracking_updated=False,
        )

        try:
            # Step 1: Fetch option chain snapshot
            snapshot = None
            spot_price = None
            if self.snapshot_fetcher:
                snapshot = await self.snapshot_fetcher.get_option_chain(symbol)
                if snapshot.success:
                    result.snapshot_fetched = True
                    spot_price = snapshot.spot_price
                    logger.debug(f"Snapshot fetched for {symbol}: {len(snapshot.contracts)} contracts")
                else:
                    logger.warning(f"Snapshot failed for {symbol}: {snapshot.error}")

            # Step 2: Calculate GEX metrics
            gex_metrics = None
            if snapshot and snapshot.success and self.gex_calculator:
                gex_metrics = await self._calculate_gex(symbol, spot_price, snapshot.contracts)
                if gex_metrics:
                    result.gex_calculated = True

            # Step 3: Store trigger to database
            if self.db_pool:
                stored = await self._store_trigger(trigger, gex_metrics)
                result.db_stored = stored

            # Step 4: Update tracking list
            if self.db_pool:
                updated = await self._update_tracking(symbol, trigger.trigger_ts)
                result.tracking_updated = updated

            # Step 5: Notify callback
            if self.on_trigger_complete:
                await self._notify_callback(trigger, gex_metrics)

            result.success = True
            self._successful += 1

        except Exception as e:
            result.error = str(e)
            self._failed += 1
            logger.error(f"Trigger handling failed for {symbol}: {e}")

        return result

    async def _calculate_gex(self, symbol: str, spot_price: float, contracts: list) -> Optional[dict]:
        """Calculate GEX metrics from snapshot contracts."""
        if not contracts or not spot_price:
            return None

        try:
            # Import here to avoid circular imports
            from analysis.gex_aggregator import aggregate_gex_metrics, ContractData

            # Convert snapshot contracts to ContractData
            contract_data = []
            for c in contracts:
                if c.open_interest > 0:
                    # Calculate TTE
                    tte = (c.expiry - date.today()).days / 365.0
                    if tte <= 0:
                        continue

                    # Use IV from snapshot or default
                    iv = c.implied_volatility if c.implied_volatility else 0.30

                    contract_data.append(ContractData(
                        strike=c.strike,
                        is_call=c.is_call,
                        open_interest=c.open_interest,
                        iv=iv,
                        tte=tte,
                    ))

            if not contract_data:
                return None

            metrics = aggregate_gex_metrics(symbol, spot_price, contract_data)
            return {
                "symbol": symbol,
                "spot_price": spot_price,
                "net_gex": metrics.net_gex,
                "net_dex": metrics.net_dex,
                "call_wall_strike": metrics.call_wall_strike,
                "put_wall_strike": metrics.put_wall_strike,
                "gamma_flip_level": metrics.gamma_flip_level,
                "net_vex": metrics.net_vex,
                "net_charm": metrics.net_charm,
                "contracts_analyzed": metrics.contracts_analyzed,
            }

        except Exception as e:
            logger.error(f"GEX calculation failed for {symbol}: {e}")
            return None

    async def _store_trigger(self, trigger, gex_metrics: Optional[dict]) -> bool:
        """Store trigger event and GEX metrics to database."""
        if not self.db_pool:
            return False

        try:
            async with self.db_pool.acquire() as conn:
                # Store trigger
                await conn.execute("""
                    INSERT INTO uoa_triggers_v2
                    (symbol, trigger_ts, trigger_type, volume_ratio, notional,
                     baseline_notional, contracts, prints, bucket_start)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                    trigger.symbol,
                    trigger.trigger_ts,
                    trigger.trigger_type,
                    trigger.volume_ratio,
                    trigger.notional,
                    trigger.baseline_notional,
                    trigger.contracts,
                    trigger.prints,
                    trigger.bucket_start,
                )

                # Store GEX metrics if available
                if gex_metrics:
                    await conn.execute("""
                        INSERT INTO gex_metrics_snapshot
                        (symbol, snapshot_ts, spot_price, net_gex, net_dex,
                         call_wall_strike, put_wall_strike, gamma_flip_level,
                         net_vex, net_charm, contracts_analyzed)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                        gex_metrics["symbol"],
                        datetime.now(),
                        gex_metrics["spot_price"],
                        gex_metrics["net_gex"],
                        gex_metrics["net_dex"],
                        gex_metrics["call_wall_strike"],
                        gex_metrics["put_wall_strike"],
                        gex_metrics["gamma_flip_level"],
                        gex_metrics["net_vex"],
                        gex_metrics["net_charm"],
                        gex_metrics["contracts_analyzed"],
                    )

            return True

        except Exception as e:
            logger.error(f"Database store failed: {e}")
            return False

    async def _update_tracking(self, symbol: str, trigger_ts: datetime) -> bool:
        """Add or update symbol in tracking list."""
        if not self.db_pool:
            return False

        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO tracked_tickers_v2
                    (symbol, first_trigger_ts, trigger_count, last_trigger_ts, ta_enabled)
                    VALUES ($1, $2, 1, $2, TRUE)
                    ON CONFLICT (symbol) DO UPDATE SET
                        trigger_count = tracked_tickers_v2.trigger_count + 1,
                        last_trigger_ts = $2,
                        updated_at = NOW()
                """, symbol, trigger_ts)

            return True

        except Exception as e:
            logger.error(f"Tracking update failed: {e}")
            return False

    async def _notify_callback(self, trigger, gex_metrics: Optional[dict]) -> None:
        """Notify completion callback."""
        if self.on_trigger_complete:
            try:
                if asyncio.iscoroutinefunction(self.on_trigger_complete):
                    await self.on_trigger_complete(trigger, gex_metrics)
                else:
                    self.on_trigger_complete(trigger, gex_metrics)
            except Exception as e:
                logger.error(f"Callback failed: {e}")

    async def handle_batch(self, triggers: list) -> list[TriggerResult]:
        """
        Handle multiple triggers concurrently.

        Args:
            triggers: List of UOATrigger events

        Returns:
            List of TriggerResult
        """
        tasks = [self.handle(t) for t in triggers]
        return await asyncio.gather(*tasks)

    def get_metrics(self) -> dict:
        """Get handler metrics."""
        return {
            "total_handled": self._total_handled,
            "successful": self._successful,
            "failed": self._failed,
            "success_rate": self._successful / self._total_handled if self._total_handled > 0 else 0,
        }


if __name__ == "__main__":
    from uoa.detector_v2 import UOATrigger
    from datetime import time as dt_time

    async def test_handler():
        print("Trigger Handler Tests")
        print("=" * 60)

        # Create mock trigger
        trigger = UOATrigger(
            symbol="AAPL",
            trigger_ts=datetime.now(),
            trigger_type="notional",
            volume_ratio=5.0,
            notional=100000,
            baseline_notional=20000,
            contracts=500,
            prints=50,
            bucket_start=dt_time(10, 0),
            confidence=0.8,
        )

        # Test without dependencies (dry run)
        handler = TriggerHandler()
        result = await handler.handle(trigger)

        print(f"\nResult for {result.symbol}:")
        print(f"  Success: {result.success}")
        print(f"  Snapshot: {result.snapshot_fetched}")
        print(f"  GEX: {result.gex_calculated}")
        print(f"  DB: {result.db_stored}")
        print(f"  Tracking: {result.tracking_updated}")
        print(f"  Error: {result.error}")
        print(f"\nMetrics: {handler.get_metrics()}")

    asyncio.run(test_handler())
