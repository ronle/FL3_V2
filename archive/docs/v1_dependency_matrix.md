# V1 Dependency Matrix
Generated: 2026-01-28

## Tables to DROP (Safe - No Active Jobs)

| Table | Size | Reason Safe |
|-------|------|-------------|
| option_trades_2025_09 | 4.4 GB | options-stream-trigger PAUSED |
| option_trades_2025_10 | 5.8 GB | options-stream-trigger PAUSED |
| option_trades_2025_11 | 7.4 GB | options-stream-trigger PAUSED |
| option_trades_2025_12 | 10.2 GB | options-stream-trigger PAUSED |
| option_trades_default | 5.6 GB | options-stream-trigger PAUSED |
| option_trades (parent) | 0 bytes | Partitioned parent, empty |
| option_trades_bad_ts | 23 MB | Bad data partition |
| uoa_hit_components | 7.5 GB | fr-uoa-* jobs all PAUSED |
| uoa_hits | 339 MB | fr-uoa-* jobs all PAUSED |
| uoa_baselines | 30 MB | fr-uoa-baselines-cron PAUSED |
| option_greeks_latest | 662 MB | fr-iv-snapshots-cron PAUSED |
| option_oi_daily | 420 MB | No active consumers |
| option_contracts | 46 MB | No active consumers |
| **TOTAL** | **~42 GB** | |

## Tables to KEEP (Active Jobs Depend On)

| Table | Size | Active Job |
|-------|------|------------|
| orats_daily | 5.9 GB | orats-daily-ingest-trigger (ENABLED) |
| orats_daily_returns | 421 MB | orats-daily-ingest-trigger |
| articles | 1.9 GB | fr-api-news-cron, fr-fmp-news-cron (ENABLED) |
| article_entities | 1.7 GB | News pipeline |
| article_insights | 102 MB | News pipeline |
| article_sentiment | 33 MB | News pipeline |
| catalyst_calendar | 1.3 GB | fr-earnings-calendar-daily (ENABLED) |
| earnings_calendar | 26 MB | fr-earnings-calendar-daily |
| master_tickers | 13 MB | fr3-tickers-import-weekly (ENABLED) |
| wave_* tables | ~100 MB | wave-v18/v19 jobs (ENABLED) |
| spot_prices | ~50 MB | fr-spot-cron (ENABLED) |
| ta_snapshots_* | ~50 MB | ta-alpaca-trigger (PAUSED but may re-enable) |
| scheduler_job_runs | 128 MB | Job tracking |
| dq_* tables | ~300 MB | Data quality (useful) |

## Critical Jobs (MUST NOT BREAK)

### ORATS Pipeline (V2 Baseline Source)
- `orats-daily-ingest-trigger` — Daily at 10pm PT
- `orats-track-top50-trigger` — Daily at 5:15am PT
- Tables: orats_daily, orats_daily_returns

### Wave Trading (Active Production)
- `fr3-wave-*` jobs — Multiple schedules
- `wave-v18-*` jobs — Every 10 min during RTH
- `wave-v19-*` jobs — Every 10 min during RTH
- `wave-v19plus-*` jobs — Every 10 min during RTH

### News/Sentiment
- `fr-api-news-cron` — Every 10 min
- `fr-fmp-news-cron` — Every 10 min
- `fr-earnings-calendar-daily` — Daily at 4am PT

## Rollback Procedure

If V1 breaks after cleanup:
1. Check which job failed in Cloud Scheduler logs
2. Identify missing table from error message
3. Restore from Cloud Storage backup:
   ```bash
   gsutil cp gs://fl3-backups/pre-v2-cleanup/<table>.sql.gz .
   gunzip <table>.sql.gz
   psql $DATABASE_URL < <table>.sql
   ```
4. Re-enable any paused jobs if needed
