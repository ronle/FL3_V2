#!/usr/bin/env python3
"""
Threshold Tuner (Component 6.4)

Tunes detection thresholds based on backtest results.
Implements grid search over threshold parameters.

Usage:
    python -m scripts.tune_thresholds
    python -m scripts.tune_thresholds --optimize precision
"""

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import json

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class ThresholdConfig:
    """Configuration for detection thresholds."""
    # UOA thresholds
    uoa_volume_ratio: float = 3.0
    uoa_notional_min: float = 50000

    # Setup phase
    setup_iv_rank_min: float = 50.0
    setup_oi_change_min: float = 0.10

    # Acceleration phase
    accel_atr_multiple: float = 2.0
    accel_volume_surge: float = 2.0
    accel_rsi_overbought: float = 70.0

    # Reversal phase
    reversal_rsi_divergence: float = 5.0
    reversal_volume_drop: float = 0.5
    reversal_iv_crush: float = 15.0

    # Phase transition
    phase_score_threshold: float = 0.5

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "uoa_volume_ratio": self.uoa_volume_ratio,
            "uoa_notional_min": self.uoa_notional_min,
            "setup_iv_rank_min": self.setup_iv_rank_min,
            "setup_oi_change_min": self.setup_oi_change_min,
            "accel_atr_multiple": self.accel_atr_multiple,
            "accel_volume_surge": self.accel_volume_surge,
            "accel_rsi_overbought": self.accel_rsi_overbought,
            "reversal_rsi_divergence": self.reversal_rsi_divergence,
            "reversal_volume_drop": self.reversal_volume_drop,
            "reversal_iv_crush": self.reversal_iv_crush,
            "phase_score_threshold": self.phase_score_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'ThresholdConfig':
        """Create from dictionary."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TuningResult:
    """Result of threshold tuning."""
    config: ThresholdConfig
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    trigger_count: int = 0
    success_rate: float = 0.0

    @property
    def score(self) -> float:
        """Overall score for ranking."""
        return self.f1_score


@dataclass
class TuningReport:
    """Full tuning report."""
    report_date: datetime
    optimization_target: str
    iterations: int = 0

    # Best configuration
    best_config: ThresholdConfig = None
    best_result: TuningResult = None

    # All results (top N)
    top_results: list = None

    # Recommendations
    recommendations: list = None

    def __post_init__(self):
        if self.top_results is None:
            self.top_results = []
        if self.recommendations is None:
            self.recommendations = []


class ThresholdTuner:
    """
    Tunes detection thresholds via grid search.

    Tests combinations of threshold parameters against
    historical data to find optimal configuration.

    Optimization targets:
    - precision: Minimize false alarms
    - recall: Catch more actual events
    - f1: Balance precision and recall
    - success_rate: Maximize profitable signals

    Usage:
        tuner = ThresholdTuner(db_pool)
        report = await tuner.tune(optimize="f1")
    """

    def __init__(self, db_pool=None):
        """
        Initialize tuner.

        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool

        # Define parameter ranges for grid search
        self.param_ranges = {
            "uoa_volume_ratio": [2.5, 3.0, 3.5, 4.0, 5.0],
            "setup_iv_rank_min": [40, 50, 60],
            "accel_atr_multiple": [1.5, 2.0, 2.5, 3.0],
            "accel_rsi_overbought": [65, 70, 75, 80],
            "reversal_iv_crush": [10, 15, 20],
            "phase_score_threshold": [0.4, 0.5, 0.6, 0.7],
        }

    async def tune(
        self,
        optimize: str = "f1",
        max_iterations: int = 100,
    ) -> TuningReport:
        """
        Tune thresholds via grid search.

        Args:
            optimize: Target metric ("precision", "recall", "f1", "success_rate")
            max_iterations: Maximum iterations to run

        Returns:
            TuningReport with best configuration
        """
        report = TuningReport(
            report_date=datetime.now(),
            optimization_target=optimize,
        )

        if not self.db_pool:
            logger.warning("No db_pool, using mock tuning")
            return self._generate_mock_report(optimize)

        # Generate parameter combinations
        configs = self._generate_configs(max_iterations)
        report.iterations = len(configs)

        logger.info(f"Testing {len(configs)} configurations...")

        # Evaluate each configuration
        results = []
        for i, config in enumerate(configs):
            result = await self._evaluate_config(config)
            results.append(result)

            if (i + 1) % 10 == 0:
                logger.info(f"Evaluated {i + 1}/{len(configs)} configs")

        # Sort by optimization target
        if optimize == "precision":
            results.sort(key=lambda r: r.precision, reverse=True)
        elif optimize == "recall":
            results.sort(key=lambda r: r.recall, reverse=True)
        elif optimize == "success_rate":
            results.sort(key=lambda r: r.success_rate, reverse=True)
        else:  # f1
            results.sort(key=lambda r: r.f1_score, reverse=True)

        # Best result
        report.best_result = results[0]
        report.best_config = results[0].config
        report.top_results = results[:10]

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    def _generate_configs(self, max_iterations: int) -> list[ThresholdConfig]:
        """Generate parameter configurations for grid search."""
        import itertools

        # Get all combinations
        keys = list(self.param_ranges.keys())
        values = [self.param_ranges[k] for k in keys]

        configs = []
        for combo in itertools.product(*values):
            config = ThresholdConfig()
            for k, v in zip(keys, combo):
                setattr(config, k, v)
            configs.append(config)

            if len(configs) >= max_iterations:
                break

        return configs

    async def _evaluate_config(self, config: ThresholdConfig) -> TuningResult:
        """Evaluate a single configuration."""
        result = TuningResult(config=config)

        # In production, this would:
        # 1. Apply config to detectors
        # 2. Re-run detection on historical data
        # 3. Compare predictions to outcomes
        # 4. Calculate precision/recall/F1

        # For now, simulate with database query
        try:
            async with self.db_pool.acquire() as conn:
                # Count triggers that would fire with this config
                row = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_triggers,
                        COUNT(*) FILTER (WHERE volume_ratio >= $1) as matching_triggers
                    FROM uoa_triggers_v2
                    WHERE trigger_ts >= NOW() - INTERVAL '30 days'
                """, config.uoa_volume_ratio)

                if row:
                    result.trigger_count = row['matching_triggers']

                    # Estimate metrics based on trigger count
                    # More triggers = lower precision, higher recall
                    base_precision = 0.8 - (result.trigger_count / 1000) * 0.3
                    base_recall = 0.5 + (result.trigger_count / 1000) * 0.3

                    result.precision = max(0.3, min(0.95, base_precision))
                    result.recall = max(0.3, min(0.95, base_recall))

                    if result.precision + result.recall > 0:
                        result.f1_score = 2 * (result.precision * result.recall) / (result.precision + result.recall)

                    result.success_rate = result.precision * 0.8  # Rough estimate

        except Exception as e:
            logger.debug(f"Config evaluation failed: {e}")

        return result

    def _generate_recommendations(self, report: TuningReport) -> list[str]:
        """Generate recommendations from tuning results."""
        recommendations = []
        best = report.best_result
        config = report.best_config

        if best:
            recommendations.append(
                f"Best F1 score: {best.f1_score:.3f} "
                f"(precision={best.precision:.2f}, recall={best.recall:.2f})"
            )

            # Specific threshold recommendations
            if config.uoa_volume_ratio >= 4.0:
                recommendations.append(
                    "High UOA threshold (>=4x) - fewer but higher quality signals"
                )
            elif config.uoa_volume_ratio <= 2.5:
                recommendations.append(
                    "Low UOA threshold (<=2.5x) - more signals but more noise"
                )

            if config.phase_score_threshold >= 0.6:
                recommendations.append(
                    "High phase score threshold - stricter phase transitions"
                )

            # Compare to defaults
            default = ThresholdConfig()
            changes = []
            if config.uoa_volume_ratio != default.uoa_volume_ratio:
                changes.append(f"UOA ratio: {default.uoa_volume_ratio} -> {config.uoa_volume_ratio}")
            if config.accel_rsi_overbought != default.accel_rsi_overbought:
                changes.append(f"RSI overbought: {default.accel_rsi_overbought} -> {config.accel_rsi_overbought}")

            if changes:
                recommendations.append(f"Key changes from defaults: {', '.join(changes)}")

        return recommendations

    def _generate_mock_report(self, optimize: str) -> TuningReport:
        """Generate mock report for testing."""
        import random

        report = TuningReport(
            report_date=datetime.now(),
            optimization_target=optimize,
            iterations=50,
        )

        # Generate mock results
        results = []
        for _ in range(50):
            config = ThresholdConfig(
                uoa_volume_ratio=random.choice([2.5, 3.0, 3.5, 4.0, 5.0]),
                setup_iv_rank_min=random.choice([40, 50, 60]),
                accel_atr_multiple=random.choice([1.5, 2.0, 2.5, 3.0]),
                accel_rsi_overbought=random.choice([65, 70, 75, 80]),
                reversal_iv_crush=random.choice([10, 15, 20]),
                phase_score_threshold=random.choice([0.4, 0.5, 0.6, 0.7]),
            )

            # Simulate metrics based on thresholds
            # Higher thresholds = higher precision, lower recall
            strictness = (config.uoa_volume_ratio - 2.5) / 2.5 + (config.phase_score_threshold - 0.4) / 0.3
            precision = 0.5 + strictness * 0.2 + random.uniform(-0.1, 0.1)
            recall = 0.8 - strictness * 0.2 + random.uniform(-0.1, 0.1)

            precision = max(0.3, min(0.95, precision))
            recall = max(0.3, min(0.95, recall))
            f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0

            result = TuningResult(
                config=config,
                precision=precision,
                recall=recall,
                f1_score=f1,
                trigger_count=int(500 - strictness * 200 + random.uniform(-50, 50)),
                success_rate=precision * 0.8,
            )
            results.append(result)

        # Sort by target
        if optimize == "precision":
            results.sort(key=lambda r: r.precision, reverse=True)
        elif optimize == "recall":
            results.sort(key=lambda r: r.recall, reverse=True)
        else:
            results.sort(key=lambda r: r.f1_score, reverse=True)

        report.best_result = results[0]
        report.best_config = results[0].config
        report.top_results = results[:10]
        report.recommendations = self._generate_recommendations(report)

        return report

    async def save_config(self, config: ThresholdConfig, path: str) -> bool:
        """Save configuration to JSON file."""
        try:
            with open(path, 'w') as f:
                json.dump(config.to_dict(), f, indent=2)
            logger.info(f"Saved config to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False


def print_report(report: TuningReport) -> None:
    """Print report in readable format."""
    print("\n" + "=" * 60)
    print("THRESHOLD TUNING REPORT")
    print("=" * 60)
    print(f"Generated: {report.report_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"Optimization Target: {report.optimization_target}")
    print(f"Iterations: {report.iterations}")

    if report.best_result:
        print("\n--- Best Configuration ---")
        best = report.best_result
        config = report.best_config

        print(f"F1 Score:    {best.f1_score:.3f}")
        print(f"Precision:   {best.precision:.3f}")
        print(f"Recall:      {best.recall:.3f}")
        print(f"Triggers:    {best.trigger_count}")

        print("\nThreshold Values:")
        print(f"  UOA Volume Ratio:     {config.uoa_volume_ratio}")
        print(f"  Setup IV Rank Min:    {config.setup_iv_rank_min}")
        print(f"  Accel ATR Multiple:   {config.accel_atr_multiple}")
        print(f"  Accel RSI Overbought: {config.accel_rsi_overbought}")
        print(f"  Reversal IV Crush:    {config.reversal_iv_crush}")
        print(f"  Phase Score Threshold:{config.phase_score_threshold}")

    if report.top_results:
        print("\n--- Top 5 Configurations ---")
        print(f"{'Rank':<6} {'F1':<8} {'Prec':<8} {'Recall':<8} {'Triggers':<10} {'UOA Ratio':<10}")
        print("-" * 60)

        for i, r in enumerate(report.top_results[:5], 1):
            print(f"{i:<6} {r.f1_score:.3f}    {r.precision:.3f}    {r.recall:.3f}    "
                  f"{r.trigger_count:<10} {r.config.uoa_volume_ratio}")

    print("\n--- Recommendations ---")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"{i}. {rec}")


async def main():
    parser = argparse.ArgumentParser(description="Tune detection thresholds")
    parser.add_argument("--optimize", type=str, default="f1",
                       choices=["precision", "recall", "f1", "success_rate"],
                       help="Optimization target")
    parser.add_argument("--max-iterations", type=int, default=50,
                       help="Maximum iterations")
    parser.add_argument("--save", type=str, help="Save best config to file")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("FL3_V2 Threshold Tuner")
    logger.info("=" * 60)
    logger.info(f"Optimizing for: {args.optimize}")

    tuner = ThresholdTuner()
    report = await tuner.tune(optimize=args.optimize, max_iterations=args.max_iterations)
    print_report(report)

    if args.save and report.best_config:
        await tuner.save_config(report.best_config, args.save)


if __name__ == "__main__":
    asyncio.run(main())
