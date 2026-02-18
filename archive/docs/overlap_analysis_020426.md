# Overlap & Redundancy Analysis
**Generated**: 2026-02-04

## Executive Summary

This document identifies every area where redundant pipelines, tables, services, or configurations exist across V1 (spartan-buckeye) and V2 (fl3-v2-prod) projects. Each overlap includes impact assessment and recommended resolution.

**Total Overlaps Identified**: 10
- HIGH Priority: 3
- MEDIUM Priority: 4
- LOW Priority: 3

---

## Overlap #1: Spot Prices — Duplicate Jobs

- **What**: Two jobs exist for updating spot prices
- **Projects**: V1 (`fr-update-spot`) and V2 (`update-spot-prices`)
- **Details**:
  | Property | V1 Job | V2 Job |
  |----------|--------|--------|
  | Job Name | `fr-update-spot` | `update-spot-prices` |
  | Image | `fl3-cli:latest` | V2 image |
  | Status | PAUSED | ACTIVE |
  | Frequency | Was every 1 min | Every 1 min |
  | Destination | `spot_prices` | `spot_prices` |
- **Impact**: None currently — V1 job correctly paused
- **Recommended Resolution**: Delete V1 job `fr-update-spot` and its scheduler
- **Priority**: LOW
- **Effort**: 15 minutes

---

## Overlap #2: Earnings Calendar — Duplicate Jobs

- **What**: Two jobs exist for fetching earnings calendar
- **Projects**: V1 (`fr3-earnings-calendar`) and V2 (`fetch-earnings-calendar`)
- **Details**:
  | Property | V1 Job | V2 Job |
  |----------|--------|--------|
  | Job Name | `fr3-earnings-calendar` | `fetch-earnings-calendar` |
  | Status | PAUSED | ACTIVE |
  | Frequency | Was daily | Daily 4 AM |
  | Destination | `earnings_calendar` | `earnings_calendar` |
- **Impact**: None currently — V1 scheduler paused
- **Recommended Resolution**: Delete V1 job `fr3-earnings-calendar` and its scheduler
- **Priority**: LOW
- **Effort**: 15 minutes

---

## Overlap #3: Sentiment Aggregation — View vs Table

- **What**: Both a stale table and a current view exist for sentiment data
- **Projects**: V1 (both artifacts)
- **Details**:
  | Property | Table | View |
  |----------|-------|------|
  | Name | `sentiment_daily` | `vw_media_daily_features` |
  | Status | STALE (14 days) | CURRENT |
  | Updated By | `fr-sentiment-agg` (FAILING) | Computes on-the-fly |
  | Used By | V2 paper trading | Nothing currently |
- **Impact**: **HIGH** — V2 paper trading uses stale `sentiment_daily` for crowded trade filter
- **Recommended Resolution**:
  1. Fix `fr-sentiment-agg` job to restore table freshness, OR
  2. Migrate V2 paper trading to query `vw_media_daily_features` instead
- **Priority**: HIGH
- **Effort**: 2-4 hours

---

## Overlap #4: Paper Trading Services — Duplicate Services

- **What**: Two `paper-trading-live` services exist in different projects
- **Projects**: V1 (`spartan-buckeye`) and V2 (`fl3-v2-prod`)
- **Details**:
  | Property | V1 Service | V2 Service |
  |----------|------------|------------|
  | Project | `spartan-buckeye-474319-q8` | `fl3-v2-prod` |
  | Service Name | `paper-trading-live` | `paper-trading-live` |
  | Revision | v29 | v30+ |
  | Alpaca Key | `APCA_V19PLUS_API_KEY_ID` | `ALPACA_API_KEY` |
  | Status | Running | ACTIVE (primary) |
- **Impact**: **HIGH** — Potential confusion about which is authoritative. Different Alpaca accounts may have different positions.
- **Recommended Resolution**:
  1. Confirm V2 is the authoritative service
  2. Stop V1 service (scale to 0 or delete)
  3. Document which Alpaca account is the "real" paper trading account
- **Priority**: HIGH
- **Effort**: 1 hour

---

## Overlap #5: Social Data — Inconsistent Storage

- **What**: Social data stored in multiple locations with different schemas
- **Projects**: V1 (articles), V1 (discord_mentions), MBS (planned)
- **Details**:
  | Source | Table | Schema |
  |--------|-------|--------|
  | Reddit | `articles` (source='reddit') | Mixed with news |
  | Discord | `discord_mentions` | Separate table |
  | MBS (planned) | `social_sentiment_llm` | Own schema |
- **Impact**: Fragmented social data makes unified analysis difficult
- **Recommended Resolution**: Per `SOCIAL_DATA_CONSOLIDATION_PLAN.md`:
  1. Create unified view spanning all social sources
  2. Standardize timestamp, ticker, sentiment columns
  3. Consider migrating to single `social_mentions` table
- **Priority**: MEDIUM
- **Effort**: 4-8 hours

---

## Overlap #6: Docker Images — Sprawl

- **What**: Excessive Docker images in V1 Artifact Registry
- **Projects**: V1 (`spartan-buckeye`)
- **Details**:
  - 20 total images in registry
  - 14 images NOT deployed to any job/service
  - Total storage: ~109 GB
  - `fr-media-jobs` uses 5 different tags across jobs
- **Impact**: Storage costs, confusion about which images are current
- **Recommended Resolution**:
  1. Delete images not referenced by any job/service
  2. Consolidate `fr-media-jobs` to single tag
  3. Implement lifecycle policy (delete images older than 30 days with no references)
- **Priority**: MEDIUM
- **Effort**: 2 hours

---

## Overlap #7: ORATS Images — Naming Confusion

- **What**: Two different Docker images for ORATS-related jobs
- **Projects**: V1 (`spartan-buckeye`)
- **Details**:
  | Image | Used By |
  |-------|---------|
  | `fr-orats-jobs` | `orats-daily-ingest` |
  | `orats-jobs` | `orats-track-top50` |
- **Impact**: Confusion about which image to update when fixing ORATS jobs
- **Recommended Resolution**:
  1. Consolidate to single `orats-jobs` image
  2. Update `orats-daily-ingest` to use consolidated image
  3. Delete `fr-orats-jobs` image
- **Priority**: LOW
- **Effort**: 1 hour

---

## Overlap #8: Duplicate NewsData Secrets

- **What**: Two secrets storing the same NewsData.io API key
- **Projects**: V1 (`spartan-buckeye`)
- **Details**:
  | Secret Name | Used By |
  |-------------|---------|
  | `API_KEY_NEWSDATA_IO` | `fr3-media-news-api` job |
  | `NEWSDATA_APIKEY` | Likely nothing (duplicate) |
- **Impact**: Confusion about which secret to rotate
- **Recommended Resolution**:
  1. Verify both secrets contain identical values
  2. Search codebase for `NEWSDATA_APIKEY` references
  3. Delete `NEWSDATA_APIKEY` if unused
- **Priority**: LOW
- **Effort**: 30 minutes

---

## Overlap #9: Multiple Alpaca API Keys

- **What**: Four different Alpaca API key sets across projects
- **Projects**: V1 and V2
- **Details**:
  | Secret Name | Project | Used By |
  |-------------|---------|---------|
  | `APCA_API_KEY_ID` | V1 | Legacy (unclear) |
  | `APCA_PAPER_API_KEY_ID` | V1 | Legacy paper trading |
  | `APCA_V19PLUS_API_KEY_ID` | V1 | V1 `paper-trading-live` |
  | `ALPACA_API_KEY` | V2 | V2 `paper-trading-live` |
- **Impact**: **HIGH** — Confusion about which account has real positions, potential for split activity
- **Recommended Resolution**:
  1. Audit which Alpaca account each key belongs to
  2. Designate ONE paper account and ONE live account
  3. Consolidate to 2 secrets total (paper + live)
  4. Delete unused secrets
- **Priority**: HIGH
- **Effort**: 2 hours

---

## Overlap #10: Unused Finnhub Secrets

- **What**: Finnhub API keys exist in both projects but are not used
- **Projects**: V1 and V2
- **Details**:
  | Project | Secret | Status |
  |---------|--------|--------|
  | V1 | `FINNHUB_API_KEY` | Not referenced by any job |
  | V2 | `FINNHUB_API_KEY` | Not referenced by any job |
- **Impact**: Potential security risk (unused credentials), confusion
- **Recommended Resolution**:
  1. Verify no code references Finnhub
  2. Delete secrets from both projects
  3. Consider canceling Finnhub subscription if paid
- **Priority**: MEDIUM
- **Effort**: 30 minutes

---

## Summary Table

| # | Overlap | Priority | Effort | Status |
|---|---------|----------|--------|--------|
| 1 | Spot Prices Duplicate Jobs | LOW | 15 min | V1 paused |
| 2 | Earnings Calendar Duplicate Jobs | LOW | 15 min | V1 paused |
| 3 | Sentiment View vs Table | HIGH | 2-4 hrs | BROKEN |
| 4 | Paper Trading Duplicate Services | HIGH | 1 hr | Needs cleanup |
| 5 | Social Data Inconsistent Storage | MEDIUM | 4-8 hrs | Fragmented |
| 6 | Docker Image Sprawl | MEDIUM | 2 hrs | 109 GB waste |
| 7 | ORATS Image Naming | LOW | 1 hr | Confusing |
| 8 | Duplicate NewsData Secrets | LOW | 30 min | Redundant |
| 9 | Multiple Alpaca Keys | HIGH | 2 hrs | Risk |
| 10 | Unused Finnhub Secrets | MEDIUM | 30 min | Dead weight |

---

## Recommended Action Order

### Immediate (This Week)
1. **Overlap #4**: Confirm V2 is authoritative paper trading, stop V1 service
2. **Overlap #9**: Audit Alpaca keys, consolidate to 2
3. **Overlap #3**: Fix sentiment aggregation OR migrate to view

### Short-Term (This Month)
4. **Overlap #10**: Delete unused Finnhub secrets
5. **Overlap #6**: Clean up Docker image sprawl
6. **Overlap #8**: Delete duplicate NewsData secret

### When Time Permits
7. **Overlap #1**: Delete V1 spot prices job
8. **Overlap #2**: Delete V1 earnings calendar job
9. **Overlap #7**: Consolidate ORATS images
10. **Overlap #5**: Implement social data consolidation plan

---

## Total Estimated Cleanup Effort

| Priority | Count | Total Effort |
|----------|-------|--------------|
| HIGH | 3 | 5-7 hours |
| MEDIUM | 4 | 7-11 hours |
| LOW | 3 | 2 hours |
| **Total** | **10** | **14-20 hours** |
