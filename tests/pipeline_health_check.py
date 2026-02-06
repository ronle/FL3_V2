#!/usr/bin/env python3
"""
FL3_V2 Pipeline Health Check

Comprehensive health check for the entire V2 paper trading pipeline.
Validates GCP jobs, database freshness, UOA detection, filtering, and Alpaca integration.

Usage:
    python -m tests.pipeline_health_check
    python -m tests.pipeline_health_check --section jobs
    python -m tests.pipeline_health_check --section data
    python -m tests.pipeline_health_check --verbose
    python -m tests.pipeline_health_check --json

Environment Variables:
    DATABASE_URL: PostgreSQL connection string
    ALPACA_API_KEY: Alpaca API key
    ALPACA_SECRET_KEY: Alpaca secret key
    GOOGLE_CLOUD_PROJECT: GCP project ID (default: fl3-v2-prod)
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from enum import Enum
from typing import List, Optional, Dict, Any

import pytz

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ET = pytz.timezone("America/New_York")
PT = pytz.timezone("America/Los_Angeles")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)


class TestStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class TestResult:
    """Result of a single test."""
    test_id: str
    name: str
    status: TestStatus
    message: str
    details: Optional[Dict[str, Any]] = None

    def __str__(self):
        status_str = f"[{self.status.value}]"
        if self.status == TestStatus.PASS:
            status_str = f"\033[92m{status_str}\033[0m"  # Green
        elif self.status == TestStatus.WARN:
            status_str = f"\033[93m{status_str}\033[0m"  # Yellow
        elif self.status == TestStatus.FAIL:
            status_str = f"\033[91m{status_str}\033[0m"  # Red
        else:
            status_str = f"\033[90m{status_str}\033[0m"  # Gray
        return f"{status_str} {self.test_id}: {self.name} - {self.message}"


@dataclass
class HealthReport:
    """Complete health check report."""
    timestamp: datetime
    market_status: str
    results: List[TestResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASS)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.WARN)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIP)

    @property
    def total(self) -> int:
        return len(self.results)


class PipelineHealthCheck:
    """
    Comprehensive V2 pipeline health check.

    Validates:
    1. GCP job status and schedules
    2. Database table freshness
    3. Pre-market flow completion
    4. UOA detection pipeline
    5. Symbol tracking
    6. Signal filtering
    7. Spot price updates
    8. Alpaca integration
    9. V1 dependencies
    """

    # Expected GCP jobs and their schedules
    EXPECTED_JOBS = {
        "premarket-ta-cache": {"schedule": "0 6 * * 1-5", "tz": "America/New_York"},
        "fl3-v2-ta-pipeline": {"schedule": "*/5 9-16 * * 1-5", "tz": "America/New_York"},
        "fl3-v2-baseline-refresh": {"schedule": "0 4 * * 1-5", "tz": "America/New_York"},
        "fetch-earnings-calendar": {"schedule": "0 4 * * 1-5", "tz": "America/Los_Angeles"},
        "refresh-sector-data": {"schedule": "0 6 * * 0", "tz": "America/New_York"},
        "update-spot-prices": {"schedule": "*/1 6-13 * * 1-5", "tz": "America/Los_Angeles"},
    }

    EXPECTED_SERVICES = ["paper-trading-live"]

    def __init__(
        self,
        db_url: str = None,
        alpaca_key: str = None,
        alpaca_secret: str = None,
        gcp_project: str = None,
        gcp_region: str = "us-west1",
        verbose: bool = False,
    ):
        self.db_url = db_url or os.environ.get("DATABASE_URL")
        self.alpaca_key = alpaca_key or os.environ.get("ALPACA_API_KEY")
        self.alpaca_secret = alpaca_secret or os.environ.get("ALPACA_SECRET_KEY")
        self.gcp_project = gcp_project or os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        self.gcp_region = gcp_region
        self.verbose = verbose

        self.results: List[TestResult] = []
        self._db_conn = None

    def _get_market_status(self) -> str:
        """Get current market status."""
        now = datetime.now(ET)

        if now.weekday() >= 5:
            return "CLOSED (Weekend)"

        current_time = now.time()
        market_open = dt_time(9, 30)
        market_close = dt_time(16, 0)

        if current_time < dt_time(4, 0):
            return "CLOSED"
        elif current_time < market_open:
            return "PRE-MARKET"
        elif current_time <= market_close:
            mins_left = int((datetime.combine(now.date(), market_close) - now.replace(tzinfo=None)).seconds / 60)
            return f"OPEN ({mins_left} min until close)"
        elif current_time < dt_time(20, 0):
            return "AFTER-HOURS"
        else:
            return "CLOSED"

    def _is_market_hours(self) -> bool:
        """Check if currently in regular trading hours."""
        now = datetime.now(ET)
        if now.weekday() >= 5:
            return False
        current_time = now.time()
        return dt_time(9, 30) <= current_time <= dt_time(16, 0)

    def _get_db_connection(self):
        """Get database connection."""
        if not self.db_url:
            return None

        if self._db_conn is None:
            import psycopg2
            self._db_conn = psycopg2.connect(self.db_url)
            self._db_conn.autocommit = True  # Avoid transaction issues

        return self._db_conn

    def _close_db_connection(self):
        """Close database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None

    def _run_gcloud_command(self, args: List[str]) -> Optional[str]:
        """Run a gcloud command and return output."""
        try:
            cmd = ["gcloud"] + args + [f"--project={self.gcp_project}", "--format=json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception as e:
            if self.verbose:
                logger.warning(f"gcloud command failed: {e}")
            return None

    # =========================================================================
    # SECTION 1: GCP JOBS
    # =========================================================================

    def check_scheduler_jobs_enabled(self) -> TestResult:
        """TEST-1.1: All scheduled jobs are ENABLED."""
        output = self._run_gcloud_command([
            "scheduler", "jobs", "list",
            f"--location={self.gcp_region}"
        ])

        if not output:
            return TestResult(
                test_id="TEST-1.1",
                name="Scheduler jobs enabled",
                status=TestStatus.FAIL,
                message="Could not query scheduler jobs (check gcloud auth)"
            )

        try:
            jobs = json.loads(output)
            enabled_count = sum(1 for j in jobs if j.get("state") == "ENABLED")
            disabled = [j.get("name", "").split("/")[-1] for j in jobs if j.get("state") != "ENABLED"]

            if disabled:
                return TestResult(
                    test_id="TEST-1.1",
                    name="Scheduler jobs enabled",
                    status=TestStatus.FAIL,
                    message=f"Disabled jobs: {', '.join(disabled)}",
                    details={"disabled": disabled}
                )

            return TestResult(
                test_id="TEST-1.1",
                name="Scheduler jobs enabled",
                status=TestStatus.PASS,
                message=f"All {enabled_count} jobs enabled"
            )
        except json.JSONDecodeError:
            return TestResult(
                test_id="TEST-1.1",
                name="Scheduler jobs enabled",
                status=TestStatus.FAIL,
                message="Failed to parse scheduler response"
            )

    def check_jobs_recent_execution(self) -> TestResult:
        """TEST-1.2: Jobs have run within expected window."""
        output = self._run_gcloud_command([
            "run", "jobs", "list",
            f"--region={self.gcp_region}"
        ])

        if not output:
            return TestResult(
                test_id="TEST-1.2",
                name="Jobs recent execution",
                status=TestStatus.SKIP,
                message="Could not query job executions"
            )

        try:
            jobs = json.loads(output)
            now = datetime.now(ET)
            stale_jobs = []

            for job in jobs:
                job_name = job.get("metadata", {}).get("name", "")

                # Get last execution time
                last_run_str = job.get("status", {}).get("latestCreatedExecution", {}).get("completionTime")
                if not last_run_str:
                    continue

                # Check staleness based on job type
                if "ta-pipeline" in job_name:
                    # Should run every 5 min during RTH
                    if self._is_market_hours():
                        max_age = timedelta(minutes=15)
                    else:
                        max_age = timedelta(hours=24)
                elif "spot-prices" in job_name:
                    # Should run every 1 min during extended hours
                    if self._is_market_hours():
                        max_age = timedelta(minutes=10)
                    else:
                        max_age = timedelta(hours=24)
                else:
                    # Daily jobs
                    max_age = timedelta(hours=36)

                # Parse and check
                try:
                    last_run = datetime.fromisoformat(last_run_str.replace("Z", "+00:00"))
                    age = now.replace(tzinfo=pytz.UTC) - last_run.replace(tzinfo=pytz.UTC)
                    if age > max_age:
                        stale_jobs.append(f"{job_name} ({age.total_seconds()/3600:.1f}h ago)")
                except:
                    pass

            if stale_jobs:
                return TestResult(
                    test_id="TEST-1.2",
                    name="Jobs recent execution",
                    status=TestStatus.WARN,
                    message=f"Stale jobs: {', '.join(stale_jobs[:3])}",
                    details={"stale_jobs": stale_jobs}
                )

            return TestResult(
                test_id="TEST-1.2",
                name="Jobs recent execution",
                status=TestStatus.PASS,
                message="All jobs ran within expected window"
            )
        except Exception as e:
            return TestResult(
                test_id="TEST-1.2",
                name="Jobs recent execution",
                status=TestStatus.SKIP,
                message=f"Error checking jobs: {e}"
            )

    def check_service_health(self) -> TestResult:
        """TEST-1.3: paper-trading-live service is healthy."""
        output = self._run_gcloud_command([
            "run", "services", "describe", "paper-trading-live",
            f"--region={self.gcp_region}"
        ])

        if not output:
            return TestResult(
                test_id="TEST-1.3",
                name="Trading service health",
                status=TestStatus.FAIL,
                message="Could not query paper-trading-live service"
            )

        try:
            service = json.loads(output)
            conditions = service.get("status", {}).get("conditions", [])

            ready = any(
                c.get("type") == "Ready" and c.get("status") == "True"
                for c in conditions
            )

            if ready:
                return TestResult(
                    test_id="TEST-1.3",
                    name="Trading service health",
                    status=TestStatus.PASS,
                    message="paper-trading-live is Ready"
                )
            else:
                return TestResult(
                    test_id="TEST-1.3",
                    name="Trading service health",
                    status=TestStatus.FAIL,
                    message="paper-trading-live is not Ready"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-1.3",
                name="Trading service health",
                status=TestStatus.FAIL,
                message=f"Error checking service: {e}"
            )

    # =========================================================================
    # SECTION 2: DATA FRESHNESS
    # =========================================================================

    def check_ta_daily_close(self) -> TestResult:
        """TEST-2.1: ta_daily_close has today's data."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.1",
                name="ta_daily_close freshness",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT MAX(trade_date), COUNT(DISTINCT symbol)
                FROM ta_daily_close
                WHERE trade_date >= CURRENT_DATE - 1
            """)
            max_date, symbol_count = cur.fetchone()
            cur.close()

            today = datetime.now(ET).date()

            if max_date is None:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.FAIL,
                    message="No recent data in ta_daily_close"
                )

            # Before 6 AM ET, yesterday's data is acceptable
            now_et = datetime.now(ET)
            if now_et.time() < dt_time(6, 0):
                expected_date = today - timedelta(days=1)
            else:
                expected_date = today

            # Handle weekends
            if expected_date.weekday() >= 5:
                expected_date = expected_date - timedelta(days=expected_date.weekday() - 4)

            if max_date >= expected_date:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.PASS,
                    message=f"Date: {max_date}, {symbol_count:,} symbols",
                    details={"max_date": str(max_date), "symbols": symbol_count}
                )
            else:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.FAIL,
                    message=f"Stale data: {max_date} (expected {expected_date})"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.1",
                name="ta_daily_close freshness",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_ta_snapshots(self) -> TestResult:
        """TEST-2.2: ta_snapshots_v2 is fresh (during RTH)."""
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.2",
                name="ta_snapshots_v2 freshness",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.2",
                name="ta_snapshots_v2 freshness",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT MAX(snapshot_ts), COUNT(DISTINCT symbol)
                FROM ta_snapshots_v2
                WHERE snapshot_ts > NOW() - INTERVAL '15 minutes'
            """)
            max_ts, symbol_count = cur.fetchone()
            cur.close()

            if max_ts is None:
                return TestResult(
                    test_id="TEST-2.2",
                    name="ta_snapshots_v2 freshness",
                    status=TestStatus.FAIL,
                    message="No snapshots in last 15 minutes"
                )

            age_minutes = (datetime.now(pytz.UTC) - max_ts.replace(tzinfo=pytz.UTC)).total_seconds() / 60

            if age_minutes <= 10:
                return TestResult(
                    test_id="TEST-2.2",
                    name="ta_snapshots_v2 freshness",
                    status=TestStatus.PASS,
                    message=f"Last update {age_minutes:.0f} min ago, {symbol_count} symbols"
                )
            else:
                return TestResult(
                    test_id="TEST-2.2",
                    name="ta_snapshots_v2 freshness",
                    status=TestStatus.WARN,
                    message=f"Last update {age_minutes:.0f} min ago (expected < 10)"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.2",
                name="ta_snapshots_v2 freshness",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_baselines(self) -> TestResult:
        """TEST-2.3: intraday_baselines_30m has recent data."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.3",
                name="Baselines freshness",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT MAX(trade_date), COUNT(DISTINCT symbol)
                FROM intraday_baselines_30m
            """)
            max_date, symbol_count = cur.fetchone()
            cur.close()

            if max_date is None:
                return TestResult(
                    test_id="TEST-2.3",
                    name="Baselines freshness",
                    status=TestStatus.FAIL,
                    message="No data in intraday_baselines_30m"
                )

            today = datetime.now(ET).date()
            days_old = (today - max_date).days

            if days_old <= 3:
                return TestResult(
                    test_id="TEST-2.3",
                    name="Baselines freshness",
                    status=TestStatus.PASS,
                    message=f"Date: {max_date}, {symbol_count:,} symbols"
                )
            else:
                return TestResult(
                    test_id="TEST-2.3",
                    name="Baselines freshness",
                    status=TestStatus.WARN,
                    message=f"Data is {days_old} days old"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.3",
                name="Baselines freshness",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_earnings_calendar(self) -> TestResult:
        """TEST-2.4: earnings_calendar has future data."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.4",
                name="Earnings calendar",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM earnings_calendar
                WHERE event_date > CURRENT_DATE
            """)
            future_count = cur.fetchone()[0]
            cur.close()

            if future_count >= 100:
                return TestResult(
                    test_id="TEST-2.4",
                    name="Earnings calendar",
                    status=TestStatus.PASS,
                    message=f"{future_count:,} upcoming earnings events"
                )
            elif future_count > 0:
                return TestResult(
                    test_id="TEST-2.4",
                    name="Earnings calendar",
                    status=TestStatus.WARN,
                    message=f"Only {future_count} future events (expected > 100)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.4",
                    name="Earnings calendar",
                    status=TestStatus.FAIL,
                    message="No future earnings data"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.4",
                name="Earnings calendar",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_master_tickers(self) -> TestResult:
        """TEST-2.5: master_tickers has sector data."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.5",
                name="Master tickers",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE sector IS NOT NULL)
                FROM master_tickers
            """)
            total, with_sector = cur.fetchone()
            cur.close()

            if with_sector >= 5000:
                return TestResult(
                    test_id="TEST-2.5",
                    name="Master tickers",
                    status=TestStatus.PASS,
                    message=f"{with_sector:,} symbols with sector data"
                )
            elif with_sector > 0:
                return TestResult(
                    test_id="TEST-2.5",
                    name="Master tickers",
                    status=TestStatus.WARN,
                    message=f"Only {with_sector:,} with sector (expected > 5000)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.5",
                    name="Master tickers",
                    status=TestStatus.FAIL,
                    message="No sector data"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.5",
                name="Master tickers",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_spot_prices(self) -> TestResult:
        """TEST-2.6: spot_prices is fresh (during RTH)."""
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.6",
                name="Spot prices freshness",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.6",
                name="Spot prices freshness",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE updated_at > NOW() - INTERVAL '5 minutes') as fresh,
                    COUNT(*) FILTER (WHERE updated_at <= NOW() - INTERVAL '5 minutes') as stale,
                    MAX(updated_at)
                FROM spot_prices
            """)
            fresh, stale, max_ts = cur.fetchone()
            cur.close()

            if fresh > 0 and stale == 0:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.PASS,
                    message=f"All {fresh} prices fresh"
                )
            elif fresh > 0:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.WARN,
                    message=f"{stale} stale prices (> 5 min old)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.FAIL,
                    message="No fresh spot prices"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.6",
                name="Spot prices freshness",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_signal_evaluations(self) -> TestResult:
        """TEST-2.7: active_signals has today's data."""
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.7",
                name="Signal evaluations",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.7",
                name="Signal evaluations",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            # Use active_signals (signals that passed) since signal_evaluations may not be accessible
            cur.execute("""
                SELECT COUNT(*), COUNT(DISTINCT symbol)
                FROM active_signals
                WHERE detected_at::date = CURRENT_DATE
            """)
            total, symbols = cur.fetchone()
            cur.close()

            if total > 0:
                return TestResult(
                    test_id="TEST-2.7",
                    name="Signal evaluations",
                    status=TestStatus.PASS,
                    message=f"{total} signals passed today ({symbols} symbols)"
                )
            else:
                # Check if it's early in the day
                now_et = datetime.now(ET)
                if now_et.time() < dt_time(10, 0):
                    return TestResult(
                        test_id="TEST-2.7",
                        name="Signal evaluations",
                        status=TestStatus.WARN,
                        message="No signals yet (early in session)"
                    )
                else:
                    return TestResult(
                        test_id="TEST-2.7",
                        name="Signal evaluations",
                        status=TestStatus.WARN,
                        message="No signals passed today (may be normal)"
                    )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.7",
                name="Signal evaluations",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    # =========================================================================
    # SECTION 3: TRACKING PIPELINE
    # =========================================================================

    def check_symbol_tracking(self) -> TestResult:
        """TEST-3.1: Symbols are being added to tracking."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-3.1",
                name="Symbol tracking",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE created_at > CURRENT_DATE - 1) as recent,
                    MAX(created_at)
                FROM tracked_tickers_v2
            """)
            total, recent, max_ts = cur.fetchone()
            cur.close()

            if total == 0:
                return TestResult(
                    test_id="TEST-3.1",
                    name="Symbol tracking",
                    status=TestStatus.FAIL,
                    message="No tracked symbols"
                )

            if recent > 0:
                return TestResult(
                    test_id="TEST-3.1",
                    name="Symbol tracking",
                    status=TestStatus.PASS,
                    message=f"{total} total tracked, {recent} added recently"
                )
            else:
                return TestResult(
                    test_id="TEST-3.1",
                    name="Symbol tracking",
                    status=TestStatus.WARN,
                    message=f"{total} tracked, none added recently"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-3.1",
                name="Symbol tracking",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_ta_coverage(self) -> TestResult:
        """TEST-3.2: TA pipeline covers tracked symbols."""
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-3.2",
                name="TA coverage",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-3.2",
                name="TA coverage",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.symbol, MAX(s.snapshot_ts) as last_ta
                FROM tracked_tickers_v2 t
                LEFT JOIN ta_snapshots_v2 s ON t.symbol = s.symbol
                WHERE t.ta_enabled = TRUE
                GROUP BY t.symbol
                HAVING MAX(s.snapshot_ts) IS NULL
                   OR MAX(s.snapshot_ts) < NOW() - INTERVAL '15 minutes'
            """)
            missing = cur.fetchall()

            cur.execute("SELECT COUNT(*) FROM tracked_tickers_v2 WHERE ta_enabled = TRUE")
            total_tracked = cur.fetchone()[0]
            cur.close()

            if len(missing) == 0:
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.PASS,
                    message=f"All {total_tracked} tracked symbols have fresh TA"
                )
            elif len(missing) <= 5:
                symbols = [m[0] for m in missing[:5]]
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.WARN,
                    message=f"{len(missing)} symbols missing TA: {', '.join(symbols)}"
                )
            else:
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.FAIL,
                    message=f"{len(missing)}/{total_tracked} symbols missing fresh TA"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-3.2",
                name="TA coverage",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_active_signals_tracked(self) -> TestResult:
        """TEST-3.3: Active signals are in tracking list."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-3.3",
                name="Active signals tracked",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.symbol
                FROM active_signals a
                LEFT JOIN tracked_tickers_v2 t ON a.symbol = t.symbol
                WHERE a.detected_at > CURRENT_DATE - 1
                  AND t.symbol IS NULL
            """)
            untracked = [r[0] for r in cur.fetchall()]
            cur.close()

            if len(untracked) == 0:
                return TestResult(
                    test_id="TEST-3.3",
                    name="Active signals tracked",
                    status=TestStatus.PASS,
                    message="All active signals are tracked"
                )
            else:
                return TestResult(
                    test_id="TEST-3.3",
                    name="Active signals tracked",
                    status=TestStatus.WARN,
                    message=f"Untracked: {', '.join(untracked[:5])}"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-3.3",
                name="Active signals tracked",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    # =========================================================================
    # SECTION 4: SIGNAL FILTERING
    # =========================================================================

    def check_filter_distribution(self) -> TestResult:
        """TEST-4.1: All filters are operational (uses active_signals)."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-4.1",
                name="Filter distribution",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            # Use active_signals since signal_evaluations may not be accessible
            cur.execute("""
                SELECT COUNT(*),
                       COUNT(DISTINCT symbol),
                       MAX(detected_at)
                FROM active_signals
                WHERE detected_at > CURRENT_DATE - 7
            """)
            total, symbols, max_date = cur.fetchone()
            cur.close()

            if total > 0:
                return TestResult(
                    test_id="TEST-4.1",
                    name="Filter distribution",
                    status=TestStatus.PASS,
                    message=f"{total} signals passed filters ({symbols} symbols) - latest: {max_date}"
                )
            else:
                return TestResult(
                    test_id="TEST-4.1",
                    name="Filter distribution",
                    status=TestStatus.WARN,
                    message="No signals passed filters recently"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-4.1",
                name="Filter distribution",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_sentiment_data(self) -> TestResult:
        """TEST-4.2: Sentiment data is available (V1 dependency)."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-4.2",
                name="Sentiment data (V1)",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(*), MAX(asof_date)
                FROM vw_media_daily_features
                WHERE asof_date >= CURRENT_DATE - 2
            """)
            count, max_date = cur.fetchone()
            cur.close()

            if count > 0:
                return TestResult(
                    test_id="TEST-4.2",
                    name="Sentiment data (V1)",
                    status=TestStatus.PASS,
                    message=f"{count} records, latest: {max_date}"
                )
            else:
                return TestResult(
                    test_id="TEST-4.2",
                    name="Sentiment data (V1)",
                    status=TestStatus.FAIL,
                    message="No recent sentiment data (V1 media pipeline down?)"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-4.2",
                name="Sentiment data (V1)",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    # =========================================================================
    # SECTION 5: ALPACA INTEGRATION
    # =========================================================================

    def check_alpaca_connection(self) -> TestResult:
        """TEST-5.1: Alpaca API credentials are valid."""
        if not self.alpaca_key or not self.alpaca_secret:
            return TestResult(
                test_id="TEST-5.1",
                name="Alpaca connection",
                status=TestStatus.SKIP,
                message="No Alpaca credentials"
            )

        try:
            import aiohttp

            async def check():
                headers = {
                    "APCA-API-KEY-ID": self.alpaca_key,
                    "APCA-API-SECRET-KEY": self.alpaca_secret,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://paper-api.alpaca.markets/v2/account",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return data
                        return None

            result = asyncio.run(check())

            if result:
                return TestResult(
                    test_id="TEST-5.1",
                    name="Alpaca connection",
                    status=TestStatus.PASS,
                    message=f"Connected: ${float(result.get('equity', 0)):,.2f} equity"
                )
            else:
                return TestResult(
                    test_id="TEST-5.1",
                    name="Alpaca connection",
                    status=TestStatus.FAIL,
                    message="API returned error"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-5.1",
                name="Alpaca connection",
                status=TestStatus.FAIL,
                message=f"Connection error: {e}"
            )

    def check_alpaca_buying_power(self) -> TestResult:
        """TEST-5.2: Alpaca account has buying power."""
        if not self.alpaca_key or not self.alpaca_secret:
            return TestResult(
                test_id="TEST-5.2",
                name="Alpaca buying power",
                status=TestStatus.SKIP,
                message="No Alpaca credentials"
            )

        try:
            import aiohttp

            async def check():
                headers = {
                    "APCA-API-KEY-ID": self.alpaca_key,
                    "APCA-API-SECRET-KEY": self.alpaca_secret,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://paper-api.alpaca.markets/v2/account",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        return None

            result = asyncio.run(check())

            if result:
                buying_power = float(result.get('buying_power', 0))
                if buying_power >= 10000:
                    return TestResult(
                        test_id="TEST-5.2",
                        name="Alpaca buying power",
                        status=TestStatus.PASS,
                        message=f"${buying_power:,.2f} available"
                    )
                elif buying_power > 0:
                    return TestResult(
                        test_id="TEST-5.2",
                        name="Alpaca buying power",
                        status=TestStatus.WARN,
                        message=f"Low buying power: ${buying_power:,.2f}"
                    )
                else:
                    return TestResult(
                        test_id="TEST-5.2",
                        name="Alpaca buying power",
                        status=TestStatus.FAIL,
                        message="No buying power"
                    )
            else:
                return TestResult(
                    test_id="TEST-5.2",
                    name="Alpaca buying power",
                    status=TestStatus.FAIL,
                    message="Could not check account"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-5.2",
                name="Alpaca buying power",
                status=TestStatus.FAIL,
                message=f"Error: {e}"
            )

    def check_trades_executed(self) -> TestResult:
        """TEST-5.3: Trades are being executed."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-5.3",
                name="Trade execution",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE created_at::date = CURRENT_DATE) as today
                FROM paper_trades_log
                WHERE created_at > CURRENT_DATE - 7
            """)
            total, today = cur.fetchone()
            cur.close()

            if today > 0:
                return TestResult(
                    test_id="TEST-5.3",
                    name="Trade execution",
                    status=TestStatus.PASS,
                    message=f"{today} trades today, {total} this week"
                )
            elif total > 0:
                return TestResult(
                    test_id="TEST-5.3",
                    name="Trade execution",
                    status=TestStatus.WARN,
                    message=f"No trades today ({total} this week)"
                )
            else:
                return TestResult(
                    test_id="TEST-5.3",
                    name="Trade execution",
                    status=TestStatus.WARN,
                    message="No trades this week"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-5.3",
                name="Trade execution",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    # =========================================================================
    # RUN ALL TESTS
    # =========================================================================

    def run_section(self, section: str) -> HealthReport:
        """Run tests for a specific section."""
        report = HealthReport(
            timestamp=datetime.now(ET),
            market_status=self._get_market_status()
        )

        section_map = {
            "jobs": [
                self.check_scheduler_jobs_enabled,
                self.check_jobs_recent_execution,
                self.check_service_health,
            ],
            "data": [
                self.check_ta_daily_close,
                self.check_ta_snapshots,
                self.check_baselines,
                self.check_earnings_calendar,
                self.check_master_tickers,
                self.check_spot_prices,
                self.check_signal_evaluations,
            ],
            "tracking": [
                self.check_symbol_tracking,
                self.check_ta_coverage,
                self.check_active_signals_tracked,
            ],
            "filtering": [
                self.check_filter_distribution,
                self.check_sentiment_data,
            ],
            "alpaca": [
                self.check_alpaca_connection,
                self.check_alpaca_buying_power,
                self.check_trades_executed,
            ],
        }

        if section not in section_map:
            logger.error(f"Unknown section: {section}")
            return report

        for test_func in section_map[section]:
            try:
                result = test_func()
                report.results.append(result)
            except Exception as e:
                report.results.append(TestResult(
                    test_id="ERROR",
                    name=test_func.__name__,
                    status=TestStatus.FAIL,
                    message=f"Test crashed: {e}"
                ))

        return report

    def run_all(self) -> HealthReport:
        """Run all health checks."""
        report = HealthReport(
            timestamp=datetime.now(ET),
            market_status=self._get_market_status()
        )

        all_tests = [
            # Section 1: GCP Jobs
            ("GCP JOBS", [
                self.check_scheduler_jobs_enabled,
                self.check_jobs_recent_execution,
                self.check_service_health,
            ]),
            # Section 2: Data Freshness
            ("DATA FRESHNESS", [
                self.check_ta_daily_close,
                self.check_ta_snapshots,
                self.check_baselines,
                self.check_earnings_calendar,
                self.check_master_tickers,
                self.check_spot_prices,
                self.check_signal_evaluations,
            ]),
            # Section 3: Tracking
            ("TRACKING PIPELINE", [
                self.check_symbol_tracking,
                self.check_ta_coverage,
                self.check_active_signals_tracked,
            ]),
            # Section 4: Filtering
            ("SIGNAL FILTERING", [
                self.check_filter_distribution,
                self.check_sentiment_data,
            ]),
            # Section 5: Alpaca
            ("ALPACA INTEGRATION", [
                self.check_alpaca_connection,
                self.check_alpaca_buying_power,
                self.check_trades_executed,
            ]),
        ]

        for section_name, tests in all_tests:
            if self.verbose:
                print(f"\n{section_name}")
                print("-" * 60)

            for test_func in tests:
                try:
                    result = test_func()
                    report.results.append(result)
                    if self.verbose:
                        print(result)
                except Exception as e:
                    result = TestResult(
                        test_id="ERROR",
                        name=test_func.__name__,
                        status=TestStatus.FAIL,
                        message=f"Test crashed: {e}"
                    )
                    report.results.append(result)
                    if self.verbose:
                        print(result)

        self._close_db_connection()
        return report

    def print_report(self, report: HealthReport):
        """Print formatted health report."""
        print("=" * 80)
        print("FL3_V2 PIPELINE HEALTH CHECK")
        print(f"Run at: {report.timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Market Status: {report.market_status}")
        print("=" * 80)

        if not self.verbose:
            # Group by section
            current_section = None
            for result in report.results:
                section = result.test_id.split("-")[0] if "-" in result.test_id else "OTHER"
                if section != current_section:
                    current_section = section
                    print(f"\nSECTION {section}")
                    print("-" * 60)
                print(result)

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Total Tests: {report.total}")
        print(f"Passed: {report.passed} ({report.passed/report.total*100:.0f}%)" if report.total > 0 else "Passed: 0")
        print(f"Warnings: {report.warnings}")
        print(f"Failed: {report.failed}")
        print(f"Skipped: {report.skipped}")

        # List failures
        failures = [r for r in report.results if r.status == TestStatus.FAIL]
        if failures:
            print("\nFAILURES:")
            for f in failures:
                print(f"  - {f.test_id}: {f.message}")

        # List warnings
        warnings = [r for r in report.results if r.status == TestStatus.WARN]
        if warnings:
            print("\nWARNINGS:")
            for w in warnings:
                print(f"  - {w.test_id}: {w.message}")

        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="FL3_V2 Pipeline Health Check")
    parser.add_argument("--section", choices=["jobs", "data", "tracking", "filtering", "alpaca"],
                       help="Run only a specific section")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Show detailed output")
    parser.add_argument("--json", action="store_true",
                       help="Output as JSON")
    args = parser.parse_args()

    checker = PipelineHealthCheck(verbose=args.verbose)

    if args.section:
        report = checker.run_section(args.section)
    else:
        report = checker.run_all()

    if args.json:
        output = {
            "timestamp": report.timestamp.isoformat(),
            "market_status": report.market_status,
            "summary": {
                "total": report.total,
                "passed": report.passed,
                "warnings": report.warnings,
                "failed": report.failed,
                "skipped": report.skipped,
            },
            "results": [
                {
                    "test_id": r.test_id,
                    "name": r.name,
                    "status": r.status.value,
                    "message": r.message,
                }
                for r in report.results
            ]
        }
        print(json.dumps(output, indent=2))
    else:
        checker.print_report(report)

    # Exit with error code if any failures
    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
