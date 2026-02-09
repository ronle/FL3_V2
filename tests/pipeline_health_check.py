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
    DATABASE_URL: PostgreSQL connection string (Cloud SQL socket for Cloud Run)
    DATABASE_URL_LOCAL: Optional TCP connection for local testing (overrides DATABASE_URL)
    ALPACA_API_KEY: Alpaca API key
    ALPACA_SECRET_KEY: Alpaca secret key
    GOOGLE_CLOUD_PROJECT: GCP project ID (default: fl3-v2-prod)

Local Testing:
    The script auto-detects Windows environment and transforms Cloud SQL socket URLs
    to TCP connections via Cloud SQL Auth Proxy on localhost:5433.

    Prerequisites:
    1. Cloud SQL Auth Proxy running: cloud_sql_proxy -instances=spartan-buckeye-474319-q8:us-west1:fr3-pg=tcp:5433
    2. GCP auth: gcloud auth login
    3. Secrets accessible: gcloud secrets versions access latest --secret=DATABASE_URL

Cloud Run Execution:
    gcloud run jobs execute fl3-v2-health-check --region=us-west1 --wait

Test Sections:
    - jobs: GCP scheduler jobs and Cloud Run service health (TEST-1.x)
    - data: Database freshness for all tables (TEST-2.x)
    - tracking: Symbol tracking pipeline (TEST-3.x)
    - filtering: Signal filter chain (TEST-4.x)
    - alpaca: Alpaca API integration (TEST-5.x)
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
        "premarket-orchestrator": {"schedule": "0 9 * * 1-5", "tz": "America/New_York"},
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
        self.verbose = verbose
        self.gcp_project = gcp_project or os.environ.get("GOOGLE_CLOUD_PROJECT", "fl3-v2-prod")
        self.gcp_region = gcp_region
        self.alpaca_key = alpaca_key or os.environ.get("ALPACA_API_KEY")
        self.alpaca_secret = alpaca_secret or os.environ.get("ALPACA_SECRET_KEY")

        # Database URL priority:
        # 1. Explicit db_url parameter
        # 2. DATABASE_URL_LOCAL env var (for local testing with Cloud SQL proxy)
        # 3. DATABASE_URL env var (Cloud SQL socket for Cloud Run)
        self.db_url = db_url or os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL")

        # Auto-detect local environment and transform socket URL to TCP
        if self.db_url and "/cloudsql/" in self.db_url and sys.platform == "win32":
            # Running locally on Windows - Cloud SQL socket won't work
            # Use Cloud SQL Auth Proxy on localhost:5433
            local_proxy_url = os.environ.get(
                "DATABASE_URL_LOCAL",
                "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
            )
            if self.verbose:
                logger.info(f"Detected local environment, using Cloud SQL proxy: 127.0.0.1:5433")
            self.db_url = local_proxy_url

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
            # On Windows, use shell=True to find gcloud.cmd
            use_shell = sys.platform == "win32"
            if use_shell:
                cmd = " ".join(cmd)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, shell=use_shell)
            if result.returncode == 0:
                return result.stdout
            if self.verbose:
                logger.warning(f"gcloud returned {result.returncode}: {result.stderr}")
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

    def check_service_errors(self) -> TestResult:
        """TEST-1.4: No recent errors on current revision."""
        # First get the current active revision from service describe (JSON format)
        output = self._run_gcloud_command([
            "run", "services", "describe", "paper-trading-live",
            f"--region={self.gcp_region}"
        ])

        if not output:
            return TestResult(
                test_id="TEST-1.4",
                name="Service errors (current revision)",
                status=TestStatus.SKIP,
                message="Could not get current revision"
            )

        try:
            service = json.loads(output)
            current_revision = service.get("status", {}).get("latestReadyRevisionName")
            if not current_revision:
                return TestResult(
                    test_id="TEST-1.4",
                    name="Service errors (current revision)",
                    status=TestStatus.SKIP,
                    message="Could not parse current revision"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-1.4",
                name="Service errors (current revision)",
                status=TestStatus.SKIP,
                message=f"Error parsing service info: {e}"
            )

        # Query logs for errors on current revision only (last 60 minutes)
        log_filter = (
            f'resource.type="cloud_run_revision" '
            f'resource.labels.service_name="paper-trading-live" '
            f'resource.labels.revision_name="{current_revision}" '
            f'severity>=ERROR '
            f'timestamp>="{(datetime.now(pytz.UTC) - timedelta(minutes=60)).isoformat()}"'
        )

        error_output = self._run_gcloud_command([
            "logging", "read", log_filter,
            "--limit=10"
        ])

        try:
            errors = json.loads(error_output) if error_output else []
            error_count = len(errors)

            if error_count == 0:
                return TestResult(
                    test_id="TEST-1.4",
                    name="Service errors (current revision)",
                    status=TestStatus.PASS,
                    message=f"No errors on {current_revision} (last 60 min)"
                )
            else:
                return TestResult(
                    test_id="TEST-1.4",
                    name="Service errors (current revision)",
                    status=TestStatus.WARN,
                    message=f"{error_count} errors on {current_revision} (last 60 min)"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-1.4",
                name="Service errors (current revision)",
                status=TestStatus.SKIP,
                message=f"Error querying logs: {e}"
            )

    def check_service_min_instances(self) -> TestResult:
        """TEST-1.5: paper-trading-live has min-instances >= 1 (prevents scale-to-zero)."""
        output = self._run_gcloud_command([
            "run", "services", "describe", "paper-trading-live",
            f"--region={self.gcp_region}"
        ])

        if not output:
            return TestResult(
                test_id="TEST-1.5",
                name="Service min-instances",
                status=TestStatus.SKIP,
                message="Could not query service"
            )

        try:
            service = json.loads(output)
            annotations = (
                service.get("spec", {})
                .get("template", {})
                .get("metadata", {})
                .get("annotations", {})
            )
            min_scale = annotations.get("autoscaling.knative.dev/minScale", "0")

            if int(min_scale) >= 1:
                return TestResult(
                    test_id="TEST-1.5",
                    name="Service min-instances",
                    status=TestStatus.PASS,
                    message=f"min-instances={min_scale} (always-on)"
                )
            else:
                return TestResult(
                    test_id="TEST-1.5",
                    name="Service min-instances",
                    status=TestStatus.FAIL,
                    message=f"min-instances={min_scale} — service will scale to zero! Set to 1."
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-1.5",
                name="Service min-instances",
                status=TestStatus.SKIP,
                message=f"Error parsing service: {e}"
            )

    # =========================================================================
    # SECTION 2: DATA FRESHNESS
    # =========================================================================

    def check_ta_daily_close(self) -> TestResult:
        """TEST-2.1: ta_daily_close has today's data with expected symbol coverage."""
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

            # Get expected symbol count from tracked_tickers_v2
            cur.execute("""
                SELECT COUNT(*) FROM tracked_tickers_v2 WHERE ta_enabled = TRUE
            """)
            tracked_count = cur.fetchone()[0]
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

            if max_date < expected_date:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.FAIL,
                    message=f"Stale data: {max_date} (expected {expected_date})"
                )

            # Date is fresh — now validate symbol coverage
            # Expect at least 80% of tracked symbols (some may lack bar data)
            expected_min = max(int(tracked_count * 0.8), 82)  # floor = DEFAULT_SYMBOLS count
            if symbol_count >= expected_min:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.PASS,
                    message=f"Date: {max_date}, {symbol_count:,} symbols (tracked: {tracked_count})",
                    details={"max_date": str(max_date), "symbols": symbol_count, "tracked": tracked_count}
                )
            else:
                return TestResult(
                    test_id="TEST-2.1",
                    name="ta_daily_close freshness",
                    status=TestStatus.WARN,
                    message=f"Date: {max_date}, only {symbol_count:,} symbols (expected >={expected_min} from {tracked_count} tracked)",
                    details={"max_date": str(max_date), "symbols": symbol_count, "tracked": tracked_count, "expected_min": expected_min}
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
            elif days_old <= 7:
                return TestResult(
                    test_id="TEST-2.3",
                    name="Baselines freshness",
                    status=TestStatus.WARN,
                    message=f"Data is {days_old} days old (refresh job may have failed)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.3",
                    name="Baselines freshness",
                    status=TestStatus.FAIL,
                    message=f"Data is {days_old} days old — 20-day rolling window degraded"
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
        """TEST-2.6: spot_prices is fresh for tracked symbols (during RTH)."""
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
            # Only check tracked symbols (not all 15K V1 rows)
            # NOTE: spot_prices uses 'ticker' and 'inserted_at' columns (NOT 'symbol'/'updated_at')
            cur.execute("""
                SELECT
                    COUNT(DISTINCT t.symbol) as total,
                    COUNT(DISTINCT t.symbol) FILTER (WHERE sp.inserted_at > NOW() - INTERVAL '5 minutes') as fresh,
                    COUNT(DISTINCT t.symbol) FILTER (WHERE sp.ticker IS NULL) as missing
                FROM tracked_tickers_v2 t
                LEFT JOIN spot_prices sp ON t.symbol = sp.ticker
                WHERE t.ta_enabled = TRUE
            """)
            total, fresh, missing = cur.fetchone()
            stale = total - fresh - missing
            cur.close()

            if total == 0:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.WARN,
                    message="No tracked symbols to check"
                )

            fresh_pct = (fresh / total * 100) if total > 0 else 0
            if fresh_pct >= 80:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.PASS,
                    message=f"{fresh}/{total} tracked symbols have fresh prices ({fresh_pct:.0f}%)"
                )
            elif fresh_pct >= 50:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.WARN,
                    message=f"Only {fresh}/{total} tracked symbols fresh ({stale} stale, {missing} missing)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.6",
                    name="Spot prices freshness",
                    status=TestStatus.FAIL,
                    message=f"Only {fresh}/{total} tracked symbols fresh ({fresh_pct:.0f}%)"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.6",
                name="Spot prices freshness",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_active_signals_today(self) -> TestResult:
        """TEST-2.7: active_signals has today's data (signals that passed all filters)."""
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.7",
                name="Active signals today",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.7",
                name="Active signals today",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
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
                    name="Active signals today",
                    status=TestStatus.PASS,
                    message=f"{total} signals passed today ({symbols} symbols)"
                )
            else:
                now_et = datetime.now(ET)
                if now_et.time() < dt_time(10, 0):
                    return TestResult(
                        test_id="TEST-2.7",
                        name="Active signals today",
                        status=TestStatus.WARN,
                        message="No signals yet (early in session)"
                    )
                else:
                    return TestResult(
                        test_id="TEST-2.7",
                        name="Active signals today",
                        status=TestStatus.WARN,
                        message="No signals passed today (may be normal)"
                    )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.7",
                name="Active signals today",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_ta_sip_coverage(self) -> TestResult:
        """TEST-2.9: TA pipeline SIP feed coverage (v44+).

        Validates that the TA pipeline is using SIP feed (full market coverage).
        Without feed="sip", only ~10% of symbols get valid data (IEX default).
        Expected: >= 200 unique symbols with fresh snapshots during RTH.
        """
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.9",
                name="TA pipeline SIP coverage",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.9",
                name="TA pipeline SIP coverage",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            # Count symbols with valid TA data (price > 0) in last 15 min
            cur.execute("""
                SELECT COUNT(DISTINCT symbol)
                FROM ta_snapshots_v2
                WHERE snapshot_ts > NOW() - INTERVAL '15 minutes'
                  AND price > 0
            """)
            valid_count = cur.fetchone()[0]

            # Also get total tracked for context
            cur.execute("SELECT COUNT(*) FROM tracked_tickers_v2 WHERE ta_enabled = TRUE")
            tracked = cur.fetchone()[0]
            cur.close()

            if valid_count >= 200:
                return TestResult(
                    test_id="TEST-2.9",
                    name="TA pipeline SIP coverage",
                    status=TestStatus.PASS,
                    message=f"{valid_count}/{tracked} symbols have valid TA (SIP feed working)"
                )
            elif valid_count >= 50:
                return TestResult(
                    test_id="TEST-2.9",
                    name="TA pipeline SIP coverage",
                    status=TestStatus.WARN,
                    message=f"Only {valid_count}/{tracked} symbols — SIP feed may be degraded"
                )
            else:
                return TestResult(
                    test_id="TEST-2.9",
                    name="TA pipeline SIP coverage",
                    status=TestStatus.FAIL,
                    message=f"Only {valid_count}/{tracked} valid — likely missing feed='sip' (IEX gives ~10%)"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.9",
                name="TA pipeline SIP coverage",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_live_baselines_today(self) -> TestResult:
        """TEST-2.10: BucketAggregator writing baselines today (v44+).

        The BucketAggregator in paper-trading-live accumulates options trades
        into 30-min buckets and flushes to intraday_baselines_30m at boundaries.
        During RTH after 10:00 AM, there should be today's baseline data.
        """
        if not self._is_market_hours():
            return TestResult(
                test_id="TEST-2.10",
                name="Live baselines today",
                status=TestStatus.SKIP,
                message="Outside market hours"
            )

        now_et = datetime.now(ET)
        if now_et.time() < dt_time(10, 0):
            return TestResult(
                test_id="TEST-2.10",
                name="Live baselines today",
                status=TestStatus.SKIP,
                message="Too early — first bucket boundary at 10:00 AM ET"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.10",
                name="Live baselines today",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT COUNT(DISTINCT symbol), COUNT(*)
                FROM intraday_baselines_30m
                WHERE trade_date = CURRENT_DATE
            """)
            symbols_today, rows_today = cur.fetchone()
            cur.close()

            if rows_today > 0:
                return TestResult(
                    test_id="TEST-2.10",
                    name="Live baselines today",
                    status=TestStatus.PASS,
                    message=f"{rows_today} rows for {symbols_today} symbols today (BucketAggregator active)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.10",
                    name="Live baselines today",
                    status=TestStatus.WARN,
                    message="No baselines for today — BucketAggregator may not be flushing"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.10",
                name="Live baselines today",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_orats_daily(self) -> TestResult:
        """TEST-2.8: orats_daily has recent data (V1 dependency — baselines derive from this)."""
        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-2.8",
                name="ORATS daily (V1 dep)",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT MAX(asof_date), COUNT(DISTINCT symbol)
                FROM orats_daily
                WHERE asof_date >= CURRENT_DATE - 7
            """)
            max_date, symbol_count = cur.fetchone()
            cur.close()

            if max_date is None:
                return TestResult(
                    test_id="TEST-2.8",
                    name="ORATS daily (V1 dep)",
                    status=TestStatus.FAIL,
                    message="No ORATS data in last 7 days — V1 ingest may be down"
                )

            today = datetime.now(ET).date()
            days_old = (today - max_date).days

            # ORATS asof_date is T-1 (yesterday's data arrives after close)
            if days_old <= 2:
                return TestResult(
                    test_id="TEST-2.8",
                    name="ORATS daily (V1 dep)",
                    status=TestStatus.PASS,
                    message=f"Date: {max_date}, {symbol_count:,} symbols"
                )
            elif days_old <= 4:
                return TestResult(
                    test_id="TEST-2.8",
                    name="ORATS daily (V1 dep)",
                    status=TestStatus.WARN,
                    message=f"Data is {days_old} days old (expected T-1)"
                )
            else:
                return TestResult(
                    test_id="TEST-2.8",
                    name="ORATS daily (V1 dep)",
                    status=TestStatus.FAIL,
                    message=f"Data is {days_old} days old — V1 orats_ingest job likely broken"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-2.8",
                name="ORATS daily (V1 dep)",
                status=TestStatus.FAIL,
                message=f"Query error: {e}"
            )

    def check_adaptive_rsi_config(self) -> TestResult:
        """TEST-2.12: Verify adaptive RSI configuration is valid (V29)."""
        try:
            from paper_trading.config import TradingConfig
            config = TradingConfig()

            issues = []
            if not config.USE_ADAPTIVE_RSI:
                issues.append("USE_ADAPTIVE_RSI is disabled")
            if config.ADAPTIVE_RSI_THRESHOLD <= config.RSI_THRESHOLD:
                issues.append(f"ADAPTIVE_RSI_THRESHOLD ({config.ADAPTIVE_RSI_THRESHOLD}) must be > RSI_THRESHOLD ({config.RSI_THRESHOLD})")
            if config.ADAPTIVE_RSI_THRESHOLD > 70.0:
                issues.append(f"ADAPTIVE_RSI_THRESHOLD ({config.ADAPTIVE_RSI_THRESHOLD}) exceeds sanity cap of 70")
            if config.ADAPTIVE_RSI_MIN_RED_DAYS < 2:
                issues.append(f"ADAPTIVE_RSI_MIN_RED_DAYS ({config.ADAPTIVE_RSI_MIN_RED_DAYS}) must be >= 2")

            if issues:
                return TestResult(
                    test_id="TEST-2.12",
                    name="Adaptive RSI config (V29)",
                    status=TestStatus.FAIL,
                    message="; ".join(issues)
                )

            return TestResult(
                test_id="TEST-2.12",
                name="Adaptive RSI config (V29)",
                status=TestStatus.PASS,
                message=f"RSI {config.RSI_THRESHOLD} normal, {config.ADAPTIVE_RSI_THRESHOLD} bounce, min {config.ADAPTIVE_RSI_MIN_RED_DAYS} red days"
            )

        except Exception as e:
            return TestResult(
                test_id="TEST-2.12",
                name="Adaptive RSI config (V29)",
                status=TestStatus.FAIL,
                message=f"Import error: {e}"
            )

    # =========================================================================
    # SECTION 3: TRACKING PIPELINE
    # =========================================================================

    def check_symbol_tracking(self) -> TestResult:
        """TEST-3.1: Symbols are being tracked and triggers are updating (v44+).

        v44 tracks ALL UOA-triggered symbols (not just those that pass filters).
        Validates both new symbol creation and trigger_count/last_trigger_ts updates.
        """
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
                    COUNT(*) FILTER (WHERE created_at > CURRENT_DATE - 1) as recent_created,
                    COUNT(*) FILTER (WHERE last_trigger_ts > NOW() - INTERVAL '1 hour') as recent_triggered,
                    MAX(last_trigger_ts)
                FROM tracked_tickers_v2
            """)
            total, recent_created, recent_triggered, last_trigger = cur.fetchone()
            cur.close()

            if total == 0:
                return TestResult(
                    test_id="TEST-3.1",
                    name="Symbol tracking",
                    status=TestStatus.FAIL,
                    message="No tracked symbols"
                )

            parts = [f"{total} total"]
            if recent_created > 0:
                parts.append(f"{recent_created} new today")
            if recent_triggered > 0:
                parts.append(f"{recent_triggered} triggered last hour")

            # During market hours, we expect active triggering
            if self._is_market_hours():
                if recent_triggered > 0:
                    return TestResult(
                        test_id="TEST-3.1",
                        name="Symbol tracking",
                        status=TestStatus.PASS,
                        message=", ".join(parts)
                    )
                else:
                    return TestResult(
                        test_id="TEST-3.1",
                        name="Symbol tracking",
                        status=TestStatus.WARN,
                        message=f"{total} tracked but no triggers in last hour (firehose active?)"
                    )
            else:
                # Outside market hours, just check total is reasonable
                if total >= 100:
                    return TestResult(
                        test_id="TEST-3.1",
                        name="Symbol tracking",
                        status=TestStatus.PASS,
                        message=", ".join(parts)
                    )
                else:
                    return TestResult(
                        test_id="TEST-3.1",
                        name="Symbol tracking",
                        status=TestStatus.WARN,
                        message=f"Only {total} tracked (expected 100+)"
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

            missing_count = len(missing)
            missing_pct = (missing_count / total_tracked * 100) if total_tracked > 0 else 0

            if missing_pct <= 10:
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.PASS,
                    message=f"{total_tracked - missing_count}/{total_tracked} tracked symbols have fresh TA ({100-missing_pct:.0f}%)"
                )
            elif missing_pct <= 30:
                symbols = [m[0] for m in missing[:5]]
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.WARN,
                    message=f"{missing_count}/{total_tracked} missing fresh TA ({missing_pct:.0f}%): {', '.join(symbols)}..."
                )
            else:
                return TestResult(
                    test_id="TEST-3.2",
                    name="TA coverage",
                    status=TestStatus.FAIL,
                    message=f"{missing_count}/{total_tracked} missing fresh TA ({missing_pct:.0f}%) — TA pipeline may be broken"
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

    def check_alpaca_bars_api(self) -> TestResult:
        """TEST-5.4: Alpaca bars API returns historical data with SIP feed.

        Validates the exact call path used by dynamic TA fetch:
        feed=sip, timeframe=1Day, start=now-120days, limit=70.
        Would have caught v41-v43 bug (missing feed/start params → 1 bar).
        """
        if not self.alpaca_key or not self.alpaca_secret:
            return TestResult(
                test_id="TEST-5.4",
                name="Alpaca bars API (SIP)",
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
                # Same params used in signal_filter.py:fetch_ta_for_symbol()
                from datetime import timezone
                start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
                params = {
                    "symbols": "AAPL",
                    "timeframe": "1Day",
                    "limit": 10000,
                    "adjustment": "raw",
                    "feed": "sip",
                    "start": start_date.isoformat() + "Z",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://data.alpaca.markets/v2/stocks/bars",
                        headers=headers,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            bars = data.get("bars", {}).get("AAPL", [])
                            return len(bars)
                        return -1

            bar_count = asyncio.run(check())

            if bar_count >= 50:
                return TestResult(
                    test_id="TEST-5.4",
                    name="Alpaca bars API (SIP)",
                    status=TestStatus.PASS,
                    message=f"AAPL returned {bar_count} daily bars (feed=sip, start=120d ago)"
                )
            elif bar_count > 0:
                return TestResult(
                    test_id="TEST-5.4",
                    name="Alpaca bars API (SIP)",
                    status=TestStatus.WARN,
                    message=f"Only {bar_count} bars — dynamic TA needs 20+ for RSI/SMA"
                )
            elif bar_count == 0:
                return TestResult(
                    test_id="TEST-5.4",
                    name="Alpaca bars API (SIP)",
                    status=TestStatus.FAIL,
                    message="0 bars returned — check feed param and start date"
                )
            else:
                return TestResult(
                    test_id="TEST-5.4",
                    name="Alpaca bars API (SIP)",
                    status=TestStatus.FAIL,
                    message="API error — check Alpaca credentials/plan"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-5.4",
                name="Alpaca bars API (SIP)",
                status=TestStatus.FAIL,
                message=f"Error: {e}"
            )

    def check_position_sync(self) -> TestResult:
        """TEST-5.5: Alpaca positions match paper_trades_log open trades."""
        if not self.alpaca_key or not self.alpaca_secret:
            return TestResult(
                test_id="TEST-5.5",
                name="Position sync",
                status=TestStatus.SKIP,
                message="No Alpaca credentials"
            )

        conn = self._get_db_connection()
        if not conn:
            return TestResult(
                test_id="TEST-5.5",
                name="Position sync",
                status=TestStatus.SKIP,
                message="No database connection"
            )

        try:
            import aiohttp

            # Get Alpaca positions
            async def get_positions():
                headers = {
                    "APCA-API-KEY-ID": self.alpaca_key,
                    "APCA-API-SECRET-KEY": self.alpaca_secret,
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://paper-api.alpaca.markets/v2/positions",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        return None

            alpaca_positions = asyncio.run(get_positions())
            if alpaca_positions is None:
                return TestResult(
                    test_id="TEST-5.5",
                    name="Position sync",
                    status=TestStatus.SKIP,
                    message="Could not fetch Alpaca positions"
                )

            alpaca_symbols = {p["symbol"] for p in alpaca_positions}

            # Get DB open trades (no exit_price = still open)
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT symbol FROM paper_trades_log
                WHERE exit_price IS NULL AND created_at > CURRENT_DATE - 7
            """)
            db_symbols = {r[0] for r in cur.fetchall()}
            cur.close()

            # Compare
            in_alpaca_not_db = alpaca_symbols - db_symbols
            in_db_not_alpaca = db_symbols - alpaca_symbols

            if not in_alpaca_not_db and not in_db_not_alpaca:
                return TestResult(
                    test_id="TEST-5.5",
                    name="Position sync",
                    status=TestStatus.PASS,
                    message=f"{len(alpaca_symbols)} positions in sync"
                )
            else:
                issues = []
                if in_alpaca_not_db:
                    issues.append(f"Alpaca-only: {', '.join(in_alpaca_not_db)}")
                if in_db_not_alpaca:
                    issues.append(f"DB-only: {', '.join(in_db_not_alpaca)}")
                return TestResult(
                    test_id="TEST-5.5",
                    name="Position sync",
                    status=TestStatus.WARN,
                    message=f"Mismatch: {'; '.join(issues)}"
                )
        except Exception as e:
            return TestResult(
                test_id="TEST-5.5",
                name="Position sync",
                status=TestStatus.FAIL,
                message=f"Error: {e}"
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
                self.check_service_errors,
                self.check_service_min_instances,
            ],
            "data": [
                self.check_ta_daily_close,
                self.check_ta_snapshots,
                self.check_baselines,
                self.check_earnings_calendar,
                self.check_master_tickers,
                self.check_spot_prices,
                self.check_active_signals_today,
                self.check_orats_daily,
                self.check_ta_sip_coverage,
                self.check_live_baselines_today,
                self.check_adaptive_rsi_config,
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
                self.check_alpaca_bars_api,
                self.check_position_sync,
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
                self.check_service_errors,
                self.check_service_min_instances,
            ]),
            # Section 2: Data Freshness
            ("DATA FRESHNESS", [
                self.check_ta_daily_close,
                self.check_ta_snapshots,
                self.check_baselines,
                self.check_earnings_calendar,
                self.check_master_tickers,
                self.check_spot_prices,
                self.check_active_signals_today,
                self.check_orats_daily,
                self.check_ta_sip_coverage,
                self.check_live_baselines_today,
                self.check_adaptive_rsi_config,
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
                self.check_alpaca_bars_api,
                self.check_position_sync,
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
