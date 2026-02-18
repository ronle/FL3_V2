# CLI Handoff: RSI Regime Backtest — Bounce-Day Filter Relaxation

**Date:** 2026-02-07
**Author:** Claude (scoping session with Ron)
**Status:** Ready for implementation
**Priority:** High — data-driven answer to "should we loosen RSI on bounce days?"
**Depends on:** Existing Polygon 1-min stock bars, signal_evaluations DB table

---

## Objective

Build a backtest script that answers one specific question:

**On market bounce-back days, would relaxing the RSI threshold from 50 → 60 have improved overall performance?**

Friday Feb 6 showed the problem: 352 signals evaluated, only 5 passed (1.4%). RSI alone killed 114 signals that passed every other filter. On a green bounce day after two red days, the RSI filter blocked exactly the momentum we'd want to trade.

But "it felt like we missed out" is not evidence. This backtest produces the evidence.

---

## What We're Comparing

| Scenario | RSI Threshold | When Applied |
|----------|--------------|--------------|
| **Baseline (V28)** | RSI < 50 always | Every day |
| **Adaptive RSI** | RSI < 50 normal days, RSI < 60 on bounce days | Only bounce-back days get relaxed threshold |

**Everything else stays identical** — same score threshold, same SMA filters, same sentiment, same earnings, same sector limits, same position limits, same exit rules.

---

## Existing Infrastructure to Reuse

### Data Sources (already available)

| Data | Location | Format |
|------|----------|--------|
| 1-min stock bars | `polygon_data/stocks/YYYY-MM-DD.csv.gz` | ticker,volume,open,close,high,low,window_start,transactions |
| Signal evaluations | DB: `signal_evaluations` | Every signal with score, RSI, trend, rejection_reason, passed_all_filters |
| SPY daily data | DB: `ta_daily_close` (or compute from polygon bars) | For bounce-day detection |
| Trading config | `paper_trading/config.py` | Exit rules, position limits, thresholds |

### Existing Backtest Code (reference, don't extend)

- `realistic_backtest.py` — Has bar loading, trade simulation, position sizing. **Good reference for patterns** but uses pre-computed signals from JSON files, not DB.
- `backtest_simulation_engine.py` — Full firehose replay. Way more than we need.

**Recommendation:** New standalone script. Borrow bar-loading pattern from `realistic_backtest.py` but query signals from DB.

---

## Bounce-Day Detection

A "bounce day" is defined as:

```python
def is_bounce_day(spy_daily: list, day_index: int) -> bool:
    """
    Bounce day = SPY closes green after 2+ consecutive red closes.
    
    Args:
        spy_daily: List of {date, open, close} sorted ascending
        day_index: Index of the day to check
    """
    if day_index < 2:
        return False
    
    today = spy_daily[day_index]
    
    # Today must be green
    if today['close'] <= today['open']:
        return False
    
    # Previous 2+ days must be red (close < prior close)
    red_streak = 0
    for i in range(day_index - 1, max(day_index - 5, -1), -1):
        if spy_daily[i]['close'] < spy_daily[i - 1]['close'] if i > 0 else False:
            red_streak += 1
        else:
            break
    
    return red_streak >= 2
```

**Alternative/additional criteria to test (parameterize):**
- SPY opens > prior close by X%
- SPY up > 0.5% from open by signal time
- VIX dropping (if available)

Start simple with the 2-red-day-then-green definition. Can refine later.

---

## Backtest Logic

### Step 1: Load SPY Daily Data

From `polygon_data/stocks/` files, extract SPY open/close per day to build the bounce-day calendar.

### Step 2: Query All Historical Signal Evaluations

```sql
SELECT 
    id, symbol, detected_at, score_total, passed_all_filters,
    rejection_reason, rsi_14, trend, notional, 
    score_volume, score_call_pct, score_sweep, score_strikes, score_notional,
    metadata
FROM signal_evaluations
WHERE detected_at >= '2025-07-01'  -- match existing backtest range
ORDER BY detected_at
```

### Step 3: Classify Each Day

For each trading day, determine if it's a bounce day. Build a set: `bounce_days = {date1, date2, ...}`

### Step 4: Replay Filter Chain — Two Scenarios

For each signal evaluation, determine if it would pass under each scenario:

**Baseline (V28):**
```python
passed = (score >= 10 
          and trend == 1 
          and rsi < 50.0 
          and notional >= 50000
          and sma_50_check  # price > 50d SMA
          and not etf
          and not high_mentions
          and not negative_sentiment
          and not near_earnings)
```

**Adaptive RSI:**
```python
rsi_threshold = 60.0 if signal_date in bounce_days else 50.0
passed = (score >= 10 
          and trend == 1 
          and rsi < rsi_threshold  # <-- only difference
          and notional >= 50000
          and sma_50_check
          and not etf
          and not high_mentions
          and not negative_sentiment
          and not near_earnings)
```

**Note:** Most filter data is already in `signal_evaluations.rejection_reason`. Parse it to reconstruct which filters each signal hit. Signals that only failed on RSI with RSI between 50-60 are the "newly admitted" ones on bounce days.

### Step 5: Apply Position Limits Per Day

For each scenario independently, process signals chronologically within each day:
- Max 5 concurrent positions
- Max 2 per sector (use `master_tickers.sector`)
- First-come-first-served (by detection time)

This is critical — you can't just count all passing signals. The position limits would have capped actual entries.

### Step 6: Simulate Trades Using 1-Min Bars

For each admitted signal, load bars from `polygon_data/stocks/{date}.csv.gz`:

```python
# Entry
entry_time = signal_time + timedelta(minutes=5)  # 5-min delay
entry_bar = get_bar_at_time(symbol, entry_time)
entry_price = entry_bar['high'] * 1.001  # worst case + 0.1% slippage

# Exit: Hold to 3:55 PM ET (matching production config)
exit_time = datetime.combine(trade_date, dt_time(15, 55))
exit_bar = get_bar_at_time(symbol, exit_time)
exit_price = exit_bar['low'] * 0.999  # worst case - 0.1% slippage

# Hard stop: -5% intraday check
# Walk bars from entry to exit, check if low ever hits -5% from entry
for bar in bars_between(entry_time, exit_time):
    if bar['low'] * 0.999 <= entry_price * 0.95:
        exit_price = entry_price * 0.95  # stopped out
        exit_time = bar['time']
        break

pnl_pct = (exit_price - entry_price) / entry_price
```

### Step 7: Compare Scenarios

Aggregate per day and overall:

```python
# Per day
for each day:
    baseline_trades = [...]
    adaptive_trades = [...]
    new_trades = adaptive_trades - baseline_trades  # the "extra" trades from relaxed RSI
    
# Output
- Total trades: baseline vs adaptive
- Win rate: baseline vs adaptive
- Avg PnL %: baseline vs adaptive
- Total PnL $: baseline vs adaptive
- Sharpe ratio: baseline vs adaptive

# Critically: 
- Performance of ONLY the new trades (RSI 50-60 on bounce days)
- Are the new trades better, worse, or same as baseline trades?
- Are bounce days better overall, or are we just adding noise?
```

---

## Output Report

```
================================================================
RSI REGIME BACKTEST RESULTS
================================================================

Date Range: 2025-07-01 to 2026-01-28
Bounce Days Identified: XX out of YYY trading days

OVERALL COMPARISON
                        Baseline (RSI<50)    Adaptive (RSI<60 bounce)
Total Trades            XXX                  XXX
Win Rate                XX.X%                XX.X%
Avg PnL/Trade           +X.XX%               +X.XX%
Total PnL               $X,XXX               $X,XXX
Sharpe Ratio            X.XX                 X.XX
Max Drawdown            X.X%                 X.X%

BOUNCE DAY PERFORMANCE (XX days)
                        Baseline             Adaptive
Trades on Bounce Days   XXX                  XXX
Win Rate                XX.X%                XX.X%  
Avg PnL/Trade           +X.XX%               +X.XX%

NORMAL DAY PERFORMANCE (identical between scenarios)
Trades                  XXX
Win Rate                XX.X%
Avg PnL/Trade           +X.XX%

NEW TRADES ANALYSIS (RSI 50-60, bounce days only)
New Trades Added        XXX
Win Rate                XX.X%
Avg PnL/Trade           +X.XX%
Best New Trade          SYMBOL +X.XX%
Worst New Trade         SYMBOL -X.XX%

VERDICT: [ADOPT / REJECT / MORE DATA NEEDED]
================================================================
```

---

## File Structure

```
FL3_V2/
├── scripts/
│   └── backtest_rsi_regime.py      # Main backtest script (~400 lines)
└── polygon_data/
    └── stocks/                      # Existing 1-min bars (read-only)
```

### Script Interface

```bash
# Default: RSI 50 vs 60 on bounce days
python -m scripts.backtest_rsi_regime

# Custom thresholds
python -m scripts.backtest_rsi_regime --baseline-rsi 50 --adaptive-rsi 65

# Custom bounce definition
python -m scripts.backtest_rsi_regime --min-red-days 3 --spy-bounce-pct 0.5

# Date range
python -m scripts.backtest_rsi_regime --from 2025-10-01 --to 2026-01-28

# Output to file
python -m scripts.backtest_rsi_regime --output results/rsi_regime_backtest.json
```

---

## Implementation Notes

### Parsing Rejection Reasons

`signal_evaluations.rejection_reason` contains semicolon-separated reasons. To identify signals that ONLY failed on RSI:

```python
def only_failed_rsi(rejection_reason: str, rsi: float, max_rsi: float) -> bool:
    """Would this signal pass if RSI threshold was max_rsi?"""
    if rejection_reason is None:
        return False  # already passed
    reasons = [r.strip() for r in rejection_reason.split(';')]
    # Remove the RSI reason
    non_rsi_reasons = [r for r in reasons if 'RSI' not in r]
    # If no other reasons AND RSI < new threshold, it would pass
    return len(non_rsi_reasons) == 0 and rsi < max_rsi
```

### Missing Data in signal_evaluations

The `signal_evaluations` table may not have all filter fields needed to fully reconstruct the filter chain (e.g., SMA50 price, sector). But we don't need to — the rejection_reason string already tells us the verdict. We only need to identify signals where RSI was the **sole** rejection reason.

### Bar Loading Optimization

~1.8M rows per day × potentially 250+ days = huge. Don't load all days at once.

```python
# Load one day at a time, only symbols we need
def load_bars_for_day(date_str: str, symbols: set) -> dict:
    """Load 1-min bars for specific symbols on a specific day."""
    filepath = f"polygon_data/stocks/{date_str}.csv.gz"
    bars = defaultdict(list)
    with gzip.open(filepath, 'rt') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['ticker'] in symbols:
                bars[row['ticker']].append(row)
    return bars
```

### Sector Concentration

Need sector data for position limits. Options:
1. Query `master_tickers` from DB (preferred)
2. Use the global `_SECTOR_CACHE` pattern from `signal_filter.py`

### Position Sizing

Match production: `MAX_POSITION_SIZE_PCT = 0.10` of $100K account = $10K max per trade.

```python
shares = min(
    int(10000 / entry_price),      # max position size
    int(100000 * 0.10 / entry_price)  # 10% of account
)
```

---

## Parameterize for Reuse

This script should be designed so we can test other filter variations later:

```python
@dataclass
class BacktestConfig:
    # RSI thresholds
    baseline_rsi: float = 50.0
    adaptive_rsi: float = 60.0
    
    # Bounce-day definition
    min_red_days: int = 2
    spy_bounce_min_pct: float = 0.0  # SPY open vs prior close
    
    # Which filter to relax (future: could relax SMA, notional, etc.)
    relax_filter: str = "rsi"  
    
    # Trade simulation
    entry_delay_min: int = 5
    slippage_pct: float = 0.001
    exit_time: str = "15:55"
    hard_stop_pct: float = -0.05
    max_positions: int = 5
    max_per_sector: int = 2
    account_size: float = 100_000
```

This way, the same script can later answer: "What about RSI < 65?", "What if bounce = 3 red days?", "What about relaxing notional to $40K on bounce days?"

---

## Success Criteria

The backtest tells us to ADOPT adaptive RSI if:
- [ ] Win rate of new trades (RSI 50-60 on bounce days) >= 55%
- [ ] Avg PnL of new trades > 0
- [ ] Overall adaptive Sharpe >= baseline Sharpe
- [ ] No significant increase in max drawdown

The backtest tells us to REJECT if:
- [ ] New trades have win rate < 50% (random coin flip)
- [ ] New trades drag down overall PnL
- [ ] Drawdown increases meaningfully

---

## Estimated Effort

| Component | Lines | Notes |
|-----------|-------|-------|
| Bounce-day detection | ~40 | SPY daily extraction + classification |
| Signal loading + parsing | ~60 | DB query + rejection_reason parsing |
| Filter replay (both scenarios) | ~80 | Reconstruct pass/fail per scenario |
| Position limit simulation | ~60 | Per-day chronological with sector caps |
| Trade simulation (bar loading + entry/exit) | ~100 | Borrow pattern from realistic_backtest.py |
| Reporting | ~60 | Console output + optional JSON export |
| **Total** | **~400** | Standalone script, no new dependencies |

---

## Open Questions

1. **Signal evaluations date range** — How far back does `signal_evaluations` go? The Polygon stock bars go back to Jan 2025, but signal_evaluations may only cover the V2 paper trading period.

2. **SMA50 / sector data** — Is the 50d SMA and sector info stored in signal_evaluations, or do we need to pull from ta_daily_close and master_tickers separately?

3. **Market regime filter** — The V28 market regime filter (SPY down >0.5% from open = pause entries) interacts with this. On bounce days, SPY might dip below -0.5% intraday before recovering. Should the backtest model this filter too, or assume it's independent?

4. **Notional in rejection_reason** — Some signals failed on notional < $50K AND RSI. These would NOT be admitted even with relaxed RSI. The parsing logic handles this correctly by checking for RSI-only rejections.
