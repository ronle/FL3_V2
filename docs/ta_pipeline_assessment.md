# TA Pipeline Assessment (Component 0.4.4)

## V1 TA Pipeline Status

### Current Tables
| Table | Timeframes | Latest Data | Symbols | Status |
|-------|------------|-------------|---------|--------|
| `ta_snapshots_latest` | 1Day | 2025-11-07 | 74 | STALE |
| `ta_snapshots_latest` | 1Min | 2025-10-08 | 1 | STALE |
| `ta_snapshots_latest` | 5Min | 2026-01-21 | 1,437 | Recent (1 week) |

### V1 Schema
```sql
ta_snapshots_latest (
    symbol TEXT,
    timeframe TEXT,
    asof TIMESTAMPTZ,
    close FLOAT,
    sma20, sma50, sma200 FLOAT,
    rsi14 FLOAT,
    macd, macd_signal, macd_hist FLOAT,
    atr14 FLOAT,
    notes TEXT,
    updated_at TIMESTAMPTZ
)
```

### V1 Issues
1. **Mixed timeframes** - Daily, 1-min, 5-min all in same table
2. **Sparse 1-min coverage** - Only 1 symbol
3. **Missing indicators** - No VWAP, no EMA-9
4. **Stale daily data** - 2+ months old
5. **No trigger-based tracking** - Static symbol list

---

## V2 Requirements

### New Table: `ta_snapshots_v2`
```sql
CREATE TABLE ta_snapshots_v2 (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    snapshot_ts TIMESTAMPTZ NOT NULL,
    price NUMERIC,
    volume BIGINT,
    rsi_14 NUMERIC,
    atr_14 NUMERIC,
    vwap NUMERIC,
    sma_20 NUMERIC,
    ema_9 NUMERIC,
    UNIQUE(symbol, snapshot_ts)
);
CREATE INDEX idx_ta_v2_symbol_ts ON ta_snapshots_v2(symbol, snapshot_ts);
```

### Key Differences from V1
| Aspect | V1 | V2 |
|--------|----|----|
| Timeframe | Mixed (1D, 1M, 5M) | 5-min only |
| Symbol selection | Static list | Trigger-based (permanent) |
| Refresh | Unknown schedule | Every 5 min during market |
| Indicators | SMA20/50/200, RSI, MACD, ATR | RSI-14, ATR-14, VWAP, SMA-20, EMA-9 |
| Data source | Alpaca | Alpaca (batched) |
| Expected rows/day | ~10K | ~78K (1000 symbols × 78 intervals) |

### Required Indicators
| Indicator | Purpose | Window |
|-----------|---------|--------|
| RSI-14 | Overbought/oversold detection | 14 bars |
| ATR-14 | Volatility normalization | 14 bars |
| VWAP | Fair value reference | Session |
| SMA-20 | Trend direction | 20 bars |
| EMA-9 | Short-term momentum | 9 bars |

---

## Implementation Plan

### 1. Tracked Tickers V2 Manager
- Symbol gets added on first UOA trigger
- Never removed (permanent tracking)
- `ta_enabled` flag for batch inclusion

### 2. Alpaca Batched Bars Fetcher
- Batch requests (50-100 symbols per call)
- Rate limiting (< 200 req/min)
- Error handling for missing symbols

### 3. TA Calculator
- Pure Python calculations (no external TA lib dependency)
- Handle insufficient data gracefully
- Return nulls for incomplete windows

### 4. TA Pipeline Orchestrator
- Run every 5 minutes during market hours
- Get symbols from tracked_tickers_v2
- Batch fetch → Calculate → Batch insert
- Target: < 60 seconds for 1000 symbols

---

## Migration Path

### Phase 1: Coexistence
- Create new `ta_snapshots_v2` table
- Keep V1 `ta_snapshots_latest` read-only
- V2 pipeline writes to new table only

### Phase 2: V2 Active
- V2 pipeline running during market hours
- Monitor for completeness and accuracy
- V1 table remains but not updated

### Phase 3: Cleanup (Future)
- After validation period
- Drop V1 TA tables if no longer needed
- Update any dashboards/views

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Alpaca rate limits | Batching + 300ms delays between calls |
| Missing symbol data | Skip symbol, log warning, retry next cycle |
| Calculation errors | Return null, don't break pipeline |
| Memory growth | Process in batches, don't cache all history |
| 60-second SLA miss | Parallelize fetch + calculate, batch inserts |

---

## Validation Criteria (CP4)
- [ ] TA pipeline stable at 1000 symbols
- [ ] Completes within 60 seconds
- [ ] No memory leaks over 6+ hours
- [ ] Handles missing data gracefully
- [ ] All 5 indicators calculated correctly

---

*Assessment Date: 2026-01-28*
*Status: Ready for implementation in Phase 4*
