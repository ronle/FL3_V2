# E2E Backtest System

## Files

### 1. `download_6month_data.py`
Downloads 6 months of flat files (July 2025 - Jan 2026):
- Options trades: `polygon_data/options/YYYY-MM-DD.csv.gz`
- Stock minute bars: `polygon_data/stocks/YYYY-MM-DD.csv.gz`

Run: `python download_6month_data.py`

### 2. `e2e_backtest.py`
Main backtest engine replicating production logic:
- **BucketAggregator**: 30-min buckets (prints, notional, contracts, call/put)
- **BaselineManager**: 20-day rolling bucket averages + time-of-day multipliers
- **UOADetector**: 3x threshold with 1-hour cooldown

Run: `python e2e_backtest.py`
Output: `polygon_data/backtest_results/e2e_backtest_results.json`

### 3. `e2e_label_outcomes.py`
Labels signals with price outcomes and applies filters:
- Price at signal time
- Price at market open (for pre-market signals)
- % to close, max gain, max loss
- Filters: penny stocks (<$5), ETFs

Run: `python e2e_label_outcomes.py`
Output: `polygon_data/backtest_results/e2e_backtest_with_outcomes.json`

## Production Logic Replicated

### Bucket Aggregation (30-min windows)
- Accumulates: prints, notional, contracts, unique options
- Stores history for baseline calculation

### Baseline Calculation
Primary: 20-day rolling average of same bucket
Fallback: Time-of-day multiplier × default baseline

### Time-of-Day Multipliers (U-shape)
```
09:30: 3.0x (open rush)
10:00: 1.8x
10:30: 1.4x
11:00: 1.1x
11:30: 0.8x
12:00: 0.6x (lunch lull)
12:30: 0.5x
13:00: 0.6x
13:30: 0.8x
14:00: 1.0x
14:30: 1.1x
15:00: 1.3x
15:30: 2.0x (close rush)
```

### Detection Thresholds
- 3x baseline notional
- $10K minimum notional
- 1-hour cooldown per symbol

### Filters Applied (during outcome labeling)
- Penny stocks: price < $5
- ETFs: SPY, QQQ, etc. excluded

## Workflow

```bash
# 1. Download data (run overnight if needed)
python download_6month_data.py

# 2. Run backtest (processes ~145 days)
python e2e_backtest.py

# 3. Label outcomes with stock prices
python e2e_label_outcomes.py

# 4. Analyze results (in JSON output)
```

## Expected Timeline

- Download: ~1-2 hours for ~11 GB
- Backtest: ~30-60 min (processes ~8M trades/day × 145 days)
- Labeling: ~10-15 min

## Key Differences from Simplified Backtest

| Simplified | E2E Production |
|------------|----------------|
| Full-day baseline | 30-min bucket baseline |
| 2x threshold | 3x threshold |
| No time adjustment | U-shaped multipliers |
| 1-day baseline | 20-day rolling |
| Volume only | Notional (price × size × 100) |
| No warmup | 20-day warmup period |
| No cooldown | 1-hour cooldown |
| No filters | Penny stock + ETF filters |
