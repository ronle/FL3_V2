# V2 Pipeline Entry Points
**Generated**: 2026-02-04

## Overview

This document maps every V2 code path, confirming the actual live trading dependencies and identifying all reads/writes.

**V2 Location**: `C:\Users\levir\Documents\FL3_V2`

---

## CRITICAL FINDING: Actual Live Trading Dependencies

**V2 paper-trading-live reads ONLY 2 database tables** (not 5 as previously documented):

| Table | Filter Step | File:Line | Status |
|-------|-------------|-----------|--------|
| `sentiment_daily` | Crowded trade filter | signal_filter.py:238 | READ |
| `master_tickers` | Sector concentration | signal_filter.py:68 | READ |

**Tables NOT used in live trading path:**
| Table | Reason |
|-------|--------|
| `ta_daily_close` | TA comes from Polygon API + JSON cache |
| `earnings_calendar` | Earnings filter NOT integrated |
| `spot_prices` | Uses Alpaca API directly |
| `intraday_baselines_30m` | Hardcoded $50K default |

---

## Live Trading Path (paper-trading-live service)

### Signal Flow

```
Polygon Firehose (T.* options trades)
    ↓
firehose/client.py - Stream trades
    ↓
paper_trading/main.py:479-491 - _process_trade()
    ↓
paper_trading/trade_aggregator.py - RollingAggregator (60s window)
    ↓
paper_trading/main.py:268-340 - _check_for_signals()
    ↓
paper_trading/signal_filter.py:36 - SignalGenerator.create_signal_async()
    ├── Load TA from JSON cache (line 299)
    ├── Fetch missing TA from Polygon API (line 605)
    └── Assemble Signal object
    ↓
paper_trading/signal_filter.py:289 - SignalFilter.apply()
    ├── Check ETF exclusion (line 299)
    ├── Check score threshold (line 308)
    ├── Check trend/RSI (lines 313-327)
    ├── Check 50d SMA (line 330)
    ├── Check notional (line 336)
    ├── Check sentiment_daily (line 341) ← DB READ
    ├── Log to signal_evaluations (line 355) ← DB WRITE
    └── If PASSED:
        ├── Log to active_signals (line 366) ← DB WRITE
        └── Open position
            ├── Check master_tickers sector limit (line 332) ← DB READ
            ├── Check market regime via Alpaca API (line 337)
            ├── Submit buy order to Alpaca (line 373)
            ├── Log to paper_trades_log (line 427) ← DB WRITE
            └── Update signal_evaluations (line 445) ← DB WRITE
```

---

## Database Operations — Exact File:Line References

### Tables READ (2 total)

#### 1. sentiment_daily (Crowded Trade Filter)

| Property | Value |
|----------|-------|
| **Location** | `signal_filter.py:238` |
| **Function** | `SignalFilter._get_sentiment_data()` |
| **Query** | `SELECT mentions_total, sentiment_index FROM sentiment_daily WHERE ticker = %s AND asof_date = %s` |
| **Called From** | signal_filter.py:273 during filter evaluation |

```python
# signal_filter.py:236-240
cur.execute("""
    SELECT mentions_total, sentiment_index
    FROM sentiment_daily
    WHERE ticker = %s AND asof_date = %s
""", (symbol, prior_date))
```

**Logic**: Uses T-1 (prior day) sentiment data
- FAIL if mentions_total >= 5 (crowded)
- FAIL if sentiment_index < 0 (negative sentiment)

#### 2. master_tickers (Sector Concentration)

| Property | Value |
|----------|-------|
| **Location** | `signal_filter.py:67-70` |
| **Function** | `get_sector_for_symbol()` |
| **Query** | `SELECT sector FROM master_tickers WHERE symbol = %s AND sector IS NOT NULL` |
| **Called From** | position_manager.py:128 in `would_exceed_sector_limit()` |

```python
# signal_filter.py:67-70
cur.execute("""
    SELECT sector FROM master_tickers
    WHERE symbol = %s AND sector IS NOT NULL
""", (symbol,))
```

**Logic**: V28 feature — max 2 concurrent positions per sector

---

### Tables WRITTEN (3 total)

#### 1. signal_evaluations (All Evaluated Signals)

| Property | Value |
|----------|-------|
| **Purpose** | Log EVERY signal (passed + filtered) for analysis |
| **INSERT** | signal_filter.py:174-204 |
| **UPDATE** | position_manager.py:444-449 |

```python
# signal_filter.py:174 - INSERT
cur.execute("""
    INSERT INTO signal_evaluations (
        symbol, detected_at, notional, ratio, call_pct, sweep_pct,
        num_strikes, contracts, rsi_14, macd_histogram, trend,
        score_volume, score_call_pct, score_sweep, score_strikes,
        score_notional, score_total, passed_all_filters, rejection_reason
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s)
""", (...))

# position_manager.py:444-449 - UPDATE
cur.execute("""
    UPDATE signal_evaluations
    SET trade_placed = TRUE, entry_price = %s
    WHERE symbol = %s AND DATE(detected_at) = CURRENT_DATE
    AND passed_all_filters = TRUE AND trade_placed = FALSE
""", (trade.entry_price, trade.symbol))
```

#### 2. active_signals (Passed Signals Only) — v45 cross-day fix

| Property | Value |
|----------|-------|
| **Purpose** | Track signals that PASSED filters |
| **INSERT** | dashboard.py:313-323 |
| **UPDATE (trade placed)** | dashboard.py:`update_signal_trade_placed()` — uses subquery (v45 cross-day fix) |
| **UPDATE (closed)** | dashboard.py:`close_signal_in_db()` — uses subquery (v45 cross-day fix) |

```python
# dashboard.py:313-323 - INSERT
cur.execute("""
    INSERT INTO active_signals (
        detected_at, symbol, notional, ratio, call_pct, sweep_pct,
        num_strikes, contracts, rsi_14, trend, price_at_signal,
        score, action
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (detected_at, symbol) DO NOTHING
""", (...))
```

#### 3. paper_trades_log (Executed Trades) — v45 crash-resilient

| Property | Value |
|----------|-------|
| **Purpose** | Full trade lifecycle with crash recovery |
| **INSERT (entry)** | dashboard.py:`log_trade_open()` — called from position_manager.py:`open_position()` |
| **UPDATE (exit)** | dashboard.py:`log_trade_close()` — called from position_manager.py:`close_position()` |
| **SELECT (startup)** | dashboard.py:`load_open_trades_from_db()` — called from position_manager.py:`sync_on_startup()` |
| **Index** | `idx_paper_trades_log_open` — partial index on `(symbol) WHERE exit_time IS NULL` |

```python
# dashboard.py:log_trade_open() - INSERT with RETURNING id
cur.execute("""
    INSERT INTO paper_trades_log
    (symbol, entry_time, entry_price, shares, signal_score, signal_rsi, signal_notional)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id
""", (...))

# dashboard.py:log_trade_close() - UPDATE by trade_db_id (precise targeting)
cur.execute("""
    UPDATE paper_trades_log
    SET exit_time = %s, exit_price = %s, pnl = %s, pnl_pct = %s, exit_reason = %s
    WHERE id = %s
""", (...))
# Fallback if trade_db_id unavailable: WHERE symbol = %s AND exit_time IS NULL

# dashboard.py:load_open_trades_from_db() - SELECT for startup recovery
cur.execute("""
    SELECT id, symbol, entry_time, entry_price, shares,
           signal_score, signal_rsi, signal_notional
    FROM paper_trades_log
    WHERE exit_time IS NULL AND created_at > CURRENT_DATE - 7
""")
```

**Startup 3-way reconciliation** (`position_manager.py:sync_on_startup()`):
- Case A (DB + Alpaca): Restore with full signal metadata from DB
- Case B (DB only): Mark closed as `crash_recovery`
- Case C (Alpaca only): Create DB record with zeroed metadata

---

## Non-Live Paths (Scheduled Jobs)

### fl3-v2-ta-pipeline

| Property | Value |
|----------|-------|
| **Image** | fl3-v2:v3 |
| **Script** | `scripts/ta_pipeline_v2.py` |
| **Schedule** | Every 5 min during RTH |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `ta_daily_close` | INSERT/UPSERT |
| `ta_snapshots_v2` | INSERT |

**External API:** Alpaca (stock bars)

---

### update-spot-prices

| Property | Value |
|----------|-------|
| **Image** | fl3-v2-jobs:v1.1 |
| **Script** | `scripts/update_spot_prices.py` |
| **Schedule** | Every 1 min during RTH |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `spot_prices` | INSERT/UPSERT |

**External API:** Alpaca (latest quote)

**Note:** spot_prices is NOT read by paper-trading-live (uses Alpaca API directly)

---

### fetch-earnings-calendar

| Property | Value |
|----------|-------|
| **Image** | fl3-v2-jobs:v1.2-fmp |
| **Script** | `scripts/fetch_earnings_calendar.py` |
| **Schedule** | Daily 4 AM |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `earnings_calendar` | INSERT/UPSERT |

**External API:** FMP (earnings endpoint)

**Note:** earnings_calendar is NOT read by paper-trading-live (filter not integrated)

---

### premarket-ta-cache

| Property | Value |
|----------|-------|
| **Image** | paper-trading:v28+ |
| **Script** | `paper_trading/premarket_ta_cache.py` |
| **Schedule** | Daily 6 AM ET (before market open) |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `ta_daily_close` | INSERT/UPSERT with sma_50 |

**Output:** Also writes JSON cache file for paper-trading-live

```python
# premarket_ta_cache.py:246
cur.execute("""
    INSERT INTO ta_daily_close
    (symbol, trade_date, rsi_14, macd, macd_signal, macd_histogram, sma_20, ema_9, close_price, sma_50)
    VALUES %s
    ON CONFLICT (symbol, trade_date) DO UPDATE SET ...
""")
```

---

### refresh-sector-data

| Property | Value |
|----------|-------|
| **Image** | paper-trading:v28 |
| **Script** | `scripts/refresh_sector_data.py` |
| **Schedule** | Weekly |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `master_tickers` | UPDATE sector column |

---

## Confirmed: Baselines NOT Used

**Search Results:**
```
$ grep -r "load_baselines|intraday_baselines" paper_trading/
# (No results in paper_trading/)
```

**Code Evidence** (trade_aggregator.py:120-130):
```python
def get_baseline(self, symbol: str) -> float:
    """Get baseline notional for a symbol."""
    return self._baselines.get(symbol, 50_000)  # Hardcoded 50K default

def load_baselines(self, baselines: Dict[str, float]):
    """Load baselines from external source."""
    self._baselines.update(baselines)
    # BUT THIS IS NEVER CALLED
```

**Conclusion:** `intraday_baselines_30m` table is populated but NEVER consumed by live trading.

---

## Confirmed: TA From Polygon API, Not Database

**How TA is used in paper_trading:**
1. Loaded from JSON cache file: `polygon_data/daily_ta_cache.json`
2. File generated by `premarket_ta_cache.py` before market open
3. Falls back to dynamic Polygon API fetch if symbol missing

**Code Evidence** (paper_trading/main.py:243-256):
```python
def load_ta_cache():
    cache_path = "polygon_data/daily_ta_cache.json"
    with open(cache_path) as f:
        return json.load(f)
```

**Conclusion:** `ta_daily_close` database table is NOT queried in live trading path.

---

## Confirmed: Market Regime Uses Alpaca API

**Location:** position_manager.py:175-177
```python
url = f"https://data.alpaca.markets/v2/stocks/{symbol}/snapshot"
# Fetches SPY open/close for market regime check
```

**Called From:** position_manager.py:337 before opening new position

**Conclusion:** `spot_prices` table is NOT queried in live trading path.

---

## Database Dependency Matrix

| Table | Live Read | Live Write | Non-Live Write | Purpose |
|-------|-----------|------------|----------------|---------|
| sentiment_daily | signal_filter.py:238 | — | V1 fr-sentiment-agg | Crowded filter |
| master_tickers | signal_filter.py:68 | — | refresh-sector-data | Sector limit |
| signal_evaluations | — | signal_filter.py:174, position_manager.py:445 | — | All signals |
| active_signals | — | dashboard.py:314,344,368 | — | Passed signals |
| paper_trades_log | — | position_manager.py:427,543 | — | Trade history |
| ta_daily_close | — | — | premarket-ta-cache | TA cache (JSON used instead) |
| ta_snapshots_v2 | — | — | fl3-v2-ta-pipeline | TA snapshots |
| spot_prices | — | — | update-spot-prices | Not used (Alpaca API) |
| earnings_calendar | — | — | fetch-earnings-calendar | Not integrated |
| intraday_baselines_30m | — | — | baseline-refresh | Not consumed |

---

## File Locations

| Component | Path |
|-----------|------|
| Main entry | `paper_trading/main.py` |
| Signal generation | `paper_trading/signal_filter.py` |
| Position management | `paper_trading/position_manager.py` |
| Dashboard/logging | `paper_trading/dashboard.py` |
| Trade aggregation | `paper_trading/trade_aggregator.py` |
| Alpaca client | `paper_trading/alpaca_trader.py` |
| Firehose client | `firehose/client.py` |
| Premarket TA | `paper_trading/premarket_ta_cache.py` |
| TA pipeline | `scripts/ta_pipeline_v2.py` |
| Spot prices | `scripts/update_spot_prices.py` |
| Earnings | `scripts/fetch_earnings_calendar.py` |
