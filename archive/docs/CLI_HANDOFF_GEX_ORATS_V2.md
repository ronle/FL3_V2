# CLI Handoff: GEX Computation + ORATS Ingest Migration to V2

**Date:** 2026-02-06
**Author:** Claude (scoping session with Ron)
**Status:** Ready for implementation
**Priority:** Medium — shadow mode first, no live trading impact

---

## Objective

Migrate the V1 `orats-daily-ingest` job to V2 and extend it to compute Gamma Exposure (GEX) metrics from the raw per-strike ORATS FTP file. GEX data will run in **shadow mode** — logged alongside signals for correlation analysis before any filter integration.

### Two Deliverables

1. **V2 ORATS Ingest Job** — replaces V1 `orats-daily-ingest`, adds GEX computation
2. **Historical Backfill CLI** — populates `gex_metrics_snapshot` from Ron's local archive of raw ORATS files

---

## Background

### Why GEX Matters for FL3

GEX (Gamma Exposure) reveals where dealer hedging flows amplify or dampen price moves:
- **Positive GEX** → dealers buy dips, sell rips → movement dampener → pump has a ceiling
- **Negative GEX** → dealers chase momentum → movement amplifier → dump accelerates
- **Gamma flip level** → price where GEX crosses zero → natural inflection point

### Current State

- `orats-daily-ingest` runs in V1 (`spartan-buckeye-474319-q8`) at 10 PM PT
- Pulls raw ORATS FTP file (~900K per-strike rows), aggregates to symbol-level, writes to `orats_daily`
- Raw per-strike data (gamma, delta, OI per contract) is **discarded after aggregation**
- `gex_metrics_snapshot` table exists with correct schema but has **0 rows**
- `orats_daily` is **NOT consumed by any V2 live trading flow** (only offline UOA scanner, LOW criticality)

### Why Migrate to V2

- Consolidates V1→V2 migration (another piece moved out)
- V2 gets full control over ORATS data pipeline
- GEX computation happens in same job, same FTP pull — no second download needed

---

## Architecture

### Data Flow

```
ORATS FTP Server (nightly, after midnight PT)
    ↓
V2 orats-ingest job (replaces V1)
    ↓
Raw file (~900K rows, one per strike/expiry per symbol)
    ├── Aggregation path (EXISTING) → orats_daily (~5,600 rows)
    │   ├── IV Rank computation
    │   ├── HV 30-day computation
    │   └── EMA computation
    └── GEX path (NEW) → gex_metrics_snapshot (~5,600 rows)
        ├── Net GEX per symbol
        ├── Net DEX per symbol
        ├── Call/Put walls
        └── Gamma flip level
```

### Shadow Scoring Integration (Separate PR)

```
SignalGenerator.create_signal_async()
    ├── [existing] Load TA, fetch price, assemble Signal
    └── [NEW] Lookup gex_metrics_snapshot for symbol
        └── Attach as metadata: net_gex, net_dex, gamma_flip, walls
            └── Written to signal_evaluations (NOT used in filtering)
```

---

## ORATS File Format (Confirmed)

Each row = one strike/expiry combo with both call and put data side by side.

### Sample Row

```
ticker  cOpra                    pOpra                    stkPx   expirDate   yte     strike  cVolu  cOi  pVolu  pOi  ...  delta      gamma        ...  spot_px  trade_date
A       A251121C00055000         A251121P00055000         147.69  11/21/2025  0.0411  55      0      1    0      5    ...  0.99947    0.00000443   ...  0        11/6/2025
```

### Field Mapping for GEX

| Need | ORATS Field | Notes |
|------|-------------|-------|
| Symbol | `ticker` | Underlying |
| Strike | `strike` | Strike price |
| Expiry | `expirDate` | Format: `MM/DD/YYYY` |
| Spot | `stkPx` | Underlying price (use first non-zero per symbol) |
| Call OI | `cOi` | Call open interest at this strike/expiry |
| Put OI | `pOi` | Put open interest at this strike/expiry |
| Call Delta | `delta` | As-is — file provides call delta |
| Put Delta | `delta - 1` | Derived (put-call parity) |
| Gamma | `gamma` | Same for call and put (Black-Scholes property) |
| Trade Date | `trade_date` | ORATS asof date, format: `MM/DD/YYYY` or `YYYY-MM-DD` |

### Key Insight: Greeks Are Pre-Computed

ORATS provides `delta` and `gamma` directly per contract. **No Black-Scholes computation needed.** The `delta` field is the call delta; put delta = `delta - 1`. Gamma is identical for call and put at the same strike/expiry.

Source: ORATS API documentation + QuantConnect integration code confirms one row per strike/expiry with call-side greeks, split into call/put using cOi/pOi.

---

## GEX Calculation Specification

### Per-Row (strike/expiry)

```python
spot = stkPx  # or spot_px, whichever is non-zero

# GEX per contract = gamma × OI × 100 × spot² × 0.01
call_gex = gamma * cOi * 100 * spot**2 * 0.01
put_gex  = gamma * pOi * 100 * spot**2 * 0.01 * (-1)  # negative (dealer hedge direction)

# DEX (Delta Exposure)
call_dex = delta * cOi * 100
put_dex  = abs(delta - 1) * pOi * 100
```

### Per-Symbol Aggregation

```python
net_gex = sum(call_gex + put_gex)           # across all strikes/expiries
net_dex = sum(call_dex) - sum(put_dex)       # across all strikes/expiries
call_wall_strike = strike with max(cOi)      # highest call OI concentration
put_wall_strike  = strike with max(pOi)      # highest put OI concentration
gamma_flip_level = interpolated strike where cumulative net GEX crosses zero
contracts_analyzed = count of rows for symbol
```

### Gamma Flip Interpolation

Sort strikes ascending. Walk through cumulative net GEX. When sign flips:

```python
# Linear interpolation between strike_below and strike_above
gamma_flip = strike_below + (strike_above - strike_below) * abs(cum_gex_below) / (abs(cum_gex_below) + abs(cum_gex_above))
```

If net GEX never crosses zero, set `gamma_flip_level = NULL`.

---

## Target Table (Already Exists — 0 Rows)

```sql
-- gex_metrics_snapshot (existing schema, no changes needed)
id                  SERIAL PRIMARY KEY
symbol              TEXT NOT NULL
snapshot_ts         TIMESTAMPTZ NOT NULL    -- ORATS trade_date as EOD: 'YYYY-MM-DD 16:00:00-04'
spot_price          NUMERIC                 -- stkPx from file
net_gex             NUMERIC                 -- calculated
net_dex             NUMERIC                 -- calculated
call_wall_strike    NUMERIC                 -- strike with max cOi
put_wall_strike     NUMERIC                 -- strike with max pOi
gamma_flip_level    NUMERIC                 -- interpolated zero-crossing (NULL if no flip)
net_vex             NUMERIC                 -- NULL for v1 (vanna not in file)
net_charm           NUMERIC                 -- NULL for v1 (charm not in file)
contracts_analyzed  INTEGER                 -- row count per symbol
created_at          TIMESTAMPTZ DEFAULT now()

-- Existing index:
CREATE INDEX idx_gex_symbol_ts ON gex_metrics_snapshot(symbol, snapshot_ts);
```

### Write Convention

- `snapshot_ts` = ORATS `trade_date` converted to `YYYY-MM-DD 16:00:00 America/New_York` (EOD timestamp, not `now()`)
- Use UPSERT on `(symbol, snapshot_ts)` to allow re-runs without duplicates
- Note: table currently lacks a unique constraint on `(symbol, snapshot_ts)` — **add one before first write**

```sql
-- Add unique constraint for idempotent upserts
ALTER TABLE gex_metrics_snapshot ADD CONSTRAINT uq_gex_symbol_ts UNIQUE (symbol, snapshot_ts);
```

---

## Implementation Plan

### Part 1: V2 ORATS Ingest Job

**Source:** Copy `FL3/sources/orats_ingest.py` to `FL3_V2/sources/orats_ingest.py`

**Changes to existing code:**

1. **Keep all existing logic intact:**
   - `_resolve_credentials()` — update to use V2 secrets
   - `_ftp_connection()` — no changes
   - `_find_latest_file()` — no changes
   - `_download_file()` — no changes
   - `_parse_orats_csv()` — extend (see below)
   - `_bulk_upsert_orats()` — no changes
   - `_compute_iv_rank_for_symbols()` — no changes
   - `_compute_hv_30day()` — no changes
   - `_compute_emas_for_ingested_data()` — no changes
   - `_compute_avg_daily_premium_from_files()` — no changes

2. **Extend `_parse_orats_csv()`** to also accumulate GEX data:
   - Add a second `defaultdict` for GEX aggregation alongside the existing symbol-level aggregation
   - While iterating rows, also accumulate per-symbol:
     - `gamma * cOi * 100 * spot² * 0.01` (call GEX)
     - `gamma * pOi * 100 * spot² * 0.01 * (-1)` (put GEX)
     - `delta * cOi * 100` (call DEX)
     - `|delta - 1| * pOi * 100` (put DEX)
     - Track `(strike, cOi)` and `(strike, pOi)` for wall detection
     - Track `(strike, cumulative_gex)` for gamma flip
   - Return GEX results as a second return value or via a callback

3. **Add `_compute_gex_from_strikes()` function:**
   - Takes the per-symbol GEX accumulation data
   - Computes gamma flip via linear interpolation
   - Returns list of dicts ready for DB write

4. **Add `_bulk_upsert_gex()` function:**
   - Writes to `gex_metrics_snapshot`
   - Uses UPSERT on `(symbol, snapshot_ts)`

5. **Update `ingest_orats_daily()` main flow:**
   - After existing aggregation + upsert, also write GEX results
   - GEX write failure should log error but NOT fail the overall job (graceful degradation)

**Dependencies to bring over from V1:**
- `utils/secrets.py` — credential resolution (or adapt to V2's secret pattern)
- `settings.ini` [orats] section — FTP host, file pattern, batch size
- `psycopg` — already in V2 requirements

**Secrets to copy to V2 project (`fl3-v2-prod`):**
- `ORATS_FTP_USER`
- `ORATS_FTP_PASSWORD`

### Part 2: Historical Backfill CLI

**New file:** `FL3_V2/scripts/backfill_gex.py`

**Purpose:** Read Ron's local archive of raw ORATS ZIP files and populate `gex_metrics_snapshot` retroactively.

**Interface:**
```bash
# Single file
python -m scripts.backfill_gex --file "C:\path\to\ORATS_SMV_Strikes_20260205.zip"

# Directory of files
python -m scripts.backfill_gex --dir "C:\path\to\orats_archive" --pattern "*.zip"

# Date range
python -m scripts.backfill_gex --dir "C:\path\to\archive" --from 2025-12-01 --to 2026-02-06
```

**Logic:**
1. For each file, parse all rows (same as `_parse_orats_csv` strike iteration)
2. Compute GEX per symbol using same calculation
3. UPSERT to `gex_metrics_snapshot`
4. Log progress: files processed, symbols computed, rows written

**This does NOT touch `orats_daily`** — only populates `gex_metrics_snapshot`. The `orats_daily` data already exists from V1's historical runs.

### Part 3: Shadow Scoring Integration

**File:** `FL3_V2/paper_trading/signal_filter.py` (in `SignalGenerator` class)

**Change:** ~20 lines — add a DB lookup after signal assembly:

```python
# In SignalGenerator.create_signal_async(), after assembling signal:
gex_row = await db.fetchrow("""
    SELECT net_gex, net_dex, call_wall_strike, put_wall_strike,
           gamma_flip_level, spot_price, contracts_analyzed
    FROM gex_metrics_snapshot
    WHERE symbol = $1
    ORDER BY snapshot_ts DESC LIMIT 1
""", signal.symbol)

if gex_row:
    signal.metadata['net_gex'] = gex_row['net_gex']
    signal.metadata['net_dex'] = gex_row['net_dex']
    signal.metadata['gamma_flip'] = gex_row['gamma_flip_level']
    signal.metadata['call_wall'] = gex_row['call_wall_strike']
    signal.metadata['put_wall'] = gex_row['put_wall_strike']
```

**Zero changes to `SignalFilter.apply()`.**

---

## Deployment Plan

### Phase 1: Local Validation
1. Copy V1 ingest code to V2
2. Add GEX computation
3. Run locally against one ORATS file
4. Verify `gex_metrics_snapshot` has reasonable data (spot-check known tickers)
5. Run backfill against local archive

### Phase 2: Deploy V2 Job
1. Copy ORATS FTP secrets to `fl3-v2-prod`
2. Add unique constraint to `gex_metrics_snapshot`
3. Build + deploy as V2 Cloud Run job (same 10 PM PT schedule)
4. Run once, verify both `orats_daily` and `gex_metrics_snapshot` populated correctly
5. Compare `orats_daily` output against V1's most recent run (should be identical)

### Phase 3: Deprecate V1 Job
1. Disable V1 `orats-daily-ingest` scheduler trigger
2. Monitor V2 job for 3-5 days
3. Delete V1 job after validation

### Phase 4: Shadow Scoring
1. Add GEX metadata lookup to `SignalGenerator`
2. Deploy new paper-trading image
3. Verify GEX fields appearing in `signal_evaluations`

### Phase 5: Analysis
After 2+ weeks of shadow data:
```sql
-- Correlate GEX with trade outcomes
SELECT
    se.symbol,
    se.detected_at,
    (se.metadata->>'net_gex')::numeric as net_gex,
    (se.metadata->>'gamma_flip')::numeric as gamma_flip,
    pt.pnl_pct,
    pt.exit_reason
FROM signal_evaluations se
JOIN paper_trades_log pt ON se.symbol = pt.symbol
    AND se.detected_at::date = pt.entry_time::date
WHERE se.metadata->>'net_gex' IS NOT NULL
ORDER BY se.detected_at;
```

---

## Edge Cases & Validation

### Data Quality

| Case | Handling |
|------|----------|
| `gamma = 0` for deep ITM/OTM | Include in aggregation (contributes 0 GEX, correct behavior) |
| `cOi = 0 AND pOi = 0` | Skip row (no open interest = no dealer exposure) |
| `stkPx = 0` or missing | Use `spot_px` field as fallback; skip symbol if both zero |
| Symbol with < 5 contracts | Compute anyway but flag with low `contracts_analyzed` |
| Expiry in the past | Include — ORATS file represents EOD snapshot, OI is valid until settled |
| Gamma flip never crosses zero | Set `gamma_flip_level = NULL` |
| Multiple dates in one file | Group by `(ticker, trade_date)` — one GEX row per symbol per date |

### Sanity Checks (Post-Run)

```sql
-- Verify row count matches expected symbols
SELECT COUNT(*), snapshot_ts FROM gex_metrics_snapshot GROUP BY snapshot_ts ORDER BY snapshot_ts DESC LIMIT 5;

-- Spot-check major tickers
SELECT symbol, net_gex, net_dex, call_wall_strike, put_wall_strike, gamma_flip_level, contracts_analyzed
FROM gex_metrics_snapshot
WHERE symbol IN ('AAPL', 'TSLA', 'SPY', 'NVDA', 'META')
  AND snapshot_ts = (SELECT MAX(snapshot_ts) FROM gex_metrics_snapshot);

-- Distribution check: most symbols should have non-zero GEX
SELECT
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE net_gex != 0) as nonzero_gex,
    COUNT(*) FILTER (WHERE gamma_flip_level IS NOT NULL) as has_flip,
    AVG(contracts_analyzed) as avg_contracts
FROM gex_metrics_snapshot
WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM gex_metrics_snapshot);
```

---

## Files to Create/Modify

| File | Action | Lines (est.) |
|------|--------|-------------|
| `FL3_V2/sources/orats_ingest.py` | Copy from V1 + extend | ~750 (existing ~650 + ~100 new) |
| `FL3_V2/sources/__init__.py` | Create if missing | 1 |
| `FL3_V2/scripts/backfill_gex.py` | New — historical backfill CLI | ~150 |
| `FL3_V2/paper_trading/signal_filter.py` | Add GEX metadata lookup | ~20 lines added |
| `FL3_V2/tests/pipeline_health_check.py` | Add TEST-2.11 for gex_metrics_snapshot | ~30 |
| `FL3_V2/CLAUDE.md` | Update "What's NOT in the Live Path" section | ~10 |

### SQL Migration

```sql
-- Run before first GEX write
ALTER TABLE gex_metrics_snapshot ADD CONSTRAINT uq_gex_symbol_ts UNIQUE (symbol, snapshot_ts);
```

---

## Open Items for Implementation

1. **V1 `utils/secrets.py`** — review and decide: copy to V2, or adapt to V2's existing secret resolution pattern?
2. **Local archive location** — Ron to confirm path to archived ORATS ZIP files for backfill
3. **`signal_evaluations` metadata column** — currently has `rejection_reason VARCHAR` but no generic metadata/JSONB column. May need to add one, or attach GEX data to `active_signals` instead.
4. **Cloud Run job name** — suggest `orats-daily-ingest` (same name, different project) or `fl3-v2-orats-ingest`?
5. **`total_volume` and `total_open_interest` columns** — V1 ingest computes these but they're not in `_parse_orats_csv()` output. Verify they're derived columns in the EMA computation or if they need explicit aggregation.

---

## Success Criteria

- [ ] V2 ORATS job produces identical `orats_daily` output as V1
- [ ] `gex_metrics_snapshot` populated with ~5,000+ symbols per nightly run
- [ ] Backfill covers available historical archive
- [ ] GEX data appearing in signal metadata (shadow mode)
- [ ] V1 `orats-daily-ingest` deprecated and disabled
- [ ] No impact to V2 live trading performance (GEX lookup < 10ms)
