# CLI Handoff: Alpaca Option Chain → GEX Parallel Test (Replace ORATS?)

**Date:** 2026-02-11
**Author:** Claude (scoping session with Ron)
**Status:** Ready for implementation
**Priority:** Medium — cost optimization, no live trading impact
**Goal:** Determine if Alpaca's option chain API can replace ORATS FTP for GEX computation, enabling cancellation of the ORATS subscription

---

## Objective

Build an Alpaca-based nightly GEX sweep that runs **in parallel** with the existing ORATS-based GEX pipeline. Compare outputs for 3-5 trading days. If the data matches closely, ORATS can be dropped.

### Success Criteria

1. Alpaca sweep covers ≥95% of the ~5,500 symbols ORATS covers
2. GEX metrics (net_gex, gamma_flip_level, call_wall, put_wall) match within 10% for ≥90% of symbols
3. No missing Greeks on contracts that matter (near-the-money, >10 OI)
4. Sweep completes in <10 minutes after market close

---

## Background

### Current ORATS Pipeline

- **Job:** `sources/orats_ingest.py` runs at 10 PM PT via Cloud Run
- **Source:** ORATS FTP → ZIP → CSV (~900K per-strike rows)
- **Outputs two tables:**
  1. `orats_daily` (~5,600 rows) — symbol-level aggregates: call/put volume, OI, IV, stock_price
  2. `gex_metrics_snapshot` (~5,600 rows) — net_gex, net_dex, gamma_flip_level, call_wall_strike, put_wall_strike
- **ORATS also feeds:** `orats_daily_volume` used as fallback baseline in UOA detector (`uoa/detector_v2.py` line ~180)

### What Alpaca Provides

- **Endpoint:** `GET /v1beta1/options/snapshots/{underlying_symbol}` (option chain)
- **Rate limit:** 10,000 calls/min on Ron's upgraded plan
- **Per-contract fields:** delta, gamma, theta, vega, rho, implied_volatility, open_interest, strike_price, expiration_date, contract_type, latest trade/quote, underlying price
- **These are the exact fields needed for GEX computation**

### GEX Computation (stays identical)

From `orats_ingest.py` lines 393-415:
```python
# Per-contract GEX
call_gex = gamma * call_oi * 100 * spot**2 * 0.01
put_gex  = gamma * put_oi  * 100 * spot**2 * 0.01 * (-1)
net_gex += call_gex + put_gex

# Per-contract DEX
call_dex = delta * call_oi * 100
put_dex  = abs(delta - 1) * put_oi * 100
net_dex += call_dex - put_dex

# Track per-strike for walls and gamma flip
gex_by_strike[strike] += call_gex + put_gex
call_oi_by_strike[strike] += call_oi
put_oi_by_strike[strike] += put_oi
```

Gamma flip and wall computation use existing functions:
- `_compute_gamma_flip()` — interpolates strike where cumulative GEX crosses zero
- Call wall = strike with max call OI concentration
- Put wall = strike with max put OI concentration

---

## Architecture

### Phase 1: Alpaca GEX Sweep Script (parallel test)

```
After market close (~4:15 PM ET)
    ↓
alpaca_gex_sweep.py
    ↓
For each symbol in universe (~5,500):
    GET /v1beta1/options/snapshots/{symbol}
    ↓
    Parse per-contract: gamma, delta, OI, strike, call/put type
    ↓
    Compute GEX (same math as orats_ingest.py)
    ↓
Write to: gex_metrics_snapshot_alpaca (NEW parallel table)
    ↓
comparison_report.py → compare vs gex_metrics_snapshot (ORATS)
```

### Phase 2: If validated, replace ORATS

- Swap `gex_metrics_snapshot` writes from ORATS to Alpaca source
- Also replace `orats_daily` with Alpaca-sourced symbol-level aggregates (call/put volume, OI, IV, stock_price)
- Update UOA detector fallback baseline to use new source
- Cancel ORATS subscription

---

## Implementation Plan

### Step 1: Create parallel GEX table

```sql
-- Exact same schema as gex_metrics_snapshot, separate table for comparison
CREATE TABLE IF NOT EXISTS gex_metrics_snapshot_alpaca (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    snapshot_ts TIMESTAMPTZ NOT NULL,
    spot_price DOUBLE PRECISION,
    net_gex DOUBLE PRECISION,
    net_dex DOUBLE PRECISION,
    gamma_flip_level DOUBLE PRECISION,
    call_wall_strike DOUBLE PRECISION,
    put_wall_strike DOUBLE PRECISION,
    contracts_analyzed INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, snapshot_ts)
);
CREATE INDEX IF NOT EXISTS idx_gex_alpaca_symbol_ts 
    ON gex_metrics_snapshot_alpaca(symbol, snapshot_ts);
```

### Step 2: Build Alpaca GEX sweep script

**File:** `scripts/alpaca_gex_sweep.py`

**Key design decisions:**
- Reuse GEX computation logic from `orats_ingest.py` (extract into shared module or copy)
- Use asyncio + aiohttp for concurrent requests (same pattern as `polygon_snapshot.py`)
- Concurrency: 50 concurrent requests (well within 10K/min limit)
- Symbol universe: query `SELECT DISTINCT symbol FROM orats_daily WHERE asof_date = (SELECT MAX(asof_date) FROM orats_daily)` to get the same symbol set ORATS covers
- Auth: `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` env vars (already used in signal_filter.py)

**Alpaca API call pattern:**
```python
url = f"https://data.alpaca.markets/v1beta1/options/snapshots/{symbol}"
headers = {
    "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
}
# Response contains: snapshots dict keyed by OCC symbol
# Each snapshot has: greeks{delta,gamma,theta,vega}, impliedVolatility, 
#                    openInterest, latestTrade, latestQuote
```

**Response parsing (per contract in option chain):**
```python
# Alpaca option chain response structure:
# { "snapshots": { 
#     "AAPL250117C00150000": {
#       "latestTrade": {"p": 5.50, "s": 10, "t": "..."},
#       "latestQuote": {"ap": 5.55, "as": 100, "bp": 5.45, "bs": 200, ...},
#       "greeks": {"delta": 0.65, "gamma": 0.03, "theta": -0.05, "vega": 0.15, "rho": 0.02},
#       "impliedVolatility": 0.32
#     }, ...
#   },
#   "next_page_token": "..." (paginate if needed)
# }
#
# NOTE: open_interest is NOT confirmed in the snapshot — it may be on the contract details endpoint
# Need to verify if option chain endpoint includes it or if we need a separate call
```

**CRITICAL: Verify OI availability.** The snapshot endpoint may not include open_interest directly. If not, we need:
- Option A: Use `/v2/options/contracts?underlying_symbols={symbol}` which has `open_interest` field
- Option B: Combine snapshot (for Greeks) + contracts endpoint (for OI)
- This is the #1 thing to verify before building

**OCC symbol parsing** (extract underlying, strike, expiry, call/put):
```python
# Alpaca OCC format: AAPL250117C00150000
# Already have utils/occ_parser.py — reuse extract_underlying()
# Parse: symbol[:-15] = underlying, [:-8][-6:] = YYMMDD, [-8] = C/P, [-8:][:8] = strike/1000
```

**GEX computation:** Copy the accumulation logic from `orats_ingest.py` lines 393-415 and the `_compute_gamma_flip()` and wall detection functions. Keep it identical.

**Rate limiting:**
- 5,500 symbols ÷ 50 concurrent = 110 batches
- At 10K calls/min, each batch of 50 takes ~0.3 seconds
- Total sweep: ~35-60 seconds
- Add retry logic with backoff for 429s

### Step 3: Build comparison report

**File:** `scripts/compare_gex_sources.py`

Compare `gex_metrics_snapshot` (ORATS) vs `gex_metrics_snapshot_alpaca` for same date:

```sql
SELECT 
    o.symbol,
    o.net_gex as orats_gex, a.net_gex as alpaca_gex,
    CASE WHEN o.net_gex != 0 
         THEN ABS(o.net_gex - a.net_gex) / ABS(o.net_gex) * 100 
         ELSE NULL END as gex_pct_diff,
    o.gamma_flip_level as orats_flip, a.gamma_flip_level as alpaca_flip,
    o.call_wall_strike as orats_cwall, a.call_wall_strike as alpaca_cwall,
    o.put_wall_strike as orats_pwall, a.put_wall_strike as alpaca_pwall,
    o.contracts_analyzed as orats_contracts, a.contracts_analyzed as alpaca_contracts,
    o.spot_price as orats_spot, a.spot_price as alpaca_spot
FROM gex_metrics_snapshot o
FULL OUTER JOIN gex_metrics_snapshot_alpaca a 
    ON o.symbol = a.symbol AND o.snapshot_ts::date = a.snapshot_ts::date
WHERE o.snapshot_ts::date = %s  -- comparison date
ORDER BY gex_pct_diff DESC NULLS LAST
```

**Report output:**
- Total symbols: ORATS-only, Alpaca-only, matched
- For matched symbols: mean/median/p95 % difference in net_gex, gamma_flip, walls
- Missing Greeks count (Alpaca contracts with NULL gamma)
- Specific comparison for recently traded symbols (join with paper_trades_log)

### Step 4: Run parallel for 3-5 days

1. Run `alpaca_gex_sweep.py` each evening after ORATS ingest completes
2. Run `compare_gex_sources.py` next morning
3. Review comparison reports
4. If consistent: plan ORATS replacement
5. If divergent: document differences, decide if acceptable

---

## Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Alpaca missing OI field in snapshot | Can't compute GEX | Verify before building; use contracts endpoint if needed |
| Missing Greeks on 0DTE/illiquid options | Undercounts GEX | Compare contract counts; these contracts have minimal GEX impact anyway |
| Alpaca data slightly delayed vs ORATS | GEX values differ | Both are EOD snapshots; timing difference should be minimal |
| Pagination needed for large chains | Missed contracts | Implement next_page_token handling |
| `orats_daily` table still needed for UOA baseline | Can't fully drop ORATS | Phase 2: compute same aggregates from Alpaca data |

---

## Dependencies

### Environment Variables (already configured)
- `ALPACA_API_KEY` — used in `signal_filter.py`
- `ALPACA_SECRET_KEY` — used in `signal_filter.py`
- `DATABASE_URL` — Cloud SQL connection

### Python Packages (already installed)
- `aiohttp` — async HTTP (used by polygon_snapshot.py)
- `psycopg2` — database (used everywhere)
- `asyncio` — async orchestration

### Existing Code to Reuse
- `utils/occ_parser.py` — OCC symbol parsing
- `orats_ingest.py` lines 393-415 — GEX accumulation math
- `orats_ingest.py` `_compute_gamma_flip()` — gamma flip interpolation
- `orats_ingest.py` `_finalize_gex_metrics()` — wall detection, DB write
- `adapters/polygon_snapshot.py` — async HTTP pattern with semaphore/retry

---

## File Inventory

| File | Action | Purpose |
|------|--------|---------|
| `scripts/alpaca_gex_sweep.py` | CREATE | Main sweep script — calls Alpaca, computes GEX, writes to parallel table |
| `scripts/compare_gex_sources.py` | CREATE | Comparison report — ORATS vs Alpaca side-by-side |
| `sql/create_gex_alpaca_table.sql` | CREATE | DDL for parallel comparison table |
| `sources/orats_ingest.py` | NO CHANGE | Keep running as-is during test |
| `sources/gex_compute.py` | CREATE (optional) | Extract shared GEX math into reusable module |

---

## First Step for CLI

**Before building anything, verify the Alpaca option chain response includes open_interest:**

```python
# Quick test script — run this first
import requests, os, json

url = "https://data.alpaca.markets/v1beta1/options/snapshots/AAPL"
headers = {
    "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
}
resp = requests.get(url, headers=headers)
data = resp.json()

# Check first contract
first_key = list(data.get("snapshots", {}).keys())[0]
snapshot = data["snapshots"][first_key]
print(json.dumps(snapshot, indent=2))

# CRITICAL: Check for these fields:
# - greeks.gamma  ← needed for GEX
# - greeks.delta  ← needed for DEX
# - openInterest or open_interest  ← needed for GEX (THIS IS THE QUESTION)
# - impliedVolatility  ← nice to have
# If OI is missing, check /v2/options/contracts?underlying_symbols=AAPL for OI
```

If OI is in the option chain response → proceed with single-endpoint approach.
If OI requires a separate call → adjust design to merge chain + contracts data (still feasible at 10K/min).

---

## Timeline

- Day 1: Verify Alpaca API fields, create parallel table, build sweep script
- Day 2: First parallel run, generate comparison report
- Days 3-5: Continue parallel runs, refine if needed
- Day 6: Go/no-go decision on ORATS cancellation
