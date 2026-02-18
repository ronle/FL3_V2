# Agent 2: FL3_V2 Account B Integration

## Context

You are working in the FL3_V2 repo (`C:\Users\levir\Documents\FL3_V2`).

FL3_V2 is a live paper trading system that detects unusual options activity (UOA) via Polygon firehose and trades stocks through Alpaca. We are adding a **second parallel Alpaca paper trading account** (Account B) that only takes trades when a confirming engulfing candlestick pattern exists on the 5-minute chart.

- **Account A (existing, no changes):** Current V2 paper trading — all signals that pass the 10-filter chain get traded
- **Account B (new):** Same filter chain, but adds one more gate — the symbol must have a bullish engulfing pattern detected on the 5-min timeframe within the last 30 minutes

A separate agent (working in the DayTrading repo) writes detected patterns to the `engulfing_scores` database table every 5 minutes during market hours. Your job is to **read from that table and route qualifying signals to Account B**.

---

## Interface Contract

A separate agent writes to this table. You read from it. This is the only integration point.

```sql
CREATE TABLE engulfing_scores (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    scan_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timeframe TEXT NOT NULL DEFAULT '5min',
    direction TEXT NOT NULL,                     -- 'bullish' or 'bearish'
    pattern_date TIMESTAMPTZ NOT NULL,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    target_1 NUMERIC,
    target_2 NUMERIC,
    pattern_strength TEXT,                       -- 'strong', 'moderate', 'weak'
    body_ratio NUMERIC,
    range_ratio NUMERIC,
    candle_range NUMERIC,
    volume_confirmed BOOLEAN,
    score NUMERIC(5,4),                         -- NULL for now, reserved for future
    UNIQUE(symbol, pattern_date, timeframe)
);
```

Your query to check for engulfing confirmation:

```sql
SELECT symbol, direction, pattern_date, pattern_strength, scan_ts
FROM engulfing_scores
WHERE symbol = %s
  AND direction = 'bullish'
  AND scan_ts > NOW() - INTERVAL '30 minutes'
ORDER BY scan_ts DESC
LIMIT 1;
```

**Direction is always 'bullish'** because V2 only buys stocks (no shorting). A bullish engulfing pattern confirms the V2 buy signal.

**30-minute lookback** — the engulfing scanner runs every 5 minutes. A 30-minute window means the pattern must have been detected within the last 6 scan cycles. This ensures the pattern and the UOA trigger are roughly coincident.

---

## What to Build

### 1. New DB table: `paper_trades_log_b`

Clone from the existing `paper_trades_log`:

```sql
CREATE TABLE paper_trades_log_b (LIKE paper_trades_log INCLUDING ALL);
```

This keeps Account A and Account B data completely separated. Zero risk of corrupting existing tracking.

### 2. New file: `paper_trading/engulfing_checker.py`

A simple module that checks `engulfing_scores` for a given symbol.

**Architecture:** Unlike the earnings and sector caches (which load once at startup), the engulfing data is intraday and changes every 5 minutes. Two approaches:

**Option A (recommended): Query per-signal with connection pooling.**

The engulfing check happens only when a signal passes all 10 filters — this is maybe 5-20 times per day. A single indexed query per signal is negligible. Use a simple function:

```python
def has_engulfing_confirmation(db_url: str, symbol: str, lookback_minutes: int = 30) -> tuple[bool, dict | None]:
    """
    Check if symbol has a recent bullish engulfing pattern.
    
    Returns:
        (True, {"pattern_strength": "strong", "scan_ts": ..., "pattern_date": ...}) or
        (False, None)
    """
```

**Option B: Periodic cache refresh.**

Every 5 minutes (aligned with scanner), bulk-load all patterns from the last 30 minutes into a dict. Per-signal lookup is a dict check. More complex but avoids any DB latency in the signal path.

Go with Option A unless latency becomes an issue. The signal path already makes DB calls (earnings, sentiment) so one more indexed query is fine.

### 3. Modified: `paper_trading/config.py`

Add Account B configuration to `TradingConfig`:

```python
# Account B — V2 + Engulfing Pattern
USE_ACCOUNT_B: bool = True
ENGULFING_LOOKBACK_MINUTES: int = 30    # How recent the 5-min pattern must be
```

No score threshold needed — we're checking pattern presence, not score.

### 4. Modified: `paper_trading/position_manager.py`

Make the trades table configurable. Currently, `PositionManager` reads/writes `paper_trades_log` hardcoded. Change it to accept a table name parameter:

```python
class PositionManager:
    def __init__(self, trader, config, trades_table="paper_trades_log"):
        self.trades_table = trades_table
        ...
```

Then use `self.trades_table` everywhere `paper_trades_log` is currently hardcoded.

**Important:** The actual DB write functions are in `dashboard.py` (`log_trade_open`, `log_trade_close`, `load_open_trades_from_db`). The `trades_table` parameter needs to flow through to wherever the actual SQL is executed. Check both `position_manager.py` and `dashboard.py` for hardcoded table references.

### 5. Modified: `paper_trading/main.py`

This is the main integration point.

#### 5a. Initialize Account B components

In `PaperTradingEngine.__init__()`:

```python
# Account B — V2 + Engulfing Pattern
if config.USE_ACCOUNT_B:
    alpaca_api_key_b = os.environ.get("ALPACA_API_KEY_B")
    alpaca_secret_key_b = os.environ.get("ALPACA_SECRET_KEY_B")
    
    if alpaca_api_key_b and alpaca_secret_key_b:
        self.trader_b = AlpacaTrader(alpaca_api_key_b, alpaca_secret_key_b, config)
        self.position_manager_b = PositionManager(
            self.trader_b, config, trades_table="paper_trades_log_b"
        )
        self.engulfing_checker = EngulfingChecker(database_url=db_url)
        self.eod_closer_b = EODCloser(
            self.position_manager_b, config,
            on_close_complete=self._on_eod_complete_b,
        )
        self._account_b_enabled = True
        logger.info("Account B (V2 + Engulfing) initialized")
    else:
        self._account_b_enabled = False
        logger.warning("Account B keys not found, Account B disabled")
```

#### 5b. Route signals to Account B

Find the location in the main signal processing flow where a signal has passed all filters and Account A's order is being submitted. After the Account A trade submission, add:

```python
# Existing: Account A trade submission (NO CHANGES)
# ...

# NEW: Account B — check engulfing confirmation
if self._account_b_enabled:
    has_engulfing, engulfing_data = self.engulfing_checker.has_engulfing_confirmation(
        symbol=signal.symbol,
        lookback_minutes=self.config.ENGULFING_LOOKBACK_MINUTES,
    )
    
    if has_engulfing:
        logger.info(
            f"ACCOUNT B TRADE: {signal.symbol} score={signal.score} "
            f"engulfing_strength={engulfing_data['pattern_strength']} "
            f"engulfing_age={engulfing_data['age_minutes']:.0f}min"
        )
        await self.position_manager_b.open_position(signal)
    else:
        logger.info(
            f"ACCOUNT B SKIP: {signal.symbol} — no bullish engulfing in last "
            f"{self.config.ENGULFING_LOOKBACK_MINUTES}min"
        )
```

**Critical:** Account A and Account B have independent position limits. Account B having a position in AAPL does not count against Account A's slot limit, and vice versa.

#### 5c. Position monitoring for Account B

The existing 30-second position monitoring loop must also cover Account B:

```python
# In the position monitoring loop:
await self.position_manager.check_positions()       # Account A (existing)

if self._account_b_enabled:                          # Account B (new)
    await self.position_manager_b.check_positions()
```

This covers hard stop checks (-2%) for Account B positions.

#### 5d. EOD close for Account B

Create a second `EODCloser` instance (initialized in 5a). Both must run:

```python
# In the main run loop where EOD closing is triggered:
# Account A EOD close (existing)
await self.eod_closer.check_and_close()

# Account B EOD close (new)
if self._account_b_enabled:
    await self.eod_closer_b.check_and_close()
```

#### 5e. Daily reset for Account B

In the daily reset logic:

```python
# Existing resets
self.signal_filter.reset_stats()

# NEW: Account B daily reset
if self._account_b_enabled:
    self.position_manager_b.reset_daily()
```

### 6. Modified: `paper_trading/dashboard.py`

The DB logging functions (`log_trade_open`, `log_trade_close`, `load_open_trades_from_db`) need to support a configurable table name. Currently they hardcode `paper_trades_log` in the SQL.

Add a `table_name` parameter (default `"paper_trades_log"` for backward compatibility):

```python
def log_trade_open(db_url, symbol, ..., table_name="paper_trades_log"):
    cur.execute(f"""
        INSERT INTO {table_name} (...)
        VALUES (...)
    """, ...)
```

Account A calls pass no `table_name` (uses default). Account B calls pass `table_name="paper_trades_log_b"`.

**Google Sheets:** For now, don't add Account B to Google Sheets. The DB logs are sufficient for the A/B comparison. Sheets integration can be added later if needed.

### 7. SQL files

**`sql/create_engulfing_scores.sql`:**
```sql
-- Engulfing pattern detections (written by DayTrading scanner, read by V2 live trader)
CREATE TABLE IF NOT EXISTS engulfing_scores (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    scan_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timeframe TEXT NOT NULL DEFAULT '5min',
    direction TEXT NOT NULL,
    pattern_date TIMESTAMPTZ NOT NULL,
    entry_price NUMERIC,
    stop_loss NUMERIC,
    target_1 NUMERIC,
    target_2 NUMERIC,
    pattern_strength TEXT,
    body_ratio NUMERIC,
    range_ratio NUMERIC,
    candle_range NUMERIC,
    volume_confirmed BOOLEAN,
    score NUMERIC(5,4),
    UNIQUE(symbol, pattern_date, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_engulfing_scores_symbol_ts
    ON engulfing_scores(symbol, scan_ts DESC);
CREATE INDEX IF NOT EXISTS idx_engulfing_scores_live_lookup
    ON engulfing_scores(symbol, direction, scan_ts);
```

**`sql/create_paper_trades_b.sql`:**
```sql
-- Account B trade log (V2 + Engulfing Pattern)
CREATE TABLE IF NOT EXISTS paper_trades_log_b (LIKE paper_trades_log INCLUDING ALL);
```

### 8. Modified: `CLAUDE.md`

Add under the "Live Trading Architecture" section:

```markdown
### Account B — V2 + Engulfing Pattern (A/B Test)

Parallel paper trading account that requires engulfing pattern confirmation.

Signal flow:
  Signal passes all 10 filters → Account A trade (existing, unchanged)
                                → Check engulfing_scores table for bullish pattern in last 30min
                                  → If found → Account B trade
                                  → Else → Account B skip (logged)

Environment variables:
  ALPACA_API_KEY_B, ALPACA_SECRET_KEY_B — Account B Alpaca credentials

New tables read:
  engulfing_scores — Written by DayTrading 5-min scanner every 5 min during market hours

New tables written:
  paper_trades_log_b — Account B trade log (same schema as paper_trades_log)

Config:
  USE_ACCOUNT_B (bool) — enable/disable Account B
  ENGULFING_LOOKBACK_MINUTES (int, default 30) — how recent the pattern must be
```

---

## Comparison Query

After collecting enough data (50+ Account B trades):

```sql
WITH a AS (
    SELECT
        COUNT(*) as trades,
        AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
        AVG(pnl_pct) as avg_pnl_pct,
        SUM(pnl) as total_pnl
    FROM paper_trades_log
    WHERE exit_time IS NOT NULL
),
b AS (
    SELECT
        COUNT(*) as trades,
        AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
        AVG(pnl_pct) as avg_pnl_pct,
        SUM(pnl) as total_pnl
    FROM paper_trades_log_b
    WHERE exit_time IS NOT NULL
)
SELECT
    'Account A' as account, a.trades, ROUND(a.win_rate * 100, 1) as wr_pct,
    ROUND(a.avg_pnl_pct * 100, 2) as avg_pnl_pct, ROUND(a.total_pnl, 2) as total_pnl
FROM a
UNION ALL
SELECT
    'Account B', b.trades, ROUND(b.win_rate * 100, 1),
    ROUND(b.avg_pnl_pct * 100, 2), ROUND(b.total_pnl, 2)
FROM b;
```

---

## Deployment

### Environment Variables (GCP Secret Manager)

Add these new secrets:

| Secret | Purpose |
|--------|---------|
| `ALPACA_API_KEY_B` | Account B API key |
| `ALPACA_SECRET_KEY_B` | Account B secret key |

Mount both on the Cloud Run service alongside existing secrets.

### Pre-Flight Checklist

- [ ] `engulfing_scores` table exists in PostgreSQL
- [ ] `paper_trades_log_b` table created
- [ ] Account B Alpaca credentials in Secret Manager
- [ ] Account B paper account has sufficient buying power
- [ ] Verify engulfing scanner is writing to `engulfing_scores` (check rows exist)
- [ ] Test in dry-run: verify Account B would route signals correctly
- [ ] Verify EOD closer closes both accounts
- [ ] Verify crash recovery works for both accounts
- [ ] Monitor first live day — check both accounts are getting fills

### Rollback

If Account B causes any issues:
1. Set `USE_ACCOUNT_B = False` in config
2. Redeploy
3. Account A continues unaffected

All Account B logic is gated behind `self._account_b_enabled`. If disabled or if keys are missing, the system runs exactly as before.

---

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `paper_trading/engulfing_checker.py` | CREATE | Query engulfing_scores for pattern confirmation |
| `paper_trading/config.py` | MODIFY | Add USE_ACCOUNT_B, ENGULFING_LOOKBACK_MINUTES |
| `paper_trading/position_manager.py` | MODIFY | Configurable trades_table parameter |
| `paper_trading/dashboard.py` | MODIFY | Support configurable table_name in log functions |
| `paper_trading/main.py` | MODIFY | Account B init, signal routing, monitoring, EOD, reset |
| `sql/create_engulfing_scores.sql` | CREATE | DDL for shared scores table |
| `sql/create_paper_trades_b.sql` | CREATE | DDL for Account B trade log |
| `CLAUDE.md` | MODIFY | Document Account B architecture |

---

## Critical Constraints

1. **DO NOT modify Account A behavior.** All Account B logic is additive — gated behind `USE_ACCOUNT_B` and `_account_b_enabled`. If Account B code is removed, Account A must work identically to before.

2. **Account B is a strict subset.** Every Account B trade must also be an Account A trade. If Account B takes a trade that Account A didn't, something is wrong.

3. **Independent position tracking.** Account A and B have separate Alpaca accounts, separate position counts, separate buying power. They don't interfere with each other.

4. **The `engulfing_scores` table may be empty.** If the scanner hasn't run yet, Account B simply takes zero trades. Don't error out — log and skip.

5. **Follow existing patterns.** The engulfing checker should be a simple module like the existing filter helpers. The dual-trader pattern should mirror how `AlpacaTrader` and `PositionManager` are already initialized. Don't introduce new architectural patterns.
