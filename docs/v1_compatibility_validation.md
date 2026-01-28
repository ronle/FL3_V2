# V1 Compatibility Validation Report
**Date:** 2026-01-28
**Status:** VALIDATED

## V1 Cloud Run Services (9 total)

| Service | Status | Last Deploy | Keep? |
|---------|--------|-------------|-------|
| fr-api | ✅ Active | 2025-11-25 | YES |
| fr-job-executor | ✅ Active | 2025-11-18 | YES |
| fr-media-scheduler | ⚠️ Warning | 2026-01-23 | YES |
| fr-scheduler | ✅ Active | 2025-11-18 | YES |
| fr-ui | ✅ Active | 2025-11-25 | YES |
| fr-workflow-api | ✅ Active | 2025-11-07 | YES |
| fr3-params-flask | ✅ Active | 2025-11-13 | YES |
| fr3-params-ui | ✅ Active | 2025-11-07 | YES |
| fr3-params-ui-beta | ✅ Active | 2025-11-07 | YES |

## V1 Scheduler Jobs - CRITICAL (ENABLED)

| Job | Schedule | Purpose | DB Tables Used |
|-----|----------|---------|----------------|
| orats-daily-ingest-trigger | 22:00 daily | ORATS data | orats_daily ✅ |
| orats-track-top50-trigger | 05:15 weekdays | Top 50 tracking | orats_daily ✅ |
| fr-spot-cron | */5 min RTH | Spot prices | spot_prices ✅ |
| fr-earnings-calendar-daily | 04:00 weekdays | Earnings | earnings_calendar ✅ |
| fr-fmp-news-cron | */10 min | FMP news | news tables |
| fr-api-news-cron | */10 min | News API | news tables |
| fr-media-scheduler-keepalive | */1 min | Keep alive | None |
| fr3-tickers-import-weekly | 04:00 Sunday | Tickers | tickers ✅ |
| fr3-wave-* jobs | Various | Wave trading | wave_*, spot_prices |
| wave-v18-* jobs | Various | V18 strategy | wave_*, spot_prices |
| wave-v19* jobs | Various | V19+ strategy | wave_*, spot_prices |

## Database Table Dependencies

### MUST KEEP (V1 Active Dependencies)
```
orats_daily          - ORATS ingest (2.7M rows, 5.9 GB)
spot_prices          - Spot price tracking
ta_snapshots_*       - TA data
earnings_calendar    - Earnings data
tickers              - Symbol universe
wave_*               - Wave trading tables
```

### SAFE TO DROP (No Active V1 Jobs)
```
option_trades_*      - 33 GB (firehose replaces)
uoa_hit_components   - 7.5 GB (old detection)
option_greeks_latest - 0.7 GB (will recalculate)
option_oi_daily      - (firehose replaces)
option_contracts     - (will rebuild)
```

## Rollback Procedure

1. **If V1 breaks after DB cleanup:**
   - Restore from Cloud Storage backup
   - `gsutil cp gs://fl3-v2-backups/*.sql.gz .`
   - `gunzip *.sql.gz && psql < backup.sql`

2. **If V2 causes issues:**
   - V2 is separate project - just disable V2 services
   - V1 continues unaffected

## Validation Checklist

- [x] V1 services inventoried
- [x] V1 scheduler jobs inventoried  
- [x] Critical jobs identified (ORATS, spot prices)
- [x] Table dependencies mapped
- [x] Drop/Keep list finalized
- [ ] ORATS ingest test (pending)
