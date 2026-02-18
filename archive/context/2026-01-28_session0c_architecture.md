# Session: 2026-01-28 Session 0c — FL3 V2 Architecture

## Summary

Established the V2 architecture decision: separate GCP project sharing PostgreSQL with V1, market-wide firehose approach replacing bounded symbol tracking, and database cleanup strategy to recover 40+ GB.

## Architecture Decision

### V1 vs V2 Approach

| Aspect | V1 (Current) | V2 (New) |
|--------|--------------|----------|
| Coverage | ~600 tracked symbols | ~5,600 symbols (market-wide) |
| Data Source | Polling + tracked universe | Polygon firehose (T.*) |
| Detection | UOA hits table | In-memory rolling aggregation |
| Greeks | Stale polling | On-demand snapshots when triggered |
| Storage | Raw trades (33 GB) | Aggregates only (~500 MB/mo) |

### Shared vs Separate Resources

```
┌─────────────────────────────────────────────────────────────────┐
│                    GCP PostgreSQL (Shared)                      │
├─────────────────────────────────────────────────────────────────┤
│  LEGACY TABLES (V1)        │  NEW TABLES (V2)                  │
│  option_trades_*           │  intraday_baselines_30m           │
│  uoa_hits                  │  uoa_triggers_v2                  │
│  uoa_hit_components        │  gex_metrics_snapshot             │
│  ta_snapshots_*            │  pd_phase_signals                 │
│  wave_instances            │  tracked_tickers_v2               │
│                            │  ta_snapshots_v2                  │
├─────────────────────────────────────────────────────────────────┤
│  SHARED TABLES (Both use)                                       │
│  orats_daily, orats_daily_returns, spot_prices                 │
└─────────────────────────────────────────────────────────────────┘
```

## Database Cleanup

### Current State: 54 GB

| Table | Size | Action |
|-------|------|--------|
| `option_trades_*` | ~33 GB | DROP — firehose replaces |
| `uoa_hit_components` | 7.5 GB | DROP — old detection |
| `orats_daily` | 5.9 GB | KEEP — baseline source |
| `articles` + entities | 3.5 GB | Optional |
| `option_greeks_latest` | 0.7 GB | DROP — will be replaced |

### Recovery: ~42 GB

Post-cleanup target: 10-12 GB

### Cleanup SQL

```sql
-- ~40 GB recovery
DROP TABLE IF EXISTS option_trades_2025_09;
DROP TABLE IF EXISTS option_trades_2025_10;
DROP TABLE IF EXISTS option_trades_2025_11;
DROP TABLE IF EXISTS option_trades_2025_12;
DROP TABLE IF EXISTS option_trades_default;
DROP TABLE IF EXISTS uoa_hit_components;
DROP TABLE IF EXISTS option_greeks_latest;
DROP TABLE IF EXISTS option_oi_daily;
DROP TABLE IF EXISTS option_contracts;
VACUUM FULL;
```

## V2 Service Architecture

```
FL3_V2 GCP Project
├── firehose_svc      — Polygon T.* websocket consumer
├── trigger_handler   — UOA detection from rolling aggregates
├── ta_refresh_v2     — Alpaca batched bars for tracked symbols
└── phase_scorer      — P&D phase detection engine
```

## Key Decisions

1. **Coexistence**: V1 and V2 run in parallel until V2 proves superior
2. **Shared DB**: Same PostgreSQL instance, separate table prefixes
3. **Firehose over polling**: Market-wide coverage via Polygon websocket
4. **On-demand Greeks**: Only calculate when UOA triggers, not continuously
5. **Rolling aggregation**: In-memory bucketing, not raw trade storage

## API Limits Confirmed

| Service | Limit | V2 Usage |
|---------|-------|----------|
| Polygon Options Firehose | 1 connection | Full market stream |
| Polygon Snapshots | 50K calls/day | On-demand for triggered symbols |
| Alpaca Bars | 200 req/min | Batched (50 symbols per call) |

---
*Session end: 2026-01-28*
