# Claude Code Project Context — FL3_V2

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

**Batching Strategy:**
- 50-100 symbols per bars request
- 1,000 symbols ÷ 50 = 20 calls per refresh
- 5-min refresh = 4 calls/min average

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
4. **Build image**: `docker build -t ... .`
5. **Deploy**: Update Cloud Run job with new image
6. **Monitor**: Check logs for errors, verify triggers are reasonable

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
