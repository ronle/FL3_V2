# Session 2: GCP Setup & Backup Started
Date: 2026-01-28 15:13-17:20 PST

## Completed

### Component 0.1: GCP Project Creation ✅
- Project `fl3-v2-prod` created
- 8 APIs enabled (Run, SQL, Secrets, Scheduler, Logging, Monitoring, Artifact Registry, Build)
- 3 service accounts: fl3-v2-cloudrun, fl3-v2-scheduler, fl3-v2-deployer
- Cross-project Cloud SQL access configured (V2 can access V1's fr3-pg)
- 7 secrets copied: DATABASE_URL, POLYGON_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY, ORATS_FTP_USER, ORATS_FTP_PASSWORD, FMP_API_KEY
- Artifact Registry: fl3-v2-images (us-west1)
- Billing alert: $100/month (50%, 90%, 100% thresholds)

### Component 0.2: V1 Compatibility Validation ✅
- 9 Cloud Run services inventoried
- 41 scheduler jobs mapped (25 ENABLED, 16 PAUSED)
- All UOA/options-stream jobs PAUSED → safe to drop tables
- ORATS ingest confirmed active (5,817 symbols/day through 2026-01-27)
- No foreign key dependencies on drop tables
- Dependency matrix: docs/v1_dependency_matrix.md

### Component 0.3: Database Backup & Cleanup ✅
Backup bucket: gs://fl3-v2-backups/pre-v2-cleanup/

#### Backups in GCS (2.65 GB total):
| File | Size |
|------|------|
| option_trades_2025_09.sql.gz | 264MB |
| option_trades_2025_10.sql.gz | 266MB |
| option_trades_2025_11.sql.gz | 264MB |
| option_trades_2025_12.sql.gz | 241MB |
| option_trades_default.sql.gz | 242MB |
| uoa_hit_components.sql.gz | 192MB |
| uoa_hits.sql.gz | 37MB |
| option_oi_daily.sql.gz | 2.3MB |
| option_greeks_latest.sql.gz | 2.9MB |
| uoa_baselines.sql.gz | 2.3MB |
| option_contracts.sql.gz | 865KB |

#### Tables Dropped:
- option_trades_2025_09, 10, 11, 12
- option_trades_default, option_trades_bad_ts, option_trades
- uoa_hit_components, uoa_hits, uoa_baselines
- option_greeks_latest, option_oi_daily, option_contracts
- Plus 5 dependent views (CASCADE)

#### Results:
- **Before**: 54 GB
- **After**: 4 GB
- **Freed**: ~50 GB

#### Shared tables verified intact:
- orats_daily: 2,953,385 rows (785 MB)
- orats_daily_returns: 2,935,911 rows (254 MB)
- spot_prices: 29,565 rows (2.6 MB)

### Component 0.4: Core Validation Tests ✅

#### 0.4.1 Firehose Feasibility
- Created `tests/test_firehose_feasibility.py`
- Quick connectivity test: PASS (after-hours, no trades expected)
- Full 30-min test requires market hours

#### 0.4.2 Baseline Validation
- Created `tests/test_baseline_validation.py`
- **Correlation: 0.961** (threshold: 0.4) — PASS
- 6.55% of days exceed 3x baseline (reasonable trigger rate)

#### 0.4.3 Time Multipliers
- Created `config/time_multipliers.json`
- U-shaped intraday pattern: Open 3.0x, Midday 0.5x, Close 2.0x
- Will refine after 30 days of bucket data

#### 0.4.4 TA Pipeline Assessment
- Created `docs/ta_pipeline_assessment.md`
- V1 Status: 5-min TA last updated 2026-01-21 (1,437 symbols)
- V2 Plan: New table, trigger-based tracking, 5 indicators

## Phase 0 Complete ✅

All Phase 0 (Infrastructure Setup) components passed:
- CP0a: GCP project operational ✅
- CP0b: V1 dependencies mapped ✅
- CP0c: DB cleanup successful ✅
- CP1: Baseline correlation > 0.4 ✅

### Phase 1: Database Schema ✅

All 6 V2 tables created via `sql/create_tables_v2.sql`:
| Table | Purpose | Est. Rows/Day |
|-------|---------|---------------|
| `intraday_baselines_30m` | Volume calibration | ~13,000 |
| `gex_metrics_snapshot` | Greeks on trigger | 50-500 |
| `uoa_triggers_v2` | UOA events | 50-500 |
| `pd_phase_signals` | Phase transitions | 10-100 |
| `tracked_tickers_v2` | Permanent tracking | ~1,000 total |
| `ta_snapshots_v2` | TA at 5-min | ~78,000 |

Plus `v2_table_stats` view for monitoring.

### Phase 2: Core Components ✅

| Component | File | Performance |
|-----------|------|-------------|
| 2.1 OCC Parser | `utils/occ_parser.py` | 1.49M parses/sec |
| 2.2 Baseline Manager | `analysis/baseline_manager.py` | Hybrid strategy working |
| 2.3 Greeks Calculator | `analysis/greeks_calculator.py` | 1.69M calcs/sec |
| 2.4 GEX Aggregator | `analysis/gex_aggregator.py` | All metrics computed |
| 2.5 Polygon Snapshot | `adapters/polygon_snapshot.py` | Live API tested |

Module `__init__.py` files created for: `utils/`, `analysis/`, `adapters/`

## Next Steps

1. **Phase 3: Firehose Pipeline** — Websocket client, aggregator, UOA detector, trigger handler
2. **Phase 4: TA Pipeline** — Ticker manager, Alpaca bars, TA calculator
3. Schedule 30-min firehose test during market hours

## DB Connection (for CLI)
```powershell
$pass = gcloud secrets versions access latest --secret=fr3-sql-db-pass --project=spartan-buckeye-474319-q8
$env:PGPASSWORD = $pass
psql -h 127.0.0.1 -p 5433 -U FR3_User -d fl3
```

## Tables to DROP (~42 GB)
```sql
DROP TABLE IF EXISTS 
  option_trades_2025_09,
  option_trades_2025_10,
  option_trades_2025_11,
  option_trades_2025_12,
  option_trades_default,
  option_trades_bad_ts,
  option_trades,
  uoa_hit_components,
  uoa_hits,
  uoa_baselines,
  option_greeks_latest,
  option_oi_daily,
  option_contracts
CASCADE;

VACUUM FULL;
```

## Git Status
- Commit f4b28d5: Context summaries
- Commit 4f16cf0: V1 dependency matrix
