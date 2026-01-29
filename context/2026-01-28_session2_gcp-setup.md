# Session 2: GCP Setup & V1 Validation
**Date:** 2026-01-28 15:13 - 16:01 PST
**Market:** RTH

## Summary
Completed Phase 0 Components 0.1 and 0.2. Started Component 0.3 (DB Backup).

## Completed

### Component 0.1: GCP Project Creation ✅
- Created `fl3-v2-prod` project
- Enabled 8 core APIs (Cloud Run, SQL, Secrets, Scheduler, etc.)
- Created 3 service accounts with proper IAM roles:
  - fl3-v2-cloudrun (SQL client, secrets accessor, log writer)
  - fl3-v2-scheduler (run invoker)
  - fl3-v2-deployer (run admin, artifact writer, build editor)
- Granted cross-project Cloud SQL access to V1 database
- Copied 7 secrets: DATABASE_URL, POLYGON_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY, ORATS_FTP_USER, ORATS_FTP_PASSWORD, FMP_API_KEY
- Created Artifact Registry: fl3-v2-images (Docker, us-west1)
- Set $100/month budget with 50/90/100% alerts

### Component 0.2: V1 Compatibility Validation ✅
- Inventoried 9 Cloud Run services (all active)
- Inventoried 43 Cloud Scheduler jobs (16 enabled, 27 paused)
- Identified critical ENABLED jobs:
  - orats-daily-ingest-trigger (22:00 daily)
  - orats-track-top50-trigger (05:15 weekdays)
  - fr-spot-cron (*/5 min RTH)
  - fr-earnings-calendar-daily (04:00 weekdays)
  - Various wave-* trading jobs
- Verified ORATS ingest ran successfully today (06:06 UTC, exit 0)
- Created dependency matrix: docs/v1_compatibility_validation.md
- Documented rollback procedure

### Component 0.3: Database Backup (IN PROGRESS)
- Created backup bucket: gs://fl3-v2-db-backups
- Granted Cloud SQL service account write access
- Started async export of option_trades tables (33 GB, RUNNING)
- Pending exports: option_greeks_latest, option_oi_daily, option_contracts, uoa_hit_components

## Key Decisions
1. Database name is `fl3` (not `flowrider`)
2. Cloud SQL exports run one at a time (must be sequential)
3. Large exports (33 GB) take 30+ minutes

## Files Changed
- prd.json (steps 0.1.1-0.1.8, 0.2.1-0.2.6 marked pass)
- docs/v1_compatibility_validation.md (new)

## Git Commits
- f73cdbb: [docs] Complete Component 0.2: V1 Compatibility Validation

## Next Steps
1. Monitor option_trades export completion
2. Run remaining exports sequentially:
   - gcloud sql export sql fr3-pg gs://fl3-v2-db-backups/uoa_hit_components_backup.sql.gz --database=fl3 --table=uoa_hit_components --project=spartan-buckeye-474319-q8
   - gcloud sql export sql fr3-pg gs://fl3-v2-db-backups/option_greeks_latest_backup.sql.gz --database=fl3 --table=option_greeks_latest --project=spartan-buckeye-474319-q8
   - gcloud sql export sql fr3-pg gs://fl3-v2-db-backups/option_oi_daily_backup.sql.gz --database=fl3 --table=option_oi_daily --project=spartan-buckeye-474319-q8
   - gcloud sql export sql fr3-pg gs://fl3-v2-db-backups/option_contracts_backup.sql.gz --database=fl3 --table=option_contracts --project=spartan-buckeye-474319-q8
3. Verify backup integrity
4. Execute DROP statements
5. Run VACUUM FULL

## References
- V1 Project: spartan-buckeye-474319-q8
- V2 Project: fl3-v2-prod
- Cloud SQL Instance: fr3-pg (us-west1)
- Database: fl3
- Backup Bucket: gs://fl3-v2-db-backups
