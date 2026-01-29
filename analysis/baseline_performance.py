"""
Baseline Performance Report (Component 6.2)

Measures accuracy of ORATS-derived baselines vs actual volume.
Computes:
- Correlation coefficient
- Mean absolute error
- Time-of-day breakdown

Usage:
    python -m analysis.baseline_performance
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, date, time as dt_time
from typing import Optional
import math

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class BucketMetrics:
    """Metrics for a single time bucket."""
    bucket_start: dt_time
    sample_count: int = 0
    correlation: float = 0.0
    mae: float = 0.0  # Mean absolute error
    mape: float = 0.0  # Mean absolute percentage error
    avg_predicted: float = 0.0
    avg_actual: float = 0.0
    bias: float = 0.0  # Systematic over/under prediction


@dataclass
class PerformanceReport:
    """Overall baseline performance report."""
    report_date: datetime
    total_samples: int = 0
    days_analyzed: int = 0

    # Overall metrics
    overall_correlation: float = 0.0
    overall_mae: float = 0.0
    overall_mape: float = 0.0
    overall_bias: float = 0.0

    # Per-bucket metrics
    bucket_metrics: list = None

    # Recommendations
    recommendations: list = None

    def __post_init__(self):
        if self.bucket_metrics is None:
            self.bucket_metrics = []
        if self.recommendations is None:
            self.recommendations = []


class BaselinePerformanceAnalyzer:
    """
    Analyzes baseline prediction accuracy.

    Compares ORATS-derived baselines (predicted volume per bucket)
    against actual observed volume from intraday_baselines_30m.

    Metrics:
    - Correlation: How well predictions track actual patterns
    - MAE: Average absolute error in notional terms
    - MAPE: Average percentage error
    - Bias: Systematic over/under prediction

    Usage:
        analyzer = BaselinePerformanceAnalyzer(db_pool)
        report = await analyzer.generate_report(days=30)
    """

    def __init__(self, db_pool=None):
        """
        Initialize analyzer.

        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool

    async def generate_report(self, days: int = 30) -> PerformanceReport:
        """
        Generate baseline performance report.

        Args:
            days: Number of days to analyze

        Returns:
            PerformanceReport with metrics
        """
        report = PerformanceReport(report_date=datetime.now())

        if not self.db_pool:
            logger.warning("No db_pool, generating mock report")
            return self._generate_mock_report()

        # Fetch predicted vs actual data
        data = await self._fetch_comparison_data(days)

        if not data:
            logger.warning("No data for baseline comparison")
            return report

        report.total_samples = len(data)
        report.days_analyzed = days

        # Calculate overall metrics
        predicted = [d['predicted'] for d in data]
        actual = [d['actual'] for d in data]

        report.overall_correlation = self._correlation(predicted, actual)
        report.overall_mae = self._mae(predicted, actual)
        report.overall_mape = self._mape(predicted, actual)
        report.overall_bias = self._bias(predicted, actual)

        # Calculate per-bucket metrics
        report.bucket_metrics = await self._calculate_bucket_metrics(data)

        # Generate recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    async def _fetch_comparison_data(self, days: int) -> list[dict]:
        """
        Fetch predicted vs actual volume data.

        Joins ORATS daily with intraday_baselines_30m.
        """
        try:
            async with self.db_pool.acquire() as conn:
                # This query compares ORATS-derived predictions with actual bucket data
                rows = await conn.fetch("""
                    WITH predicted AS (
                        SELECT
                            ticker as symbol,
                            asof_date as trade_date,
                            -- Distribute daily volume across buckets using time multipliers
                            total_volume / 13.0 as base_bucket_volume  -- 13 buckets per day
                        FROM orats_daily
                        WHERE asof_date >= CURRENT_DATE - INTERVAL '%s days'
                    ),
                    actual AS (
                        SELECT
                            symbol,
                            trade_date,
                            bucket_start,
                            notional as actual_notional,
                            prints as actual_prints
                        FROM intraday_baselines_30m
                        WHERE trade_date >= CURRENT_DATE - INTERVAL '%s days'
                    )
                    SELECT
                        a.symbol,
                        a.trade_date,
                        a.bucket_start,
                        COALESCE(p.base_bucket_volume, 0) as predicted,
                        a.actual_notional as actual
                    FROM actual a
                    LEFT JOIN predicted p ON p.symbol = a.symbol AND p.trade_date = a.trade_date
                    WHERE p.base_bucket_volume IS NOT NULL
                    ORDER BY a.trade_date, a.bucket_start, a.symbol
                    LIMIT 10000
                """, days, days)

                return [dict(r) for r in rows]

        except Exception as e:
            logger.error(f"Failed to fetch comparison data: {e}")
            return []

    async def _calculate_bucket_metrics(self, data: list[dict]) -> list[BucketMetrics]:
        """Calculate metrics per time bucket."""
        # Group by bucket
        buckets = {}
        for d in data:
            bucket = d.get('bucket_start')
            if bucket not in buckets:
                buckets[bucket] = {'predicted': [], 'actual': []}
            buckets[bucket]['predicted'].append(d['predicted'])
            buckets[bucket]['actual'].append(d['actual'])

        # Calculate metrics per bucket
        metrics = []
        for bucket_start, values in sorted(buckets.items()):
            predicted = values['predicted']
            actual = values['actual']

            m = BucketMetrics(
                bucket_start=bucket_start,
                sample_count=len(predicted),
                correlation=self._correlation(predicted, actual),
                mae=self._mae(predicted, actual),
                mape=self._mape(predicted, actual),
                avg_predicted=sum(predicted) / len(predicted) if predicted else 0,
                avg_actual=sum(actual) / len(actual) if actual else 0,
                bias=self._bias(predicted, actual),
            )
            metrics.append(m)

        return metrics

    def _correlation(self, predicted: list, actual: list) -> float:
        """Calculate Pearson correlation coefficient."""
        if len(predicted) < 2:
            return 0.0

        n = len(predicted)
        sum_x = sum(predicted)
        sum_y = sum(actual)
        sum_xy = sum(p * a for p, a in zip(predicted, actual))
        sum_x2 = sum(p * p for p in predicted)
        sum_y2 = sum(a * a for a in actual)

        numerator = n * sum_xy - sum_x * sum_y
        denominator = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def _mae(self, predicted: list, actual: list) -> float:
        """Calculate Mean Absolute Error."""
        if not predicted:
            return 0.0
        return sum(abs(p - a) for p, a in zip(predicted, actual)) / len(predicted)

    def _mape(self, predicted: list, actual: list) -> float:
        """Calculate Mean Absolute Percentage Error."""
        if not predicted:
            return 0.0

        errors = []
        for p, a in zip(predicted, actual):
            if a != 0:
                errors.append(abs(p - a) / abs(a))

        return sum(errors) / len(errors) if errors else 0.0

    def _bias(self, predicted: list, actual: list) -> float:
        """Calculate systematic bias (mean error)."""
        if not predicted:
            return 0.0
        return sum(p - a for p, a in zip(predicted, actual)) / len(predicted)

    def _generate_recommendations(self, report: PerformanceReport) -> list[str]:
        """Generate recommendations based on report."""
        recommendations = []

        # Check overall correlation
        if report.overall_correlation < 0.5:
            recommendations.append(
                "Low correlation ({:.2f}) - consider using more sophisticated baseline model"
                .format(report.overall_correlation)
            )
        elif report.overall_correlation > 0.8:
            recommendations.append(
                "Strong correlation ({:.2f}) - baseline model is performing well"
                .format(report.overall_correlation)
            )

        # Check bias
        if abs(report.overall_bias) > 1000:
            if report.overall_bias > 0:
                recommendations.append(
                    "Systematic over-prediction by ${:,.0f} - adjust multipliers down"
                    .format(report.overall_bias)
                )
            else:
                recommendations.append(
                    "Systematic under-prediction by ${:,.0f} - adjust multipliers up"
                    .format(abs(report.overall_bias))
                )

        # Check MAPE
        if report.overall_mape > 0.5:
            recommendations.append(
                "High MAPE ({:.0f}%) - predictions have high variance"
                .format(report.overall_mape * 100)
            )

        # Check bucket-specific issues
        if report.bucket_metrics:
            worst_bucket = min(report.bucket_metrics, key=lambda x: x.correlation)
            if worst_bucket.correlation < 0.3:
                recommendations.append(
                    f"Bucket {worst_bucket.bucket_start} has low correlation ({worst_bucket.correlation:.2f}) - tune time multiplier"
                )

        if not recommendations:
            recommendations.append("Baseline performance is within acceptable ranges")

        return recommendations

    def _generate_mock_report(self) -> PerformanceReport:
        """Generate mock report for testing."""
        import random

        report = PerformanceReport(
            report_date=datetime.now(),
            total_samples=5000,
            days_analyzed=30,
            overall_correlation=0.85 + random.uniform(-0.1, 0.1),
            overall_mae=2500 + random.uniform(-500, 500),
            overall_mape=0.25 + random.uniform(-0.05, 0.05),
            overall_bias=500 + random.uniform(-200, 200),
        )

        # Generate bucket metrics
        bucket_times = [
            dt_time(9, 30), dt_time(10, 0), dt_time(10, 30), dt_time(11, 0),
            dt_time(11, 30), dt_time(12, 0), dt_time(12, 30), dt_time(13, 0),
            dt_time(13, 30), dt_time(14, 0), dt_time(14, 30), dt_time(15, 0),
            dt_time(15, 30),
        ]

        for bt in bucket_times:
            # Simulate U-shaped accuracy (worse at midday)
            hour = bt.hour
            if hour in [9, 15]:
                corr = 0.9 + random.uniform(-0.05, 0.05)
            elif hour in [10, 14]:
                corr = 0.85 + random.uniform(-0.05, 0.05)
            else:
                corr = 0.75 + random.uniform(-0.1, 0.1)

            report.bucket_metrics.append(BucketMetrics(
                bucket_start=bt,
                sample_count=random.randint(300, 500),
                correlation=corr,
                mae=2000 + random.uniform(-500, 1000),
                mape=0.20 + random.uniform(-0.05, 0.1),
                avg_predicted=50000 + random.uniform(-10000, 10000),
                avg_actual=48000 + random.uniform(-10000, 10000),
                bias=1000 + random.uniform(-500, 500),
            ))

        report.recommendations = self._generate_recommendations(report)

        return report


def print_report(report: PerformanceReport) -> None:
    """Print report in readable format."""
    print("\n" + "=" * 60)
    print("BASELINE PERFORMANCE REPORT")
    print("=" * 60)
    print(f"Generated: {report.report_date.strftime('%Y-%m-%d %H:%M')}")
    print(f"Days Analyzed: {report.days_analyzed}")
    print(f"Total Samples: {report.total_samples:,}")

    print("\n--- Overall Metrics ---")
    print(f"Correlation:  {report.overall_correlation:.3f}")
    print(f"MAE:          ${report.overall_mae:,.0f}")
    print(f"MAPE:         {report.overall_mape*100:.1f}%")
    print(f"Bias:         ${report.overall_bias:+,.0f}")

    if report.bucket_metrics:
        print("\n--- Per-Bucket Metrics ---")
        print(f"{'Bucket':<10} {'Samples':<10} {'Corr':<8} {'MAE':<12} {'MAPE':<8} {'Bias':<12}")
        print("-" * 60)
        for m in report.bucket_metrics:
            print(f"{m.bucket_start.strftime('%H:%M'):<10} "
                  f"{m.sample_count:<10} "
                  f"{m.correlation:.3f}    "
                  f"${m.mae:>8,.0f}    "
                  f"{m.mape*100:>5.1f}%   "
                  f"${m.bias:>+8,.0f}")

    print("\n--- Recommendations ---")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"{i}. {rec}")


async def main():
    logger.info("Generating Baseline Performance Report...")

    analyzer = BaselinePerformanceAnalyzer()
    report = await analyzer.generate_report(days=30)
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
