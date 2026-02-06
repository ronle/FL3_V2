# V2 Pipeline Health Check - Test Design

## Overview

This document defines a comprehensive health check for the FL3_V2 paper trading pipeline. The test validates all components from GCP job orchestration to trade execution.

---

## Quick Start

### Run Locally (Recommended)
```bash
# Requires Cloud SQL Auth Proxy running on localhost:5433
python -m tests.pipeline_health_check
```

### Run on Cloud
```bash
gcloud run jobs execute fl3-v2-health-check --region=us-west1 --wait
```

---

## Prerequisites for Local Testing

### 1. Cloud SQL Auth Proxy
The script needs database access. Locally, this requires Cloud SQL Auth Proxy:
```bash
cloud_sql_proxy -instances=spartan-buckeye-474319-q8:us-west1:fr3-pg=tcp:5433
```

### 2. GCP Authentication
```bash
gcloud auth login
gcloud config set project fl3-v2-prod
```

### 3. Environment Variables
The script auto-fetches from GCP secrets, or you can set manually:
```bash
export ALPACA_API_KEY=$(gcloud secrets versions access latest --secret=ALPACA_API_KEY)
export ALPACA_SECRET_KEY=$(gcloud secrets versions access latest --secret=ALPACA_SECRET_KEY)
export DATABASE_URL=$(gcloud secrets versions access latest --secret=DATABASE_URL)
```

### How Database Connection Works

| Environment | DATABASE_URL Format | How It Connects |
|-------------|---------------------|-----------------|
| Cloud Run | `?host=/cloudsql/project:region:instance` | Cloud SQL socket |
| Local (Windows) | Auto-transformed | TCP via `127.0.0.1:5433` |
| Local (override) | `DATABASE_URL_LOCAL` | Direct TCP connection |

The script auto-detects Windows and transforms Cloud SQL socket URLs to TCP connections.

---

## 1. GCP Job Inventory & Status

### 1.1 Cloud Run Jobs

| Job | Purpose | Schedule | Writes To |
|-----|---------|----------|-----------|
| `premarket-ta-cache` | Pre-compute daily TA | 6:00 AM ET Mon-Fri | `ta_daily_close` |
| `fl3-v2-ta-pipeline` | 5-min intraday TA | */5 9-16 ET Mon-Fri | `ta_snapshots_v2` |
| `fl3-v2-baseline-refresh` | Update baselines | 4:00 AM ET Mon-Fri | `intraday_baselines_30m` |
| `fetch-earnings-calendar` | Earnings dates | 4:00 AM PT Mon-Fri | `earnings_calendar` |
| `refresh-sector-data` | Sector mapping | 6:00 AM ET Sunday | `master_tickers` |
| `update-spot-prices` | Position prices | */1 6-13 PT Mon-Fri | `spot_prices` |
| `premarket-orchestrator` | Pre-market coordination | 9:00 AM ET Mon-Fri | (orchestration) |

### 1.2 Cloud Run Services

| Service | Purpose | Expected State |
|---------|---------|----------------|
| `paper-trading-live` | Main trading service | Running 24/7 |

### Test Cases - Job Status

```
TEST-1.1: All scheduled jobs are ENABLED
  - Query: gcloud scheduler jobs list
  - Expected: All jobs have STATE: ENABLED

TEST-1.2: All jobs have run within expected window
  - For daily jobs: Last run within 24 hours
  - For 5-min jobs: Last run within 10 minutes (during market hours)
  - For weekly jobs: Last run within 7 days

TEST-1.3: paper-trading-live service is healthy
  - Query: GET /health endpoint
  - Expected: {"status": "running", ...}
```

---

## 2. Database Table Freshness

### 2.1 Table-to-Job Mapping

| Table | Written By | Expected Freshness |
|-------|------------|-------------------|
| `ta_daily_close` | premarket-ta-cache | Today's date (after 6 AM ET) |
| `ta_snapshots_v2` | fl3-v2-ta-pipeline (feed=sip) | Within 10 min (during RTH), 200+ symbols |
| `intraday_baselines_30m` | fl3-v2-baseline-refresh + paper-trading-live (BucketAggregator) | Yesterday or today |
| `earnings_calendar` | fetch-earnings-calendar | Has future dates |
| `master_tickers` | refresh-sector-data | >5000 rows |
| `spot_prices` | update-spot-prices | Within 5 min (during RTH) |
| `active_signals` | paper-trading-live | Today (during RTH) |
| `paper_trades_log` | paper-trading-live | Today (if trades executed) |
| `tracked_tickers_v2` | paper-trading-live (all UOA triggers) | Growing over time |

### Test Cases - Data Freshness

```
TEST-2.1: ta_daily_close has today's data
  - Query: SELECT MAX(trade_date) FROM ta_daily_close
  - Expected: CURRENT_DATE (after 6 AM ET)
  - Failure indicates: premarket-ta-cache job failed

TEST-2.2: ta_snapshots_v2 is fresh (during RTH)
  - Query: SELECT MAX(snapshot_ts) FROM ta_snapshots_v2
  - Expected: Within last 10 minutes
  - Failure indicates: fl3-v2-ta-pipeline job not running

TEST-2.3: intraday_baselines_30m has recent data
  - Query: SELECT MAX(trade_date) FROM intraday_baselines_30m
  - Expected: Yesterday or today
  - Failure indicates: fl3-v2-baseline-refresh job failed

TEST-2.4: earnings_calendar has future data
  - Query: SELECT COUNT(*) FROM earnings_calendar WHERE event_date > CURRENT_DATE
  - Expected: > 100
  - Failure indicates: fetch-earnings-calendar job failed or stale

TEST-2.5: master_tickers has sector data
  - Query: SELECT COUNT(*) FROM master_tickers WHERE sector IS NOT NULL
  - Expected: > 5000
  - Failure indicates: refresh-sector-data job failed

TEST-2.6: spot_prices is fresh (during RTH)
  - Query: SELECT COUNT(*) FROM spot_prices WHERE updated_at > NOW() - INTERVAL '5 minutes'
  - Expected: > 0
  - Failure indicates: update-spot-prices job not running

TEST-2.7: active_signals has today's data (during RTH)
  - Query: SELECT COUNT(*) FROM active_signals WHERE detected_at::date = CURRENT_DATE
  - Expected: > 0 (after 10:00 AM ET, may be 0 early)
  - Failure indicates: paper-trading-live not passing any signals

TEST-2.9: TA pipeline SIP coverage (v44+)
  - Query: SELECT COUNT(DISTINCT symbol) FROM ta_snapshots_v2
           WHERE snapshot_ts > NOW() - INTERVAL '15 minutes' AND price > 0
  - Expected: >= 200 symbols (with feed="sip")
  - Failure indicates: Missing feed="sip" in ta_pipeline_v2.py (IEX gives ~10% coverage)

TEST-2.10: Live baselines today (v44+)
  - Query: SELECT COUNT(*) FROM intraday_baselines_30m WHERE trade_date = CURRENT_DATE
  - Expected: > 0 (after 10:00 AM ET, first 30-min bucket boundary)
  - Failure indicates: BucketAggregator not wired or not flushing in paper-trading-live
```

---

## 3. Pre-Market Flow Validation

### 3.1 Job Execution Order

```
4:00 AM ET: fl3-v2-baseline-refresh → intraday_baselines_30m
4:00 AM PT: fetch-earnings-calendar → earnings_calendar
6:00 AM ET: premarket-ta-cache → ta_daily_close + JSON cache
6:00 AM ET (Sun): refresh-sector-data → master_tickers
9:00 AM ET: premarket-orchestrator (validation)
```

### Test Cases - Pre-Market

```
TEST-3.1: Baseline refresh completed before market open
  - Query: Check job execution log timestamp
  - Expected: Completed before 9:30 AM ET

TEST-3.2: TA cache has sufficient symbols
  - Query: SELECT COUNT(DISTINCT symbol) FROM ta_daily_close WHERE trade_date = CURRENT_DATE
  - Expected: > 4000 symbols

TEST-3.3: Baselines cover active symbols
  - Query: Compare tracked_tickers_v2 symbols with intraday_baselines_30m
  - Expected: > 90% coverage

TEST-3.4: Earnings calendar has upcoming week
  - Query: SELECT COUNT(*) FROM earnings_calendar WHERE event_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
  - Expected: > 0 (typically 50-200)
```

---

## 4. UOA Detection Flow

### 4.1 Flow Diagram (v44+)

```
Polygon WebSocket (T.*)
    ↓
FirehoseClient (firehose/client.py)
    ↓
TradeAggregator (paper_trading/trade_aggregator.py)
    - 60-second rolling window
    - Per-symbol baseline comparison
    ↓
BucketAggregator (v44+) — accumulates 30-min baselines → intraday_baselines_30m
    ↓
UOA Detection (score >= 10, notional >= baseline)
    ↓
ALL triggered symbols → tracked_tickers_v2 (v44+)
    ↓
SignalGenerator.create_signal_async()
    - Fetch TA (intraday or daily)
    - Fetch price (Alpaca)
    ↓
SignalFilter.apply()
    - 8 active filter checks
    ↓
If PASSED → Execute trade via Alpaca → active_signals + paper_trades_log
```

### Test Cases - UOA Detection

```
TEST-4.1: Polygon WebSocket is connected
  - Check: paper-trading-live logs for "Connected to Polygon"
  - Expected: No reconnection loops, stable connection

TEST-4.2: Trades are being processed
  - Check: Service logs for trade processing metrics
  - Expected: trades_processed > 0 and increasing

TEST-4.3: Symbols are being triggered
  - Query: SELECT COUNT(*) FROM tracked_tickers_v2
           WHERE last_trigger_ts > NOW() - INTERVAL '1 hour'
  - Expected: > 0 during RTH (v44 tracks all triggered symbols)

TEST-4.4: BucketAggregator is flushing baselines
  - Query: SELECT COUNT(*) FROM intraday_baselines_30m WHERE trade_date = CURRENT_DATE
  - Expected: > 0 after 10:00 AM ET (v44+ BucketAggregator)

TEST-4.5: Active signals flowing through filters
  - Query: SELECT COUNT(*) FROM active_signals WHERE detected_at::date = CURRENT_DATE
  - Expected: > 0 (after sufficient market activity)
```

---

## 5. Tracked Tickers Pipeline

### 5.1 Flow (v44+)

```
UOA trigger detected (score >= 10)
    ↓
ALL triggered symbols upserted to tracked_tickers_v2 (v44+)
    ├── New symbols: INSERT with trigger_count=1
    └── Existing: UPDATE trigger_count + last_trigger_ts
    ↓
ta_pipeline picks up symbol (next 5-min cycle, feed=sip)
    ↓
ta_snapshots_v2 gets intraday TA (200+ symbols with SIP)
```

**Note (v44 change):** Previously only symbols that passed all 8 filters were tracked. Now ALL UOA-triggered symbols are tracked, growing the universe from ~3-5/day to dozens/day.

### Test Cases - Tracking (v44+)

```
TEST-5.1: Symbols are being tracked with active triggers
  - Query: SELECT COUNT(*),
           COUNT(*) FILTER (WHERE last_trigger_ts > NOW() - INTERVAL '1 hour')
           FROM tracked_tickers_v2
  - Expected: > 0 total, recent triggers during RTH
  - v44 change: ALL triggered symbols tracked, not just filter-passed

TEST-5.2: Trigger count is incrementing
  - Query: SELECT symbol, trigger_count, last_trigger_ts FROM tracked_tickers_v2
           WHERE trigger_count > 1 ORDER BY last_trigger_ts DESC LIMIT 5
  - Expected: Some symbols have multiple triggers with recent timestamps

TEST-5.3: TA pipeline covers tracked symbols (200+ with SIP)
  - Query:
    SELECT COUNT(DISTINCT symbol) FROM ta_snapshots_v2
    WHERE snapshot_ts > NOW() - INTERVAL '15 minutes' AND price > 0
  - Expected: >= 200 symbols (validates SIP feed, not just IEX ~10%)

TEST-5.4: Active signals match tracked symbols
  - Query:
    SELECT a.symbol, t.symbol as tracked
    FROM active_signals a
    LEFT JOIN tracked_tickers_v2 t ON a.symbol = t.symbol
    WHERE a.detected_at > CURRENT_DATE
  - Expected: All active signals are tracked
```

---

## 6. Signal Filtering Validation

### 6.1 Filter Chain (8 active as of v44)

| # | Filter | Source Table | Rejection Keyword | Status |
|---|--------|--------------|-------------------|--------|
| 1 | ETF exclusion | (hardcoded) | "ETF excluded" | Active |
| 2 | Score >= 10 | - | "score X < 10" | Active |
| 3 | RSI < 50 | ta_daily_close / ta_snapshots_v2 | "RSI X >= 50" | Active |
| 4 | Price > SMA20 | ta_* | "not uptrend" | Active |
| 5 | Price > SMA50 | ta_daily_close | "below 50d SMA" | Active |
| 6 | Notional >= baseline | intraday_baselines_30m | "notional < baseline" | Active |
| 7 | Crowded trade | vw_media_daily_features | "high mentions" / "negative sentiment" | Active |
| 8 | Earnings proximity | earnings_calendar | "earnings TODAY/TOMORROW" | Active |

**Note:** Sector concentration and market regime filters exist in code but are not currently called in the live filter chain.

### Test Cases - Filtering

```
TEST-6.1: Filters are passing signals
  - Query: SELECT COUNT(*), COUNT(DISTINCT symbol) FROM active_signals
           WHERE detected_at > CURRENT_DATE - 7
  - Expected: > 0 signals passed within the week

TEST-6.2: RSI filter uses correct source
  - Check logs for: "Refreshed intraday TA cache: X symbols" after 9:35 AM
  - Expected: Intraday cache is being used (200+ symbols with SIP feed)

TEST-6.3: Earnings filter is blocking appropriately
  - Check service logs for: "FILTERED: earnings" rejection messages
  - Expected: Symbols within 2 days of earnings are rejected

TEST-6.4: Sentiment data is available
  - Query: SELECT COUNT(*) FROM vw_media_daily_features WHERE asof_date >= CURRENT_DATE - 2
  - Expected: > 0
  - Failure indicates: V1 media pipeline not running
```

---

## 7. Spot Price Pipeline

### 7.1 Flow

```
update-spot-prices job (every 1 min during RTH)
    ↓
Fetch prices for symbols in:
    - tracked_tickers_v2 (ta_enabled = TRUE)
    - Active positions (from Alpaca)
    ↓
UPDATE spot_prices table
```

### Test Cases - Spot Prices

```
TEST-7.1: Spot prices are updating
  - Query: SELECT COUNT(*) FROM spot_prices WHERE updated_at > NOW() - INTERVAL '5 minutes'
  - Expected: > 0 during RTH

TEST-7.2: Position symbols have prices
  - Query:
    SELECT p.symbol, s.price, s.updated_at
    FROM paper_trades_log p
    LEFT JOIN spot_prices s ON p.symbol = s.symbol
    WHERE p.exit_price IS NULL  -- Open positions
  - Expected: All open positions have recent prices

TEST-7.3: Price staleness alert
  - Query: Symbols with spot price > 5 min old
  - Expected: None during RTH
```

---

## 8. Alpaca Integration

### 8.1 Components

- **API Connection**: ALPACA_API_KEY + ALPACA_SECRET_KEY
- **Account Type**: Paper trading
- **Operations**: Buy orders, position queries, account balance

### Test Cases - Alpaca

```
TEST-8.1: Alpaca credentials are valid
  - Call: GET /v2/account
  - Expected: 200 OK with account details

TEST-8.2: Account has buying power
  - Query: Account buying_power
  - Expected: > $10,000 (configurable threshold)

TEST-8.3: Orders are executing
  - Query:
    SELECT COUNT(*) FROM paper_trades_log
    WHERE created_at::date = CURRENT_DATE AND entry_price IS NOT NULL
  - Expected: > 0 if signals passed

TEST-8.4: Position sync is working
  - Compare: Alpaca positions vs paper_trades_log open positions
  - Expected: Match (no orphaned positions)

TEST-8.5: EOD closer is configured
  - Check: Positions are closed by 3:55 PM ET
  - Query: All trades have exit_time on same day as entry_time
```

---

## 9. Cross-System Dependencies

### 9.1 V1 → V2 Dependencies

| V2 Component | V1 Dependency | Table |
|--------------|---------------|-------|
| Sentiment filter | media-analyze job | vw_media_daily_features |
| Sector lookup | (shared table) | master_tickers |

### Test Cases - V1 Dependencies

```
TEST-9.1: V1 media pipeline is running
  - Query: SELECT MAX(analyzed_at) FROM article_insights
  - Expected: Within 24 hours

TEST-9.2: vw_media_daily_features is populated
  - Query: SELECT COUNT(*) FROM vw_media_daily_features WHERE asof_date >= CURRENT_DATE - 1
  - Expected: > 0
```

---

## 10. Implementation: Health Check Script

### 10.1 Script Structure

```python
# tests/pipeline_health_check.py

class PipelineHealthCheck:
    """
    Comprehensive V2 pipeline health check.

    Usage:
        python -m tests.pipeline_health_check
        python -m tests.pipeline_health_check --section jobs
        python -m tests.pipeline_health_check --verbose
    """

    def __init__(self, db_url: str, alpaca_key: str, alpaca_secret: str):
        self.db_url = db_url
        self.alpaca_key = alpaca_key
        self.alpaca_secret = alpaca_secret
        self.results = []

    # Section 1: GCP Jobs
    def check_job_status(self) -> List[TestResult]: ...
    def check_job_schedules(self) -> List[TestResult]: ...

    # Section 2: Data Freshness
    def check_ta_daily_close(self) -> TestResult: ...
    def check_ta_snapshots(self) -> TestResult: ...
    def check_baselines(self) -> TestResult: ...
    def check_earnings(self) -> TestResult: ...
    def check_spot_prices(self) -> TestResult: ...

    # Section 3: Pre-Market
    def check_premarket_completion(self) -> List[TestResult]: ...

    # Section 4: UOA Detection
    def check_websocket_connection(self) -> TestResult: ...
    def check_signal_processing(self) -> TestResult: ...

    # Section 5: Tracking
    def check_symbol_tracking(self) -> TestResult: ...
    def check_ta_coverage(self) -> TestResult: ...

    # Section 6: Filtering
    def check_filter_distribution(self) -> TestResult: ...

    # Section 7: Spot Prices
    def check_price_freshness(self) -> TestResult: ...

    # Section 8: Alpaca
    def check_alpaca_connection(self) -> TestResult: ...
    def check_account_balance(self) -> TestResult: ...
    def check_position_sync(self) -> TestResult: ...

    # Section 9: V1 Dependencies
    def check_v1_media_pipeline(self) -> TestResult: ...

    def run_all(self) -> HealthReport: ...
    def run_section(self, section: str) -> HealthReport: ...
```

### 10.2 Output Format

```
================================================================================
FL3_V2 PIPELINE HEALTH CHECK
Run at: 2026-02-05 14:30:00 ET
Market Status: OPEN (1h 30m until close)
================================================================================

SECTION 1: GCP JOBS
--------------------------------------------------------------------------------
[PASS] TEST-1.1: All scheduled jobs are ENABLED (7/7)
[PASS] TEST-1.2: Jobs have run within expected window
[PASS] TEST-1.3: paper-trading-live service is healthy

SECTION 2: DATA FRESHNESS
--------------------------------------------------------------------------------
[PASS] TEST-2.1: ta_daily_close has today's data (5,234 symbols)
[PASS] TEST-2.2: ta_snapshots_v2 is fresh (last update 3 min ago)
[PASS] TEST-2.3: intraday_baselines_30m has recent data
[PASS] TEST-2.4: earnings_calendar has future data (1,234 upcoming)
[PASS] TEST-2.5: master_tickers has sector data (5,898 symbols)
[WARN] TEST-2.6: spot_prices has some stale entries (3 symbols > 5 min)
[PASS] TEST-2.7: signal_evaluations has today's data (156 evaluated)

... (continues for all sections)

================================================================================
SUMMARY
================================================================================
Total Tests: 25
Passed: 23 (92%)
Warnings: 1 (4%)
Failed: 1 (4%)

FAILURES:
  - TEST-5.3: TA pipeline missing coverage for 2 tracked symbols

WARNINGS:
  - TEST-2.6: spot_prices has some stale entries

RECOMMENDED ACTIONS:
  1. Check fl3-v2-ta-pipeline job logs for errors
  2. Verify tracked symbols are valid (not delisted)
================================================================================
```

---

## 11. Scheduled Execution

### 11.1 Recommended Schedule

| Check | Frequency | Time |
|-------|-----------|------|
| Full health check | Daily | 9:00 AM ET (pre-market) |
| Data freshness only | Hourly | During RTH |
| Critical checks | Every 5 min | During RTH |

### 11.2 Alerting

```
CRITICAL (immediate alert):
- Polygon WebSocket disconnected > 5 min
- Alpaca API returning errors
- paper-trading-live service down

WARNING (daily summary):
- Data staleness > expected threshold
- Filter rejection rate > 99%
- No trades executed for 2+ hours during RTH

INFO (logged only):
- Job completion times
- Signal statistics
```

---

## 12. Quick Reference: Troubleshooting

| Symptom | Likely Cause | Check |
|---------|--------------|-------|
| No signals evaluated | WebSocket disconnected | Service logs |
| All signals rejected | TA data missing | ta_daily_close freshness |
| Stale intraday TA | ta_pipeline job failed | Job execution logs |
| Only ~11 valid TA symbols (not 200+) | Missing `feed="sip"` in ta_pipeline | TEST-2.9 |
| No baselines for today | BucketAggregator not wired/flushing | TEST-2.10 |
| Tracked symbols not growing | Symbol tracking only on PASS (pre-v44) | TEST-3.1 last_trigger_ts |
| No trades executing | Alpaca API issue | Alpaca connection test |
| Sentiment filter blocking all | V1 media pipeline down | vw_media_daily_features |
| Missing baselines (historical) | Baseline refresh failed | intraday_baselines_30m |
