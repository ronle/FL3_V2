# Cameron Scanner — Phase 0 Data Availability Audit

**Date**: 2026-02-27
**Status**: COMPLETE

---

## 0.1 Float Data

### What We Have Today
| Source | Float Column? | Notes |
|--------|--------------|-------|
| `orats_daily` | NO | No float or shares_outstanding columns |
| `master_tickers` | NO | Has `market_cap` (3,766/5,980 populated via Polygon) |
| Polygon `/v3/reference/tickers/{ticker}` | PARTIAL | Returns `share_class_shares_outstanding`, `weighted_shares_outstanding` — NOT true float |
| FMP `/v3/profile/{symbol}` | YES | Returns `floatShares` directly |

### Recommendation: Two-Step Approach
1. **Immediate (free, no extra API calls):** Extend `refresh_market_cap.py` to also capture `weighted_shares_outstanding` from the same Polygon API call it already makes. This is a ~5-line change. `weighted_shares_outstanding` is a reasonable float proxy (typically within 10-20% of true float for most stocks).
2. **If precision needed later:** Use FMP API for true `floatShares`. FMP key exists in GCP secrets. Batch 100+ symbols per call = ~110 calls for 11K symbols.

### Proxy Alternative
Market cap can serve as a float proxy for Cameron's $1-$20 price range:
- Stock at $5 with market_cap $50M → ~10M shares outstanding
- Cameron's sweet spot (float < 10M) ≈ market_cap < $100M at $10/share
- Already have `market_cap` in master_tickers — usable immediately for backtesting

### Action: Add columns to master_tickers
```sql
ALTER TABLE master_tickers ADD COLUMN shares_outstanding BIGINT;
ALTER TABLE master_tickers ADD COLUMN shares_outstanding_updated_at TIMESTAMPTZ;
```

---

## 0.2 Pre-Market Gap Calculation

### Finding: FULLY FEASIBLE

Polygon stock minute bar files (`D:\polygon_data\stocks\*.csv.gz`) **include pre-market data**.

| Property | Value |
|----------|-------|
| Time coverage | 4:00 AM - 8:00 PM ET (pre-market + RTH + after-hours) |
| Columns | `ticker, volume, open, close, high, low, window_start, transactions` |
| Timestamp format | Unix nanoseconds (UTC) |
| Session indicator | NONE — must derive from timestamp |
| File count | 1,536 files (2020-2026) |
| Total size | ~27 GB compressed |

### Gap Calculation Method

```
gap_pct = (first_premarket_bar_open - prev_day_close) / prev_day_close
```

For backtesting, two approaches:
- **Conservative**: Use first bar at 9:30 AM (market open) as gap proxy
- **Accurate**: Use actual pre-market bars (4:00 AM+) from the minute data

Both are feasible with existing data. The pre-market bars exist in the files.

### DuckDB Query Pattern
```sql
-- Convert nanosecond timestamps, filter pre-market
SELECT ticker,
       open as premarket_open,
       epoch_ms(window_start / 1000000) as bar_time
FROM read_csv_auto('D:/polygon_data/stocks/2026-02-11.csv.gz')
WHERE hour(timezone('America/New_York', epoch_ms(window_start / 1000000))) < 9
   OR (hour(...) = 9 AND minute(...) < 30)
ORDER BY window_start
```

---

## 0.3 Equity RVOL (Relative Volume)

### Finding: FULLY FEASIBLE — Components Already Exist

| Component | Source | Status |
|-----------|--------|--------|
| `avg_daily_volume` | `orats_daily` column | Already used by v57 ADV filter, full 6-year history |
| Options volume ratio | `total_volume / volume_ema_30d` in orats_daily | Computed nightly by ORATS ingest |
| Stock daily volume | Polygon minute bars (sum per day) | Available in backtest cache |
| Real-time volume | Alpaca snapshot `dailyBar.v` | Available via existing API |

### RVOL Calculation

**For backtesting:**
```sql
-- Stock-level RVOL from Polygon minute bars
daily_volume = SUM(volume) WHERE date = trade_date
avg_30d_volume = AVG(daily_volume) over prior 30 trading days
rvol = daily_volume / avg_30d_volume
```

**For live trading:**
```python
# Alpaca snapshot provides today's cumulative volume
rvol = alpaca_snapshot.daily_bar.volume / orats_daily.avg_daily_volume
```

### Options RVOL (Already Exists)
`engulfing_checker.get_volume_ratio()` computes `total_volume / volume_ema_30d` from orats_daily. This measures options activity relative volume — different from stock RVOL but potentially useful as a confluence signal.

---

## Summary & Next Steps

| Metric | Data Available? | Backtest Ready? | Live Ready? | Effort |
|--------|----------------|-----------------|-------------|--------|
| **Gap %** | YES (Polygon minute bars) | YES | YES (Alpaca snapshot) | Low |
| **Float** | PARTIAL (shares_outstanding via Polygon) | YES (with market_cap proxy) | YES (extend refresh script) | Low-Medium |
| **RVOL** | YES (orats avg_daily_volume + Polygon bars) | YES | YES (Alpaca snapshot) | Low |
| **Price range** | YES (minute bars have close price) | YES | YES | Zero |

### Immediate Actions (Phase 1 unblocked)
1. Build daily universe from Polygon minute bars using DuckDB (gap %, RVOL, price)
2. Use `market_cap < $100M` as float proxy for initial backtest
3. Extend `refresh_market_cap.py` to capture `weighted_shares_outstanding` (parallel, non-blocking)

### No Blockers for Phase 1
All three core Cameron metrics (gap %, float proxy, RVOL) are available from existing data. Proceed to Phase 1.1 (Build Daily Universe).
