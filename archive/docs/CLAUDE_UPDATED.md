# FL3 V2 — Claude Code Project Context

**Last Updated:** 2026-01-30
**Status:** Paper Trading LIVE

---

## Session Startup (MANDATORY)

**At the start of EVERY session:**

1. Run `Get-Date` (Windows) or `date` (Linux) to confirm current time
2. State the current date, time (ET), and market status
3. Check paper trading status if market hours

**Market Hours (ET):**
- Pre-Market: 4:00 AM - 9:30 AM
- Market Open: 9:30 AM - 4:00 PM
- After-Hours: 4:00 PM - 8:00 PM

---

## Project Overview

FL3_V2 is a pump-and-dump detection system that processes options trades via Polygon firehose to detect unusual options activity (UOA), score signals, and execute paper trades.

### Current System Status

| Component | Status | Notes |
|-----------|--------|-------|
| Firehose (fl3-v2-firehose) | ✅ LIVE | Detects UOA signals |
| TA Pipeline (premarket-ta-cache) | ✅ LIVE | Daily 6 AM ET refresh |
| Paper Trading | ✅ LIVE | Alpaca integration |
| Signal Evaluation | ✅ LIVE | Logging to DB |

---

## Validated Trading Strategy

### Entry Criteria (ALL must pass)

```python
ENTRY_FILTERS = {
    "score": >= 10,           # Multi-factor score
    "trend": "uptrend",       # Price > 20-day SMA (prior days only)
    "rsi_14": < 50,           # Prior-day RSI (not overbought)
    "notional": >= 50000,     # $50K+ liquidity
}
```

### Exit Strategy

```python
EXIT_STRATEGY = "hold_to_close"  # Exit at 3:55 PM ET
HARD_STOP = None                  # Optional: -5% disaster stop
```

**CRITICAL:** Trailing stops FAIL under adversarial testing. Do NOT implement trailing stops.

### Position Management

```python
MAX_POSITIONS = 3           # Maximum concurrent positions
MAX_PER_SYMBOL = 1          # No duplicate buys
POSITION_SIZE = ~$10,000    # Per position
```

### Expected Performance (Adversarial-Validated)

| Filter | Signals/Day | Win Rate | Avg Return |
|--------|-------------|----------|------------|
| Score>=10 + Uptrend | 10.5 | 54.2% | +0.41% |
| + RSI < 50 | **2.3** | **74.5%** | **+1.06%** |

---

## Scoring System

| Factor | Points | Condition |
|--------|--------|-----------|
| Volume ratio | 1-5 | 5x=1pt, 10x=3pt, 20x+=5pt |
| Call % | 0-3 | >85% calls = 3pt |
| Sweep % | 0-3 | >50% sweeps = 3pt |
| Strike concentration | 0-3 | Few strikes = 3pt |
| Notional | 0-3 | >$200K = 3pt |

**Minimum score for entry:** 10

---

## Project Structure

```
FL3_V2/
├── paper_trading/           # Paper trading system
│   ├── main.py              # Main orchestrator
│   ├── config.py            # Trading config
│   ├── signal_filter.py     # Entry filters
│   ├── position_manager.py  # Position tracking
│   ├── alpaca_trader.py     # Alpaca API client
│   ├── eod_closer.py        # 3:55 PM exit
│   └── premarket_ta_cache.py # TA data fetch
├── firehose/                # Signal detection
│   ├── client.py            # WebSocket client
│   └── aggregator.py        # Rolling aggregation
├── analysis/                # Analysis tools
│   ├── ta_calculator.py     # TA indicators
│   └── ta_prior_day_enricher.py
├── polygon_data/            # Historical data
│   ├── options/             # Options flat files
│   ├── stocks/              # Stock minute bars
│   └── backtest_results/    # Backtest outputs
├── scripts/
│   └── generate_extended_signals.py
├── sql/                     # Database schemas
└── tests/
```

---

## Database Tables

### Core Tables (GCP Cloud SQL)

| Table | Purpose |
|-------|---------|
| `ta_daily_close` | Prior-day TA values (RSI, MACD) |
| `signal_evaluations` | All signal evaluations with scores |
| `active_signals` | Signals that passed filters (cross-day WHERE fix v45) |
| `paper_trades_log` | Full trade lifecycle: entry+exit with crash recovery (v45) |
| `uoa_triggers_v2` | Raw UOA triggers |
| `intraday_baselines_30m` | Volume baselines |
| `tracked_tickers_v2` | Symbols being tracked |

### Key Queries

```sql
-- Today's signal evaluations
SELECT symbol, score_total, rsi_14, trend, passed_all_filters
FROM signal_evaluations
WHERE DATE(detected_at) = CURRENT_DATE
ORDER BY detected_at DESC;

-- TA coverage check
SELECT COUNT(*) as symbols, 
       MAX(trade_date) as latest
FROM ta_daily_close;

-- Paper trades today
SELECT * FROM paper_trades_log
WHERE DATE(created_at) = CURRENT_DATE;
```

---

## GCP Configuration

| Resource | Value |
|----------|-------|
| Project | `fl3-v2-prod` |
| Region | `us-west1` |
| Cloud SQL | `fr3-pg` (PostgreSQL) |
| Registry | `us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2` |

### Cloud Run Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `fl3-v2-firehose` | Market hours | Signal detection |
| `premarket-ta-cache` | 6:00 AM ET Mon-Fri | TA data refresh |

### Secrets

| Secret | Purpose |
|--------|---------|
| `DATABASE_URL` | PostgreSQL connection |
| `POLYGON_API_KEY` | Polygon API |
| `ALPACA_API_KEY` | Alpaca trading |
| `ALPACA_SECRET_KEY` | Alpaca auth |

---

## Paper Trading Commands

```bash
# Start paper trading
python start_paper_trading.py

# Dry run (no trades)
python start_paper_trading.py --dry-run

# Test connectivity
python start_paper_trading.py --test

# Generate pre-market TA cache
python start_paper_trading.py --premarket
```

---

## Known Issues & Fixes Needed

### BUG: Duplicate Trades
**Issue:** System bought SPY 3x on Jan 30 because `trade_placed` not updated after fill.
**Fix needed:**
1. Update `trade_placed = true` after order fills
2. Check existing positions before buying
3. Enforce max 3 positions

### BUG: ETF Inclusion
**Issue:** SPY, QQQ, XLE being traded. Our edge was tested on individual stocks.
**Fix needed:** Add ETF exclusion filter

### Missing: Dashboard
**Issue:** No visibility into signal decisions
**Fix needed:** Implement `CLI_TASK_DASHBOARD_v2.md`
- Google Sheet integration
- Real-time signal logging
- Position P/L tracking

---

## Validation Summary

All tests PASSED:

| Test | Result |
|------|--------|
| Out-of-Sample WR | 57.1% ✅ |
| Entry Delay +5min | 61.5% WR ✅ |
| Monte Carlo 5th %ile | 57.2% ✅ |
| Adversarial Hold-to-Close | 54.5% WR, +0.42% ✅ |
| Look-ahead Bias Fix | Minimal impact ✅ |
| TA Coverage | 99.7% RSI ✅ |
| RSI<50 Filter | 74.5% WR, +1.06% ✅ |

**Trailing stops FAIL** adversarial test (-0.34%). Do NOT use.

---

## Key Documents

| File | Purpose |
|------|---------|
| `CLAUDE.md` | This file - system overview |
| `PRD_UPDATE_v1.5.md` | Strategy validation results |
| `TEST_6_GO_NOGO_SUMMARY.md` | Final validation summary |
| `CLI_UPDATE_PLAN_v2.md` | Pending fixes and tests |
| `CLI_TASK_DASHBOARD_v2.md` | Dashboard implementation spec |
| `BACKTEST_PLAN.md` | Extended testing plan |

---

## First Day Results (Jan 30, 2026)

### Trades Executed

| Symbol | Entry | Qty | Notes |
|--------|-------|-----|-------|
| SPY | $690.66 | 42 | ❌ 3 duplicate buys (bug) |
| QQQ | $620.98 | 16 | ⚠️ ETF |
| AMZN | $239.64 | 41 | ✅ Valid |
| AAPL | $258.48 | 38 | ✅ Valid |

### Mid-Day P/L

| Symbol | P/L |
|--------|-----|
| AAPL | +$28.12 ✅ |
| AMZN | +$12.30 ✅ |
| QQQ | -$19.36 |
| SPY | -$32.24 |

**Individual stocks (AAPL, AMZN) winning. ETFs (SPY, QQQ) losing.**

---

## Immediate Priorities

1. **Fix duplicate trade bug** - Update `trade_placed`, check positions
2. **Add ETF exclusion** - Filter out SPY, QQQ, XLE, etc.
3. **Implement dashboard** - Google Sheets for monitoring
4. **Monitor EOD close** - Verify 3:55 PM exit works

---

## Development Workflow

1. Check market status and paper trading status
2. Review recent signals in `signal_evaluations`
3. Check positions via Alpaca dashboard
4. Make fixes, test with `--dry-run`
5. Deploy and monitor

---

## API Constraints

| API | Limit | Our Usage |
|-----|-------|-----------|
| Polygon WebSocket | 1 connection | 1 (firehose) |
| Polygon REST | 50K/day | ~500/day |
| Alpaca | 200 req/min | ~10/min |

---

## Critical Rules

1. **NO trailing stops** - They fail adversarial testing
2. **Hold to close** - Simple exit strategy that works
3. **Max 3 positions** - Prevents overexposure
4. **RSI < 50 filter** - Dramatically improves WR
5. **Prior-day TA** - Use yesterday's RSI/MACD for early signals
6. **Exclude ETFs** - Edge is on individual stocks
