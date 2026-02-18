# CLAUDE.md Accuracy Audit
**Generated**: 2026-02-04

## Overview

This document compares each project's CLAUDE.md against the ground truth established in Phases 1-4 and flags every inaccuracy.

---

## FL3/CLAUDE.md Issues

### HIGH Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| Core Domains table | References `uoa_hits`, `uoa_baselines`, `uoa_episodes_daily` — **DROPPED** | HIGH | Remove or mark as deprecated |
| Core Domains table | References `ta_snapshots_latest`, `price_levels_latest` — tables don't exist | HIGH | Remove these rows |
| Core Domains table | References `option_greeks_latest`, `option_trades` — **DROPPED** | HIGH | Remove these rows |
| Wave Trade Tables | Documents `wave_trade_decisions`, `wave_trade_fills`, `wave_trade_exits`, `wave_trade_manager_signals` — **NEVER CREATED** | HIGH | Replace with `paper_trades_log`, `active_signals`, `signal_evaluations` |
| V18/V19 Pipeline | Entire section describes jobs that are **DEPRECATED** by V2 | HIGH | Mark as deprecated, point to V2 |

### MEDIUM Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| Docker Images | Lists `fr-ml-jobs` — may be outdated | MEDIUM | Verify which images are still deployed |
| Scheduler Jobs | Lists `fr-update-spot` as active — **PAUSED** | MEDIUM | Mark as paused or remove |
| Scheduler Jobs | V19 Pipeline times may be stale | MEDIUM | Verify current schedules |
| CLI Commands | Lists UOA commands (`uoa-identify`, `uoa-backfill-baselines`) — superseded by V2 | MEDIUM | Mark as deprecated |

### LOW Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| A/B Strategy Pipelines | Documents V18/V19/V19+ — all superseded by V2 paper trading | LOW | Add note that V2 is now primary |
| Memory Tiers | M0/M1/M2 tiers described — not used in V2 | LOW | Mark as V1-only |

### Recommended Actions

1. Add header noting V1 is now **Media/Social/ORATS only**
2. Remove or mark deprecated: UOA, TA, Market Data, Wave Trade sections
3. Add cross-reference to FL3_V2/CLAUDE.md for V2 trading system
4. Update Core Domains table to reflect actual active tables

---

## FL3_V2/CLAUDE.md Issues

### HIGH Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| V2 Tables (New) | Lists `intraday_baselines_30m` as "Time-of-day volume calibration" — **NEVER CONSUMED** | HIGH | Add note: "Data accumulates but `load_baselines()` never called. Hardcoded $50K default used." |
| Baseline Strategy | Entire section describes hybrid baseline logic — **NOT IMPLEMENTED** in live trading | HIGH | Add note: "This logic exists in code but is not called. Live trading uses hardcoded $50K default." |

### MEDIUM Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| V2 Tables | Lists `uoa_triggers_v2`, `gex_metrics_snapshot`, `pd_phase_signals` — **NOT USED** by paper trading | MEDIUM | Clarify these are for future/analysis only |
| Database Schema | Lists 5 tables as live dependencies — only **2 are actually queried** (sentiment_daily, master_tickers) | MEDIUM | Update to reflect actual live dependencies |
| Phase Detection | Documents Phase 1/2/3 detection — **NOT IMPLEMENTED** in live trading | MEDIUM | Mark as "Planned" or "Analysis Only" |
| Greeks Calculations | Documents GEX/Vanna/Charm — **NOT USED** in live trading | MEDIUM | Mark as "Analysis Only" |

### LOW Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| GCP Configuration | States `fl3-v2-prod` project — verify this is correct | LOW | Confirm project name |
| Key SQL Queries | Shows baseline and GEX queries — not used in live | LOW | Mark as "Analysis queries" |
| Time Awareness | Documents market status check — verify it's actually called | LOW | Verify runtime behavior |

### Critical Clarifications Needed

**Actual V2 Live Trading Dependencies (Confirmed by Code Archaeology):**

| Table | Documented | Actual | Status |
|-------|-----------|--------|--------|
| ta_daily_close | READ | NOT USED | TA from Polygon API + JSON cache |
| master_tickers | READ | READ | signal_filter.py:68 |
| earnings_calendar | READ | NOT USED | Filter not integrated |
| sentiment_daily | READ | READ | signal_filter.py:238 |
| spot_prices | READ | NOT USED | Uses Alpaca API directly |
| intraday_baselines_30m | — | NOT USED | Hardcoded $50K default |

### Recommended CLAUDE.md Updates

Add new section:

```markdown
## CRITICAL: Actual Live Trading Dependencies

The paper-trading-live service reads **ONLY 2 database tables**:

| Table | File:Line | Purpose |
|-------|-----------|---------|
| `sentiment_daily` | signal_filter.py:238 | Crowded trade filter |
| `master_tickers` | signal_filter.py:68 | Sector concentration limit |

**Tables NOT used in live trading:**
- `ta_daily_close` — TA comes from Polygon API + JSON cache
- `earnings_calendar` — Earnings filter not integrated
- `spot_prices` — Uses Alpaca API directly
- `intraday_baselines_30m` — Hardcoded $50K default (load_baselines never called)

**Tables written by live trading:**
| Table | Purpose |
|-------|---------|
| `signal_evaluations` | All evaluated signals |
| `active_signals` | Passed signals |
| `paper_trades_log` | Executed trades |
```

---

## MBS/CLAUDE.md Issues

### HIGH Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| File Structure | Shows `bots/` subfolder — **files are in root** | HIGH | Update to actual structure |
| Current Status | Checklist outdated — most items complete | HIGH | Update status |
| Missing | No documentation of fl3 cross-database access | HIGH | Add section on fl3 tables read |

### MEDIUM Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| Bot Lineup | `fl3_core` described as "Ron's existing FL3 system" — **STUB only** | MEDIUM | Note implementation status |
| Bot Lineup | Missing `free_cage_llm` detailed description | MEDIUM | Add strategy details |
| Database | Only mentions local tables, not fl3 cross-access | MEDIUM | Add fl3 tables section |

### LOW Severity Issues

| Section | Issue | Severity | Fix |
|---------|-------|----------|-----|
| Related Project | Obside reference may be outdated | LOW | Verify relevance |
| Commands | May not match actual entry points | LOW | Verify commands work |

### Recommended MBS CLAUDE.md Updates

Add new section:

```markdown
## FL3 Cross-Database Access

MBS bots can read from the shared fl3 database for market data:

### Tables Read by social_sentiment_llm:
- `vw_media_daily_features` — Reddit daily sentiment
- `discord_mentions` — Discord mention counts
- `articles` (source='reddit') — Raw Reddit posts
- `article_entities` — Ticker extractions
- `article_sentiment` — Sentiment scores

### Tables Read by free_cage_llm:
- `articles` — News headlines
- `uoa_triggers_v2` — UOA signals
- `gex_metrics_snapshot` — Gamma exposure
- `ta_snapshots_v2` — Technical indicators

### Connection:
- Uses `SENTIMENT_DATABASE_URL` or derives from `DATABASE_URL`
- Read-only access (no writes to fl3)
- Pool: min=1, max=5 connections
```

---

## Summary by Project

| Project | HIGH Issues | MEDIUM Issues | LOW Issues | Total |
|---------|-------------|---------------|------------|-------|
| FL3 | 5 | 4 | 2 | 11 |
| FL3_V2 | 2 | 4 | 3 | 9 |
| MBS | 3 | 2 | 2 | 7 |
| **Total** | **10** | **10** | **7** | **27** |

---

## Priority Fix Order

### Immediate (This Week)

1. **FL3_V2/CLAUDE.md**: Add "Actual Live Trading Dependencies" section clarifying only 2 tables are read
2. **FL3_V2/CLAUDE.md**: Add note that baselines are NOT consumed (hardcoded $50K)
3. **FL3/CLAUDE.md**: Add header noting V1 is now Media/Social/ORATS only
4. **FL3/CLAUDE.md**: Remove or mark deprecated: Wave Trade Tables section

### Short-Term (This Month)

5. **FL3/CLAUDE.md**: Update Core Domains table to remove dropped tables
6. **MBS/CLAUDE.md**: Add FL3 cross-database access section
7. **MBS/CLAUDE.md**: Update file structure to match reality
8. **FL3_V2/CLAUDE.md**: Mark Phase Detection and Greeks as "Analysis Only"

### When Time Permits

9. **FL3/CLAUDE.md**: Review and update entire V18/V19 pipeline section
10. **MBS/CLAUDE.md**: Update status checklist
11. **All**: Cross-reference related CLAUDE.md files

---

## Ground Truth Reference

For any future CLAUDE.md updates, refer to these Phase 4 documents:

| Document | Location |
|----------|----------|
| V1 Entry Points | `FL3_V2/docs/v1_entry_points_020426.md` |
| V2 Entry Points | `FL3_V2/docs/v2_entry_points_020426.md` |
| MBS Data Access | `FL3_V2/docs/mbs_data_access_020426.md` |
| Cross-Project Dependencies | `FL3_V2/docs/cross_project_dependencies_registry_020426.md` |
| API Matrix | `FL3_V2/docs/api_dependency_matrix_020426.md` |
| Master Data Flow | `FL3_V2/docs/master_data_flow_020426.md` |
| Overlap Analysis | `FL3_V2/docs/overlap_analysis_020426.md` |
