# Claude Code Project Context — FL3_V2

## SESSION CONTEXT FILE
If `.claude_session_context.md` exists in the project root, read it immediately before anything else. It contains injected rules and recent changelog from the launcher script.

---

## COMPACTION RECOVERY
If you have just been compacted or feel uncertain about your instructions:
1. Re-read AGENT_RULES.md immediately — it is short (52 lines) and has all behavioral rules
2. Re-read this file from the top
3. Tell Ron: "Context was compacted — re-reading rules now" before continuing

---

## Session Continuity (MANDATORY)

### On Startup — ALWAYS do this first:
1. Read the last 50 lines of the active session log (check `logs/sessions.json` for path)
2. Read `CHANGELOG.md` for recent milestones
3. State what was last worked on and what the current task is before proceeding

### Before Closing — ALWAYS do this last:
Before ending ANY session (whether asked to or not), you MUST:
1. Append a summary block to `CHANGELOG.md`:
   ```
   ## [YYYY-MM-DD HH:MM] — <one-line summary>
   ### Done
   - bullet list of completed work
   ### State
   - current status of any in-progress work
   ### Next
   - what to do next session
   ### Files Changed
   - list of files modified
   ```
2. Update the `## Current Status` section in this file (`CLAUDE.md`) to reflect the latest state
3. Write final status to the session log if one is active

**This is non-negotiable. If the user says "done", "close", "stop", or "end session" — document first, then close.**

---

## Current Status

_Last updated: 2026-02-18_

- Account B deployed to Cloud Run and actively trading (revision `paper-trading-live-00089-d86`)
- Account A healthy with 10 positions, Account B filled 10 slots on first cycle
- Dashboard tabs live: "Account B Signals", "Account B Positions", "Account B Closed"
- Fixed: engulfing checker DB connection (`.strip()`), signal log format (string not float), Cloud Run traffic routing (`--to-latest`)
- Options flat file download running for 2023-2024 data (PID 143320)

---

## Session Startup (MANDATORY)

**At the start of EVERY session, before any other work:**

1. Run `date` or `Get-Date` to confirm current date and time. Remember, local time is set in PST.
2. State the current date, time (ET), and day of week
3. Determine market status:
   - **Market Open**: Mon-Fri 9:30am-4:00pm ET (excluding holidays)
   - **Pre-Market**: Mon-Fri 4:00am-9:30am ET
   - **After-Hours**: Mon-Fri 4:00pm-8:00pm ET
   - **Closed**: Weekends and holidays
4. If working on time-sensitive code (firehose, TA pipeline), confirm whether we're in market hours

**Example startup:**
```
Current time: Tuesday, January 28, 2026 at 2:45 PM ET
Market Status: OPEN (closes in 1h 15m)
```

---

## Temporary & Working Files

All temporary files, scratch scripts, and work-support files (e.g. one-off analysis scripts, debug outputs, data exports, intermediate results) **MUST** be placed under the `temp/` directory in the project root. Never create throwaway files in the repo root or other project directories.

The `temp/` folder is excluded from git, Docker builds, and Cloud Build uploads.

---

## Project Overview

FL3_V2 is a market-wide pump-and-dump detection system that processes ALL options trades via Polygon websocket firehose to detect unusual options activity (UOA), calculate gamma exposure (GEX) metrics, and identify P&D phase transitions.

**Key Differences from V1:**
| Aspect | V1 | V2 |
|--------|----|----|
| Coverage | ~600 tracked symbols | ~5,600 symbols (market-wide) |
| Data Source | Polling + tracked universe | Firehose (T.*) |
| Detection | UOA hits table | In-memory rolling aggregation |
| Greeks | Stale polling | On-demand snapshots |
| TA Pipeline | Disabled | Re-enabled, permanent tracking |
| Storage | Raw trades (33 GB) | Aggregates only (~500 MB/mo) |

**Core Flow**: 
```
Polygon Firehose (T.*) → OCC Parser → Rolling Aggregator → UOA Detector → 
  → Trigger Handler → GEX Calculator → Phase Detector → Alerts
```

---

## GCP Configuration

### V2 Project (NEW)
- **Project**: `fl3-v2-prod` (or similar)
- **Region**: `us-west1`
- **Registry**: `us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2`

### Shared Resources (from V1)
- **Cloud SQL**: `fr3-pg` (PostgreSQL) — same database, different tables
- **Project V1**: `spartan-buckeye-474319-q8` (reference only)

### Secrets (Copy from V1)
| Secret | Purpose |
|--------|---------|
| `DATABASE_URL` | PostgreSQL connection string |
| `POLYGON_API_KEY` | Polygon.io API (firehose + snapshots) |
| `ALPACA_API_KEY` | Alpaca Market Data (TA bars) |
| `ALPACA_SECRET_KEY` | Alpaca authentication |
| `ORATS_API_KEY` | ORATS API (if direct access needed) |

---

## Database Schema

### Shared Tables (V1 + V2)

| Table | Rows | Purpose | Owner |
|-------|------|---------|-------|
| `orats_daily` | 2.9M | Options activity baseline | V1 (active) |
| `orats_daily_returns` | 2.9M | Forward returns for backtesting | V1 (active) |
| `spot_prices` | ~15K | Current underlying prices | V1 (active) |

**CRITICAL**: V1's `orats_ingest` and `price_ingest` jobs MUST remain running. V2 depends on this shared data.

### V2 Tables (New)

| Table | Purpose | Est. Rows/Day |
|-------|---------|---------------|
| `intraday_baselines_30m` | Time-of-day volume calibration | ~13,000 |
| `uoa_triggers_v2` | Triggered UOA events with context | 50-500 |
| `gex_metrics_snapshot` | GEX/DEX/Vanna/Charm on trigger | 50-500 |
| `pd_phase_signals` | Phase transitions (Setup/Accel/Reversal) | 10-100 |
| `tracked_tickers_v2` | Permanent tracking list | ~1,000 |
| `ta_snapshots_v2` | TA data (5-min intervals) | ~78,000 |

### Key Schemas

**intraday_baselines_30m**
```sql
CREATE TABLE intraday_baselines_30m (
    symbol TEXT NOT NULL,
    trade_date DATE NOT NULL,
    bucket_start TIME NOT NULL,  -- 09:30, 10:00, etc.
    prints INTEGER NOT NULL,
    notional NUMERIC NOT NULL,
    contracts_unique INTEGER,
    PRIMARY KEY (symbol, trade_date, bucket_start)
);
```

**gex_metrics_snapshot**
```sql
CREATE TABLE gex_metrics_snapshot (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    snapshot_ts TIMESTAMPTZ NOT NULL,
    spot_price NUMERIC,
    net_gex NUMERIC,           -- Net gamma exposure
    net_dex NUMERIC,           -- Net delta exposure  
    call_wall_strike NUMERIC,  -- Max call OI strike
    put_wall_strike NUMERIC,   -- Max put OI strike
    gamma_flip_level NUMERIC,  -- Price where GEX = 0
    net_vex NUMERIC,           -- Vanna exposure
    net_charm NUMERIC          -- Charm exposure
);
CREATE INDEX idx_gex_symbol_ts ON gex_metrics_snapshot(symbol, snapshot_ts);
```

**tracked_tickers_v2**
```sql
CREATE TABLE tracked_tickers_v2 (
    symbol TEXT PRIMARY KEY,
    first_trigger_ts TIMESTAMPTZ,
    trigger_count INTEGER DEFAULT 1,
    last_trigger_ts TIMESTAMPTZ,
    ta_enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**ta_snapshots_v2**
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

---

## Baseline Strategy

### Cold Start (Days 1-30)
No historical bucket data exists. Use ORATS daily as proxy:

```python
expected_bucket_volume = (orats_daily.total_volume / 390) * time_multiplier[bucket]
```

**Time-of-Day Multipliers:**
| Period | Time (ET) | Multiplier |
|--------|-----------|------------|
| Open | 9:30-9:45 | 3.0x |
| Morning | 9:45-11:00 | 1.5x |
| Midday | 11:00-14:00 | 0.7x |
| Lunch | 12:00-13:00 | 0.5x |
| Afternoon | 14:00-15:30 | 1.2x |
| Close | 15:30-16:00 | 2.5x |

### Warm (Days 30+)
Use rolling 20-day bucket history:

```python
baseline = avg(intraday_baselines_30m WHERE trade_date > TODAY - 20 days)
```

### Hybrid (Ongoing)
- Prefer bucket history if available
- Fall back to ORATS-derived baseline for new symbols

---

## Phase Detection Framework

### Phase 1: Setup
| Signal | Source | Threshold |
|--------|--------|-----------|
| UOA trigger | Firehose | Volume > 3x baseline |
| IV elevation | ORATS | iv_rank > 50 |
| OI building | Snapshot | Call OI increasing |

### Phase 2: Acceleration  
| Signal | Source | Threshold |
|--------|--------|-----------|
| Price breakout | TA | Price > 3x ATR |
| Volume surge | Firehose | Sustained high volume |
| GEX positive | Snapshot | Net GEX > 0 (dealer long gamma) |
| RSI overbought | TA | RSI > 70 |

### Phase 3: Reversal
| Signal | Source | Threshold |
|--------|--------|-----------|
| Vanna flip | Snapshot | Net VEX sign change |
| GEX negative | Snapshot | Net GEX < 0 (dealer short gamma) |
| RSI divergence | TA | Price up, RSI down |
| Volume climax | Firehose | Spike then drop |
| IV crush | ORATS | iv_rank dropping |

---

## Greeks Calculations

### Black-Scholes Components
```
d1 = [ln(S/K) + (r - q + σ²/2)T] / (σ√T)
d2 = d1 - σ√T
```

### First-Order Greeks
```
Delta (call) = e^(-qT) × N(d1)
Delta (put)  = e^(-qT) × (N(d1) - 1)
Gamma = e^(-qT) × n(d1) / (S × σ × √T)
```

### Second-Order Greeks
```
Vanna = -e^(-qT) × n(d1) × (d2/σ)
Charm = -e^(-qT) × n(d1) × [q + (d2×σ)/(2T√T)]
```

### Exposure Aggregations
```
GEX per contract = Γ × OI × 100 × S² × 0.01
Net GEX = Σ[GEX_Call(K)] - Σ[GEX_Put(K)]
Net DEX = Σ[Δ_call × CallOI × 100] - Σ[|Δ_put| × PutOI × 100]
Gamma Flip = Price where Net GEX crosses zero
```

---

## API Constraints

### Polygon API
| Endpoint | Limit | Our Usage |
|----------|-------|-----------|
| Websocket (firehose) | 1 connection | 1 connection |
| REST (snapshots) | 50,000/day | ~200-500/day |

### Alpaca API (TA Pipeline)
| Plan | Limit | Our Usage |
|------|-------|-----------|
| Free | 200 req/min | ~4 req/min (batched) |

**CRITICAL: Multi-Symbol Pagination (v38c fix)**
Alpaca's multi-symbol bars endpoint (`/v2/stocks/bars`) paginates by **total bar count across all symbols**, NOT per-symbol:
- Requesting `limit=70` for 100 symbols returns ~70 bars TOTAL (1-2 symbols), not 70 per symbol
- Must follow `next_page_token` until all symbols have sufficient data
- Use `limit=10000` (API max) per request to minimize round-trips
- File: `adapters/alpaca_bars_batch.py:_fetch_bars_batch()`

**Batching Strategy:**
- 100 symbols per request (max supported)
- Uses SIP feed for full market coverage
- Pagination handles 300+ symbols in 1-2 pages with limit=10000

---

## CRITICAL: Live Trading Architecture (Verified 2026-02-05)

### Signal Flow
```
Polygon Firehose (T.* options trades)
    ↓
TradeAggregator (60s rolling window, per-symbol baselines from intraday_baselines_30m)
    ↓
SignalGenerator.create_signal_async()
    ├── Load TA from ta_daily_close (before 9:35 AM) or ta_snapshots_v2 (after 9:35 AM)
    ├── Fetch current price from Alpaca snapshot API (2s timeout)
    └── Assemble Signal object
    ↓
SignalFilter.apply() — 10 filter steps:
    1. ETF exclusion (hardcoded list)
    2. Score threshold (>= 10)
    3. RSI < 50 (adaptive: RSI < 60 on bounce-back days -- V29)
    4. Uptrend SMA20 (from TA cache)
    5. SMA50 momentum (from TA cache)
    6. Notional >= per-symbol baseline (intraday_baselines_30m DB)
    7. Crowded trade filter (vw_media_daily_features VIEW — mentions < 5, sentiment >= 0)
    8. Sector limit max 2 (master_tickers DB)
    9. Market regime (Alpaca API — SPY snapshot)
   10. Earnings proximity (earnings_calendar DB — reject if earnings within 2 days)
    ↓
If PASSED → Submit buy order via Alpaca API → Write to paper_trades_log, active_signals
         → Add symbol to tracked_tickers_v2 for intraday TA updates

Exit Rules:
    - Hard stop: -2% (USE_HARD_STOP=True, checked every 30s via Alpaca positions API)
    - EOD exit: 3:55 PM ET (all positions liquidated)
    - Dashboard update: every 30s (1 bulk Alpaca call → Google Sheets)
```

### Database Tables Read by Live Trading

| Table/View | Purpose |
|------------|---------|
| `vw_media_daily_features` | Crowded trade + sentiment filter |
| `master_tickers` | Sector concentration limit |
| `earnings_calendar` | Earnings proximity filter |
| `intraday_baselines_30m` | Per-symbol notional baselines |
| `ta_daily_close` | Prior-day TA (before 9:35 AM) |
| `ta_snapshots_v2` | Intraday TA (after 9:35 AM, 5-min refresh) |

### Database Tables Written by Live Trading

| Table | Purpose | Write Points |
|-------|---------|--------------|
| `active_signals` | Signals that passed all filters | INSERT on signal pass, UPDATE on trade placed + close |
| `paper_trades_log` | Executed trades with full lifecycle | INSERT on entry (`log_trade_open`), UPDATE on exit (`log_trade_close`) |
| `tracked_tickers_v2` | Symbols added for intraday TA updates | UPSERT on every UOA trigger |

### Crash-Resilient Trade Persistence (v45+)

On startup, `PositionManager.sync_on_startup()` performs 3-way reconciliation:

| Case | Condition | Action |
|------|-----------|--------|
| A: DB + Alpaca | Trade in `paper_trades_log` AND Alpaca position exists | Restore TradeRecord with signal metadata from DB, live price from Alpaca |
| B: DB only | Trade in DB but no Alpaca position | Mark closed as `crash_recovery` in DB |
| C: Alpaca only | Alpaca position but no DB record | Create new DB record (signal metadata zeroed) |

**Key files:**
- `paper_trading/dashboard.py` — `log_trade_open()`, `log_trade_close()`, `load_open_trades_from_db()`, `close_position()` (writes to Google Sheets "Closed Today" tab)
- `paper_trading/position_manager.py` — `sync_on_startup()`, wired DB writes in `open_position()` and `close_position()`

### Google Sheets Dashboard

Six tabs updated in real-time via `gspread` — three per account:

**Account A (default, no prefix):**

| Tab | Columns | Written By |
|-----|---------|------------|
| Active Signals | Date/Time, Symbol, Score, RSI, Ratio, Notional, Price, Action | `dashboard.log_signal()` |
| Positions | Symbol, Score, Entry, Current, P/L %, Status | `dashboard.update_position()` |
| Closed Today | Date/Time, Symbol, Score, Shares, Entry, Exit, P/L %, $ P/L, Result | `dashboard.close_position()` |

**Account B (prefixed tabs, engulfing-specific columns):**

| Tab | Columns | Written By |
|-----|---------|------------|
| Account B Signals | Date/Time, Symbol, Score, Engulfing, Notional, Price, VolR, Action | `dashboard_b.log_signal()` |
| Account B Positions | Symbol, Score, Entry, Current, P/L %, Status | `dashboard_b.update_position()` |
| Account B Closed | Date/Time, Symbol, Score, Shares, Entry, Exit, P/L %, $ P/L, Result | `dashboard_b.close_position()` |

- **Sheet ID**: `DASHBOARD_SHEET_ID` env var (set on Cloud Run service)
- **Credentials**: `dashboard-credentials` secret in Secret Manager
- **Daily reset**: `clear_daily()` clears all tabs and re-adds headers at market open
- **Tab prefix**: Account B uses `Dashboard(tab_prefix="Account B ")` — creates/finds worksheets with prefix
- **Backfill**: `scripts/backfill_closed_sheet.py` (Account A), `scripts/_backfill_signals.py` (Account B one-off)

### What's NOT in the Live Path (Yet)

| Item | Status |
|------|--------|
| `signal_evaluations` table | Designed but FR3_User lacks permissions |
| `spot_prices` table | Market regime uses Alpaca API directly |
| Phase detection | Code exists in `phase_detectors/` - NOT integrated yet |
| GEX calculations | **PLANNED** - code exists, to be added later |
| Greeks (Black-Scholes) | Code exists - NOT used in live trading |
| ORATS scanner | `identify_uoa_candidates.py` runs independently, does NOT feed live trader |

### Account B — Engulfing-Primary, V2 Score as Confirmation (A/B Test)

Parallel paper trading account where **engulfing pattern is the primary gate** and V2 score >= 10 is confirmation. Account B is **NOT a subset of Account A** — a symbol may fail Account A's RSI filter but still trade in Account B if it has engulfing + score >= 10.

**Signal flow:**
```
Pre-market / startup:
  Load daily bullish engulfing watchlist from engulfing_scores (timeframe='1D', last 20h)

During market hours:
  Aggregator fires UOA trigger for symbol
    → b_eligible? (position limits, not already traded)
    → score >= 10?
    → On engulfing watchlist (daily, last 20h) OR 5-min pattern (last 30min)?
      → YES → Account B trade (bypasses RSI, sentiment, earnings, etc.)
      → NO  → Account B skip
```

**Environment variables:**
| Variable | Purpose |
|----------|---------|
| `ALPACA_API_KEY_B` | Account B Alpaca API key |
| `ALPACA_SECRET_KEY_B` | Account B Alpaca secret key |

**Tables:**
| Table | Purpose |
|-------|---------|
| `engulfing_scores` | Written by DayTrading scanner (both 1D and 5min timeframes). UNIQUE on symbol, pattern_date, timeframe |
| `paper_trades_log_b` | Account B trade log (same schema as `paper_trades_log`) |

**Config (`paper_trading/config.py`):**
| Setting | Default | Purpose |
|---------|---------|---------|
| `USE_ACCOUNT_B` | `True` | Enable/disable Account B |
| `ENGULFING_LOOKBACK_MINUTES` | `30` | How recent the 5-min pattern must be (fallback) |
| `ENGULFING_DAILY_LOOKBACK_HOURS` | `20` | Daily patterns persist overnight |

**Key files:**
- `paper_trading/engulfing_checker.py` — Daily watchlist cache (O(1) lookup) + per-query 5-min fallback + `get_volume_ratio()`. `.strip()` on DB URL (critical for Cloud Run where secrets may have trailing `\r`)
- `paper_trading/position_manager.py` — `trades_table` and `skip_dashboard` params for Account B isolation
- `paper_trading/dashboard.py` — `tab_prefix` param creates prefixed worksheets. `log_signal()` uses Account B layout (Engulfing, VolR columns instead of RSI, Ratio). `table_name` param on `log_trade_open()`, `log_trade_close()`, `load_open_trades_from_db()` with `ALLOWED_TABLES` whitelist
- `paper_trading/main.py` — Account B evaluates BEFORE filter chain at aggregator level (score + engulfing only)

**Rollback:** Set `USE_ACCOUNT_B = False` in config.py, redeploy. Account A continues unaffected.

---

## Live Trading TA Data Sources (v37+)

The paper trading service uses different TA data sources based on time of day:

| Time | RSI/SMA20 Source | SMA50 Source | Notes |
|------|------------------|--------------|-------|
| Before 9:35 AM | `ta_daily_close` | `ta_daily_close` | Prior day close values |
| After 9:35 AM | `ta_snapshots_v2` | `ta_daily_close` | 5-min refresh for RSI/SMA20 |

**Key Files:**
- `paper_trading/signal_filter.py:SignalGenerator` - Smart TA lookup
- `scripts/ta_pipeline_v2.py` - Writes to `ta_snapshots_v2` every 5 min

**Prerequisite:** The TA pipeline job must be running during market hours:
```bash
python -m scripts.ta_pipeline_v2
```

---

## Project Structure

```
FL3_V2/
├── adapters/
│   ├── polygon_firehose.py      # Websocket client
│   ├── polygon_snapshot.py      # REST snapshot fetcher
│   └── alpaca_bars_batch.py     # Batched TA bars
├── analysis/
│   ├── baseline_manager.py      # Hybrid baseline logic
│   ├── greeks_calculator.py     # Black-Scholes implementation
│   ├── gex_aggregator.py        # GEX/DEX/Vanna/Charm
│   ├── ta_calculator.py         # RSI, ATR, VWAP
│   └── phase_scorer.py          # Phase detection scoring
├── firehose/
│   ├── client.py                # Websocket connection manager
│   ├── aggregator.py            # Rolling window aggregator
│   └── bucket_aggregator.py     # 30-min bucket storage
├── uoa/
│   ├── detector_v2.py           # UOA detection logic
│   └── trigger_handler.py       # On-trigger actions
├── phase_detectors/
│   ├── setup.py                 # Phase 1 detection
│   ├── acceleration.py          # Phase 2 detection
│   └── reversal.py              # Phase 3 detection
├── tracking/
│   └── ticker_manager_v2.py     # Permanent tracking
├── utils/
│   └── occ_parser.py            # OCC symbol parsing
├── scripts/
│   ├── firehose_main.py         # Main firehose orchestrator
│   ├── ta_pipeline_v2.py        # TA refresh orchestrator
│   └── refresh_baselines.py     # Daily baseline refresh
├── sql/
│   ├── create_tables_v2.sql     # Schema DDL
│   └── cleanup_legacy.sql       # V1 table cleanup
├── config/
│   └── time_multipliers.json    # Time-of-day config
├── tests/
│   ├── test_firehose_feasibility.py
│   ├── test_baseline_validation.py
│   └── test_gex_calculator.py
├── Dockerfile
├── requirements.txt
└── CLAUDE.md                    # This file
```

---

## OCC Symbol Parsing

Options symbols follow OCC format: `O:{UNDERLYING}{YYMMDD}{C/P}{STRIKE}`

Example: `O:AAPL250117C00150000`
- Underlying: `AAPL`
- Expiry: `2025-01-17`
- Right: `C` (Call)
- Strike: `$150.00`

```python
def parse_occ_symbol(symbol: str) -> dict:
    # Remove O: prefix
    s = symbol[2:] if symbol.startswith("O:") else symbol
    
    # Find where date starts (first digit after letters)
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    
    underlying = s[:i]
    date_str = s[i:i+6]
    right = s[i+6]
    strike = int(s[i+7:]) / 1000
    
    return {
        "underlying": underlying,
        "expiry": f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}",
        "right": "call" if right == "C" else "put",
        "strike": strike
    }
```

---

## Time Awareness (Application Runtime)

The firehose engine MUST verify time on startup:

```python
from datetime import datetime
import pytz

def get_market_status():
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    
    # Check if weekend
    if now.weekday() >= 5:
        return "CLOSED", "Weekend"
    
    # Check market hours
    market_open = now.replace(hour=9, minute=30, second=0)
    market_close = now.replace(hour=16, minute=0, second=0)
    
    if now < market_open:
        return "PRE_MARKET", f"Opens at 9:30 ET"
    elif now > market_close:
        return "AFTER_HOURS", f"Closed at 4:00 ET"
    else:
        mins_left = int((market_close - now).seconds / 60)
        return "OPEN", f"{mins_left} minutes until close"

# On startup
status, msg = get_market_status()
logger.info(f"Market Status: {status} - {msg}")

if status != "OPEN":
    logger.warning("Starting in test mode - market is closed")
```

---

## MCP Tools Usage

### Database (Shared with V1)
| Tool | Purpose |
|------|---------|
| `mcp__pg__pg_query_ro` | SELECT queries (max 5000 rows) |
| `mcp__pg__pg_exec_ddl` | DDL only (CREATE, ALTER, DROP) |

### GCP Operations
| Tool | Purpose |
|------|---------|
| `mcp__gcp_deploy__gcp_deploy_deploy_job_image` | Update Cloud Run job |
| `mcp__gcp_deploy__gcp_deploy_execute_job` | Run a job |
| `mcp__gcp_logs__gcp_logs_tail_job` | View job logs |

### Build (Use PowerShell)
```powershell
# From FL3_V2 directory
docker build -t us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2/firehose:v1 .
docker push us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2/firehose:v1
```

---

## Key SQL Queries

### Check ORATS freshness (shared data)
```sql
SELECT MAX(asof_date) as latest FROM orats_daily;
```

### Get baseline for symbol
```sql
SELECT bucket_start, AVG(prints) as avg_prints, AVG(notional) as avg_notional
FROM intraday_baselines_30m
WHERE symbol = 'AAPL' AND trade_date > CURRENT_DATE - 20
GROUP BY bucket_start
ORDER BY bucket_start;
```

### Recent UOA triggers
```sql
SELECT symbol, trigger_ts, trigger_type, volume_ratio, notional
FROM uoa_triggers_v2
WHERE trigger_ts > NOW() - INTERVAL '1 hour'
ORDER BY trigger_ts DESC;
```

### GEX snapshot for symbol
```sql
SELECT symbol, snapshot_ts, spot_price, net_gex, net_dex, 
       gamma_flip_level, call_wall_strike, put_wall_strike
FROM gex_metrics_snapshot
WHERE symbol = 'TSLA'
ORDER BY snapshot_ts DESC
LIMIT 10;
```

### Tracked symbols count
```sql
SELECT COUNT(*), 
       COUNT(*) FILTER (WHERE ta_enabled) as ta_active
FROM tracked_tickers_v2;
```

### TA coverage check
```sql
SELECT 
    t.symbol,
    MAX(ta.snapshot_ts) as last_ta,
    NOW() - MAX(ta.snapshot_ts) as staleness
FROM tracked_tickers_v2 t
LEFT JOIN ta_snapshots_v2 ta ON ta.symbol = t.symbol
WHERE t.ta_enabled = TRUE
GROUP BY t.symbol
HAVING MAX(ta.snapshot_ts) < NOW() - INTERVAL '10 minutes'
ORDER BY staleness DESC;
```

---

## Known Gotchas

1. **V1 Dependencies**: NEVER disable V1's `orats_ingest` or `price_ingest` — V2 depends on shared tables
2. **Table Names**: Always use `_v2` suffix for new tables to avoid conflicts
3. **Time Zones**: Polygon timestamps are UTC, market hours are ET — always convert
4. **OCC Parsing**: Some symbols have variable-length underlyings (e.g., `BRKB` vs `A`)
5. **MCP Limits**: `pg_query_ro` max 5000 rows — use LIMIT or aggregations
6. **Firehose Reconnects**: Websocket may disconnect — implement auto-reconnect with backoff
7. **ORATS Timing**: `asof_date` is T-1 (yesterday's data arrives after close)
8. **TA Storage**: 78K rows/day at 5-min intervals — plan for ~2.8 GB/year
9. **GCP Secret trailing whitespace**: `DATABASE_URL` secret has trailing `\r\r`. Any `psycopg2.connect()` call MUST `.strip()` the URL. asyncpg is unaffected (trims internally). Check with `gcloud secrets versions access ... | cat -A`
10. **Cloud Run traffic pinning**: If traffic was pinned to a specific revision name, `gcloud run deploy` creates new revisions but they're immediately retired. Fix: `gcloud run services update-traffic SERVICE --to-latest`
11. **engulfing_strength is text, not numeric**: `engulfing_scores.pattern_strength` is a string enum (`strong`/`moderate`/`weak`). Don't use float format codes on it

---

## V1 Coexistence Rules

| Rule | Description |
|------|-------------|
| Shared DB | Same PostgreSQL instance, different tables |
| Shared Data | `orats_daily`, `orats_daily_returns`, `spot_prices` |
| V1 Active | ORATS ingest, price ingest MUST stay running |
| V1 Disabled | UOA detection, wave engine, TA refresh (replaced by V2) |
| Cleanup | Only drop tables after V1 validation complete |

---

## Development Workflow

1. **Start session**: Check date/time and market status
2. **Check shared data**: Verify ORATS and spot_prices are fresh
3. **Test locally**: Use `python -m scripts.firehose_main --test-mode`
4. **MANDATORY: Check context size before build** (see below)
5. **Build image**: `docker build -t ... .`
6. **Deploy**: Update Cloud Run job with new image
7. **Monitor**: Check logs for errors, verify triggers are reasonable

---

## MANDATORY: Pre-Deployment Size Check

**ALWAYS check Docker context size before building/deploying.** The repository contains large local data files that MUST NOT be uploaded.

### Check Command
```bash
# Check total directory size
du -sh C:/Users/levir/Documents/FL3_V2

# Should be < 10MB after .dockerignore exclusions
# If > 50MB, something is wrong - investigate!
```

### Expected Sizes
| Directory | Expected | Notes |
|-----------|----------|-------|
| Total repo | ~27GB | Contains polygon_data, backups |
| Docker context | < 5MB | After .dockerignore exclusions |
| Built image | ~260MB | Python 3.11 slim + deps |

### Common Issues
| File/Dir | Size | Fix |
|----------|------|-----|
| `polygon_data/` | ~22GB | Already in .dockerignore |
| `backups/` | ~4.7GB | Already in .dockerignore |
| `nul` | Variable | Windows artifact - delete if exists |
| `*.csv.gz` | ~50MB+ | Data files - in .dockerignore |
| Root `*.py` scripts | ~55 files | Dev scripts - in .dockerignore |

### CRITICAL: Two Ignore Files Required

| File | Used By | Purpose |
|------|---------|---------|
| `.dockerignore` | `docker build` (local) | Excludes files from Docker context |
| `.gcloudignore` | `gcloud builds submit` | Excludes files from Cloud Build upload |

**Both files must be kept in sync!** If you update one, update the other.

### If Build Takes > 2 Minutes
1. Stop the build immediately (Ctrl+C)
2. Check what's being uploaded: `du -sh */ | sort -rh | head -10`
3. Update BOTH `.dockerignore` AND `.gcloudignore`
4. Verify locally with: `docker build --no-cache .` (watch context size)
5. Verify gcloud with first line of output: `Creating temporary archive of X file(s) totalling Y`

**DO NOT proceed with deployment if context upload exceeds 1MB (should be ~700KB).**

---

## Error Handling Patterns

### Firehose Disconnect
```python
async def run_firehose():
    while True:
        try:
            await connect_and_process()
        except websockets.ConnectionClosed:
            logger.warning("Connection lost, reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Firehose error: {e}")
            await asyncio.sleep(10)
```

### Snapshot Rate Limit
```python
async def fetch_snapshot_with_retry(symbol: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await polygon_client.get_snapshot(symbol)
        except RateLimitError:
            wait = 2 ** attempt
            logger.warning(f"Rate limited, waiting {wait}s...")
            await asyncio.sleep(wait)
    raise Exception(f"Failed to fetch snapshot for {symbol}")
```

### Missing Baseline
```python
def get_baseline(symbol: str, bucket: str) -> float:
    # Try bucket history first
    baseline = db.query("""
        SELECT AVG(notional) FROM intraday_baselines_30m
        WHERE symbol = %s AND bucket_start = %s
        AND trade_date > CURRENT_DATE - 20
    """, (symbol, bucket))
    
    if baseline:
        return baseline
    
    # Fall back to ORATS
    orats = db.query("""
        SELECT total_volume FROM orats_daily
        WHERE symbol = %s ORDER BY asof_date DESC LIMIT 1
    """, (symbol,))
    
    if orats:
        return (orats.total_volume / 390) * TIME_MULTIPLIERS[bucket]
    
    # No data at all — use conservative default
    logger.warning(f"No baseline for {symbol}, using default")
    return 1000  # Conservative default
```

---

## Monitoring Checklist

### Daily
- [ ] ORATS data loaded (check `MAX(asof_date)`)
- [ ] Firehose connected and processing
- [ ] UOA triggers within expected range (50-500/day)
- [ ] TA pipeline completing within 60s

### Weekly  
- [ ] Review false positive rate
- [ ] Check baseline correlation vs actual volume
- [ ] Verify GEX calculations against known sources
- [ ] Storage growth within estimates

### Monthly
- [ ] Tune detection thresholds based on backtest results
- [ ] Review phase detection accuracy
- [ ] Database maintenance (VACUUM ANALYZE)

---

## Pipeline Health Check

Comprehensive test suite for validating the entire V2 pipeline.

### Running the Health Check

**Local (recommended):**
```bash
# Requires Cloud SQL Auth Proxy running on localhost:5433
python -m tests.pipeline_health_check
```

**On Cloud Run:**
```bash
gcloud run jobs execute fl3-v2-health-check --region=us-west1 --wait
```

### Prerequisites for Local Testing

1. **Cloud SQL Auth Proxy** must be running:
   ```bash
   cloud_sql_proxy -instances=spartan-buckeye-474319-q8:us-west1:fr3-pg=tcp:5433
   ```

2. **GCP credentials** - `gcloud auth login` completed

3. **Alpaca credentials** - Set via environment or GCP secrets

### Environment Variables

| Variable | Purpose | Auto-detected |
|----------|---------|---------------|
| `DATABASE_URL` | Cloud SQL socket (for Cloud Run) | Yes - transforms to TCP locally |
| `DATABASE_URL_LOCAL` | TCP connection for local testing | Optional override |
| `ALPACA_API_KEY` | Alpaca API key | From GCP secrets |
| `ALPACA_SECRET_KEY` | Alpaca secret | From GCP secrets |
| `GOOGLE_CLOUD_PROJECT` | GCP project (default: fl3-v2-prod) | Yes |

### Test Sections (19 tests)

| Section | Tests | What It Checks |
|---------|-------|----------------|
| GCP Infrastructure | 1.1-1.4 | Scheduler jobs, Cloud Run service, errors |
| Data Freshness | 2.1-2.7 | TA cache, baselines, earnings, tickers |
| Tracking Pipeline | 3.1-3.3 | Symbol tracking, TA coverage |
| Signal Filtering | 4.1-4.2 | Filter chain, sentiment data |
| Alpaca Integration | 5.1-5.3 | Connection, buying power, trades |

### How Local DB Access Works

The script auto-detects Windows environment and transforms the Cloud SQL socket URL to TCP:

```
Cloud Run:  postgresql://...?host=/cloudsql/project:region:instance
Local:      postgresql://FR3_User:***@127.0.0.1:5433/fl3
```

This requires Cloud SQL Auth Proxy running on port 5433.

### Expected Results

| Status | Meaning |
|--------|---------|
| PASS | Test passed |
| WARN | Minor issue (e.g., stale data but still usable) |
| FAIL | Critical issue requiring attention |
| SKIP | Test skipped (e.g., outside market hours) |

### Typical Warnings (Not Failures)

- **Baselines X days old** - OK if within 20-day window
- **None added recently to tracking** - OK if v37 just deployed
- **No trades today** - OK if market closed
