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

### Component 0.3: Database Backup — IN PROGRESS
Backup bucket: gs://fl3-v2-backups
Local backup dir: C:\Users\levir\Documents\FL3_V2\backups

#### Backups completed (check sizes):
- option_contracts.sql.gz
- uoa_baselines.sql.gz  
- uoa_hits.sql.gz
- option_oi_daily.sql.gz
- option_greeks_latest.sql.gz
- uoa_hit_components.sql.gz (if completed)
- option_trades_2025_09.sql (uncompressed?)
- option_trades_2025_10.sql (uncompressed?)
- option_trades_2025_11.sql (uncompressed?)
- option_trades_2025_12.sql (uncompressed?)
- option_trades_default.sql (uncompressed?)

## Next Steps for CLI

1. **Verify backups** — check file sizes in backups/ folder
2. **Compress any uncompressed .sql files** with gzip
3. **Upload to GCS**: `gcloud storage cp *.gz gs://fl3-v2-backups/pre-v2-cleanup/`
4. **Verify backup integrity** — spot check one restore
5. **Execute DROP statements** — see docs/v1_dependency_matrix.md
6. **VACUUM FULL** — reclaim disk space

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
