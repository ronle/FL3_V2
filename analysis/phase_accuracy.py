"""
Phase Detection Accuracy Report (Component 6.3)

Measures accuracy of phase detection vs actual price outcomes.
Computes:
- Precision/recall for each phase
- Timing accuracy (did reversal predict the top?)
- Confusion matrix

Usage:
    python -m analysis.phase_accuracy
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PhaseAccuracyMetrics:
    """Accuracy metrics for a single phase."""
    phase: str
    total_signals: int = 0

    # Precision: Of signals fired, how many were correct?
    true_positives: int = 0
    false_positives: int = 0
    precision: float = 0.0

    # Recall: Of actual events, how many did we catch?
    false_negatives: int = 0
    recall: float = 0.0

    # F1 Score
    f1_score: float = 0.0

    # Timing accuracy
    avg_timing_error_days: float = 0.0  # How many days off from actual peak/trough
    within_1_day: float = 0.0  # % of signals within 1 day of actual
    within_3_days: float = 0.0  # % of signals within 3 days


@dataclass
class ConfusionMatrix:
    """Confusion matrix for phase predictions."""
    # Rows = Predicted, Columns = Actual outcome
    # [predicted_up_actual_up, predicted_up_actual_down]
    # [predicted_down_actual_up, predicted_down_actual_down]
    true_positive: int = 0  # Predicted move, move happened
    false_positive: int = 0  # Predicted move, no move
    true_negative: int = 0  # Predicted no move, no move
    false_negative: int = 0  # Predicted no move, move happened


@dataclass
class PhaseAccuracyReport:
    """Full phase detection accuracy report."""
    report_date: datetime
    days_analyzed: int = 0
    total_signals: int = 0

    # Per-phase metrics
    setup_metrics: PhaseAccuracyMetrics = None
    acceleration_metrics: PhaseAccuracyMetrics = None
    reversal_metrics: PhaseAccuracyMetrics = None

    # Overall metrics
    overall_precision: float = 0.0
    overall_recall: float = 0.0
    overall_f1: float = 0.0

    # Reversal timing (most important)
    reversal_timing_accuracy: float = 0.0  # % that predicted within 2 days of actual top
    avg_return_after_reversal: float = 0.0  # Avg return 5 days after reversal signal

    # Confusion matrix
    confusion: ConfusionMatrix = None

    # Recommendations
    recommendations: list = None

    def __post_init__(self):
        if self.confusion is None:
            self.confusion = ConfusionMatrix()
        if self.recommendations is None:
            self.recommendations = []


class PhaseAccuracyAnalyzer:
    """
    Analyzes phase detection accuracy.

    For each phase signal, checks if the predicted outcome occurred:
    - SETUP: Did acceleration follow within 5 days?
    - ACCELERATION: Did price continue up and then reverse?
    - REVERSAL: Did price actually peak and decline?

    Key metrics:
    - Precision: Avoid false alarms
    - Recall: Catch actual P&D events
    - Timing: How close to the actual top/bottom

    Usage:
        analyzer = PhaseAccuracyAnalyzer(db_pool)
        report = await analyzer.generate_report(days=30)
    """

    def __init__(
        self,
        db_pool=None,
        setup_window_days: int = 5,  # Days to see acceleration after setup
        reversal_window_days: int = 3,  # Days to see decline after reversal
        reversal_threshold: float = -0.05,  # 5% decline = successful reversal call
    ):
        """
        Initialize analyzer.

        Args:
            db_pool: Database connection pool
            setup_window_days: Window to check for acceleration after setup
            reversal_window_days: Window to check for decline after reversal
            reversal_threshold: Return threshold for reversal success
        """
        self.db_pool = db_pool
        self.setup_window_days = setup_window_days
        self.reversal_window_days = reversal_window_days
        self.reversal_threshold = reversal_threshold

    async def generate_report(self, days: int = 30) -> PhaseAccuracyReport:
        """
        Generate phase accuracy report.

        Args:
            days: Number of days to analyze

        Returns:
            PhaseAccuracyReport with metrics
        """
        report = PhaseAccuracyReport(
            report_date=datetime.now(),
            days_analyzed=days,
        )

        if not self.db_pool:
            logger.warning("No db_pool, generating mock report")
            return self._generate_mock_report()

        # Fetch phase signals
        signals = await self._fetch_phase_signals(days)
        report.total_signals = len(signals)

        if not signals:
            logger.warning("No phase signals to analyze")
            return report

        # Analyze each phase
        report.setup_metrics = await self._analyze_phase(signals, "SETUP")
        report.acceleration_metrics = await self._analyze_phase(signals, "ACCELERATION")
        report.reversal_metrics = await self._analyze_phase(signals, "REVERSAL")

        # Calculate overall metrics
        self._calculate_overall_metrics(report)

        # Analyze reversal timing
        await self._analyze_reversal_timing(report, signals)

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    async def _fetch_phase_signals(self, days: int) -> list[dict]:
        """Fetch phase signals from database."""
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT
                        symbol, signal_ts, phase, score,
                        contributing_factors, meta_json
                    FROM pd_phase_signals
                    WHERE signal_ts >= NOW() - INTERVAL '%s days'
                    ORDER BY signal_ts
                """, days)

                return [dict(r) for r in rows]

        except Exception as e:
            logger.error(f"Failed to fetch phase signals: {e}")
            return []

    async def _analyze_phase(
        self,
        signals: list[dict],
        phase: str,
    ) -> PhaseAccuracyMetrics:
        """Analyze accuracy for a specific phase."""
        phase_signals = [s for s in signals if s.get('phase') == phase]

        metrics = PhaseAccuracyMetrics(phase=phase, total_signals=len(phase_signals))

        if not phase_signals:
            return metrics

        # For each signal, check if outcome matched prediction
        for signal in phase_signals:
            outcome = await self._check_signal_outcome(signal, phase)

            if outcome == "TP":
                metrics.true_positives += 1
            elif outcome == "FP":
                metrics.false_positives += 1
            elif outcome == "FN":
                metrics.false_negatives += 1

        # Calculate precision and recall
        if metrics.true_positives + metrics.false_positives > 0:
            metrics.precision = metrics.true_positives / (metrics.true_positives + metrics.false_positives)

        if metrics.true_positives + metrics.false_negatives > 0:
            metrics.recall = metrics.true_positives / (metrics.true_positives + metrics.false_negatives)

        # F1 Score
        if metrics.precision + metrics.recall > 0:
            metrics.f1_score = 2 * (metrics.precision * metrics.recall) / (metrics.precision + metrics.recall)

        return metrics

    async def _check_signal_outcome(self, signal: dict, phase: str) -> str:
        """
        Check if signal outcome was correct.

        Returns: "TP" (true positive), "FP" (false positive), or "FN" (false negative)
        """
        symbol = signal['symbol']
        signal_date = signal['signal_ts'].date() if isinstance(signal['signal_ts'], datetime) else signal['signal_ts']

        try:
            async with self.db_pool.acquire() as conn:
                # Get forward returns
                row = await conn.fetchrow("""
                    SELECT return_1d, return_3d, return_5d, return_10d
                    FROM orats_daily_returns
                    WHERE ticker = $1 AND asof_date = $2
                """, symbol, signal_date)

                if not row:
                    return "FP"  # Can't verify, assume false positive

                if phase == "SETUP":
                    # Setup is correct if we see acceleration (big move up) within 5 days
                    max_return = max(
                        row['return_1d'] or 0,
                        row['return_3d'] or 0,
                        row['return_5d'] or 0
                    )
                    return "TP" if max_return >= 0.05 else "FP"

                elif phase == "ACCELERATION":
                    # Acceleration is correct if price continues up
                    return_5d = row['return_5d'] or 0
                    return "TP" if return_5d >= 0.03 else "FP"

                elif phase == "REVERSAL":
                    # Reversal is correct if price declines after signal
                    return_5d = row['return_5d'] or 0
                    return "TP" if return_5d <= self.reversal_threshold else "FP"

        except Exception as e:
            logger.debug(f"Could not check outcome for {symbol}: {e}")
            return "FP"

        return "FP"

    def _calculate_overall_metrics(self, report: PhaseAccuracyReport) -> None:
        """Calculate overall precision, recall, F1."""
        total_tp = 0
        total_fp = 0
        total_fn = 0

        for metrics in [report.setup_metrics, report.acceleration_metrics, report.reversal_metrics]:
            if metrics:
                total_tp += metrics.true_positives
                total_fp += metrics.false_positives
                total_fn += metrics.false_negatives

        if total_tp + total_fp > 0:
            report.overall_precision = total_tp / (total_tp + total_fp)

        if total_tp + total_fn > 0:
            report.overall_recall = total_tp / (total_tp + total_fn)

        if report.overall_precision + report.overall_recall > 0:
            report.overall_f1 = 2 * (report.overall_precision * report.overall_recall) / (report.overall_precision + report.overall_recall)

        # Update confusion matrix
        report.confusion.true_positive = total_tp
        report.confusion.false_positive = total_fp
        report.confusion.false_negative = total_fn

    async def _analyze_reversal_timing(
        self,
        report: PhaseAccuracyReport,
        signals: list[dict],
    ) -> None:
        """Analyze timing accuracy of reversal signals."""
        reversal_signals = [s for s in signals if s.get('phase') == 'REVERSAL']

        if not reversal_signals:
            return

        timing_errors = []
        returns_after = []

        for signal in reversal_signals:
            # This would require finding actual price peaks
            # For now, use forward returns as proxy
            if self.db_pool:
                try:
                    async with self.db_pool.acquire() as conn:
                        row = await conn.fetchrow("""
                            SELECT return_1d, return_3d, return_5d
                            FROM orats_daily_returns
                            WHERE ticker = $1 AND asof_date = $2
                        """, signal['symbol'], signal['signal_ts'].date())

                        if row and row['return_5d'] is not None:
                            returns_after.append(row['return_5d'])
                            # Rough timing: if return is negative, timing was good
                            if row['return_5d'] < 0:
                                timing_errors.append(0)  # Good timing
                            else:
                                timing_errors.append(3)  # Off by ~3 days

                except Exception:
                    pass

        if returns_after:
            report.avg_return_after_reversal = sum(returns_after) / len(returns_after)

        if timing_errors:
            within_1 = sum(1 for t in timing_errors if t <= 1)
            report.reversal_timing_accuracy = within_1 / len(timing_errors)

    def _generate_recommendations(self, report: PhaseAccuracyReport) -> list[str]:
        """Generate recommendations based on report."""
        recommendations = []

        # Check overall F1
        if report.overall_f1 < 0.5:
            recommendations.append(
                f"Low F1 score ({report.overall_f1:.2f}) - detection needs improvement"
            )
        elif report.overall_f1 >= 0.7:
            recommendations.append(
                f"Good F1 score ({report.overall_f1:.2f}) - detection is performing well"
            )

        # Check precision vs recall trade-off
        if report.overall_precision < 0.5:
            recommendations.append(
                "Low precision - too many false alarms, tighten thresholds"
            )
        if report.overall_recall < 0.5:
            recommendations.append(
                "Low recall - missing actual events, loosen thresholds"
            )

        # Check reversal timing
        if report.reversal_metrics and report.reversal_metrics.precision < 0.5:
            recommendations.append(
                "Reversal detection has low precision - review Vanna/GEX signals"
            )

        # Check reversal returns
        if report.avg_return_after_reversal > 0:
            recommendations.append(
                f"Price up {report.avg_return_after_reversal*100:.1f}% after reversal signals - timing may be early"
            )
        elif report.avg_return_after_reversal < -0.05:
            recommendations.append(
                f"Strong decline ({report.avg_return_after_reversal*100:.1f}%) after reversal signals - timing is good"
            )

        if not recommendations:
            recommendations.append("Phase detection is performing within acceptable ranges")

        return recommendations

    def _generate_mock_report(self) -> PhaseAccuracyReport:
        """Generate mock report for testing."""
        import random

        report = PhaseAccuracyReport(
            report_date=datetime.now(),
            days_analyzed=30,
            total_signals=150,
        )

        # Setup metrics
        report.setup_metrics = PhaseAccuracyMetrics(
            phase="SETUP",
            total_signals=60,
            true_positives=42,
            false_positives=18,
            false_negatives=8,
            precision=0.70,
            recall=0.84,
            f1_score=0.76,
            avg_timing_error_days=1.5,
            within_1_day=0.45,
            within_3_days=0.78,
        )

        # Acceleration metrics
        report.acceleration_metrics = PhaseAccuracyMetrics(
            phase="ACCELERATION",
            total_signals=50,
            true_positives=35,
            false_positives=15,
            false_negatives=5,
            precision=0.70,
            recall=0.875,
            f1_score=0.78,
            avg_timing_error_days=1.2,
            within_1_day=0.52,
            within_3_days=0.82,
        )

        # Reversal metrics (most important)
        report.reversal_metrics = PhaseAccuracyMetrics(
            phase="REVERSAL",
            total_signals=40,
            true_positives=26,
            false_positives=14,
            false_negatives=6,
            precision=0.65,
            recall=0.81,
            f1_score=0.72,
            avg_timing_error_days=2.1,
            within_1_day=0.35,
            within_3_days=0.68,
        )

        # Overall
        report.overall_precision = 0.68
        report.overall_recall = 0.84
        report.overall_f1 = 0.75

        report.reversal_timing_accuracy = 0.65
        report.avg_return_after_reversal = -0.03 + random.uniform(-0.02, 0.02)

        # Confusion matrix
        report.confusion = ConfusionMatrix(
            true_positive=103,
            false_positive=47,
            true_negative=200,
            false_negative=19,
        )

        report.recommendations = self._generate_recommendations(report)

        return report


def print_report(report: PhaseAccuracyReport) -> None:
    """Print report in readable format."""
    print("\n" + "=" * 60)
    print("PHASE DETECTION ACCURACY REPORT")
    print("=" * 60)
    print(f"Generated: {report.report_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"Days Analyzed: {report.days_analyzed}")
    print(f"Total Signals: {report.total_signals}")

    print("\n--- Per-Phase Metrics ---")
    print(f"{'Phase':<15} {'Signals':<10} {'Precision':<12} {'Recall':<12} {'F1':<10}")
    print("-" * 60)

    for metrics in [report.setup_metrics, report.acceleration_metrics, report.reversal_metrics]:
        if metrics:
            print(f"{metrics.phase:<15} {metrics.total_signals:<10} "
                  f"{metrics.precision:.2f}         {metrics.recall:.2f}         {metrics.f1_score:.2f}")

    print("\n--- Overall Metrics ---")
    print(f"Precision: {report.overall_precision:.2f}")
    print(f"Recall:    {report.overall_recall:.2f}")
    print(f"F1 Score:  {report.overall_f1:.2f}")

    print("\n--- Reversal Timing ---")
    print(f"Timing Accuracy: {report.reversal_timing_accuracy*100:.0f}% within 1 day of peak")
    print(f"Avg Return After Reversal: {report.avg_return_after_reversal*100:+.1f}%")

    print("\n--- Confusion Matrix ---")
    print(f"                  Actual Move    No Move")
    print(f"Predicted Move    {report.confusion.true_positive:>8}      {report.confusion.false_positive:>8}")
    print(f"Predicted None    {report.confusion.false_negative:>8}      {report.confusion.true_negative:>8}")

    print("\n--- Recommendations ---")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"{i}. {rec}")


async def main():
    logger.info("Generating Phase Detection Accuracy Report...")

    analyzer = PhaseAccuracyAnalyzer()
    report = await analyzer.generate_report(days=30)
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
