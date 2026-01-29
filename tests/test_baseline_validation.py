#!/usr/bin/env python3
"""
Baseline Calculation Validation (Component 0.4.2)

Validates that ORATS-derived baselines correlate with actual volume patterns.
Checkpoint CP1 requires correlation > 0.4.

Results from 2026-01-28 analysis:
- Correlation: 0.961 (PASS - well above 0.4 threshold)
- 6.55% of days exceed 3x baseline (reasonable trigger rate)
"""

import os
import sys
from datetime import datetime

import psycopg2
import numpy as np

# Validation SQL
VALIDATION_QUERY = """
WITH rolling_baselines AS (
    SELECT
        symbol,
        asof_date,
        total_volume as actual_volume,
        AVG(total_volume) OVER (
            PARTITION BY symbol
            ORDER BY asof_date
            ROWS BETWEEN 21 PRECEDING AND 1 PRECEDING
        ) as baseline_20d
    FROM orats_daily
    WHERE asof_date >= %s AND asof_date <= %s
),
accuracy AS (
    SELECT
        symbol,
        asof_date,
        actual_volume,
        baseline_20d,
        CASE
            WHEN baseline_20d > 0 THEN actual_volume::float / baseline_20d
            ELSE NULL
        END as volume_ratio
    FROM rolling_baselines
    WHERE baseline_20d IS NOT NULL AND baseline_20d > 100
)
SELECT
    COUNT(*) as observations,
    AVG(volume_ratio) as avg_ratio,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY volume_ratio) as median_ratio,
    PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY volume_ratio) as p10_ratio,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY volume_ratio) as p90_ratio,
    CORR(actual_volume, baseline_20d) as correlation,
    COUNT(*) FILTER (WHERE volume_ratio > 3) as days_above_3x,
    COUNT(*) FILTER (WHERE volume_ratio > 3) * 100.0 / COUNT(*) as pct_above_3x
FROM accuracy;
"""


def run_validation(start_date: str = "2025-06-01", end_date: str = "2025-12-31"):
    """Run baseline validation analysis."""

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    print(f"\n{'='*60}")
    print("BASELINE VALIDATION REPORT")
    print(f"{'='*60}")
    print(f"Analysis period: {start_date} to {end_date}")
    print(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    cur.execute(VALIDATION_QUERY, (start_date, end_date))
    row = cur.fetchone()

    observations, avg_ratio, median_ratio, p10, p90, correlation, above_3x, pct_3x = row

    print(f"Observations:        {observations:,}")
    print(f"Correlation:         {correlation:.3f}")
    print(f"Average ratio:       {avg_ratio:.2f}")
    print(f"Median ratio:        {median_ratio:.2f}")
    print(f"P10-P90 range:       {p10:.2f} - {p90:.2f}")
    print(f"Days > 3x baseline:  {above_3x:,} ({pct_3x:.2f}%)")

    print(f"\n{'='*60}")
    print("CHECKPOINT CP1 ASSESSMENT")
    print(f"{'='*60}")

    threshold = 0.4
    passed = correlation > threshold

    print(f"Required correlation: > {threshold}")
    print(f"Actual correlation:   {correlation:.3f}")
    print(f"Status:               {'PASS' if passed else 'FAIL'}")
    print(f"{'='*60}\n")

    cur.close()
    conn.close()

    return passed


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-06-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2025-12-31"

    passed = run_validation(start, end)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
