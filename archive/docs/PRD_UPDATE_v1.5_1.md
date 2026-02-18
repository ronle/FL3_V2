# FL3 V2 - PRD Update v1.5

## Validation Results Summary

**Date:** 2026-01-30
**Status:** Phase 6 (Backtesting & Validation) - COMPLETE with findings

### Test Results

| Test | Target | Result | Status |
|------|--------|--------|--------|
| Out-of-Sample | 55%+ WR | 57.1% | ✅ PASS |
| Entry Delay +5 min | 55%+ WR | 61.5% | ✅ PASS |
| Monte Carlo 5th %ile | >52% | 57.2% | ✅ PASS |
| 0.2% Slippage | +0.20% net | +0.12% | ❌ FAIL (fixed by trailing stop) |
| Look-ahead Bias | None | 18.2% affected | ⚠️ REQUIRES FIX |

### Critical Discovery: Trailing Stops

| Exit Strategy | Win Rate | Avg Return |
|---------------|----------|------------|
| Hold to close | 59.3% | +0.52% |
| **0.5% trailing stop** | **77.8%** | **+1.20%** |

After 0.2% slippage with trailing stop: **+1.00% net** ✅

---

## Production Configuration

### Entry Rules

```python
def should_enter(signal):
    return (
        signal.trend == 1                    # Price > 20d SMA (at signal time, not EOD)
        and signal.score >= 10               # Multi-factor score
        and signal.notional >= 50000         # $50K+ for liquidity
        and signal.call_pct > 0.8            # Bullish flow
        and not is_friday_afternoon()        # Reduce Friday exposure
    )
```

### Exit Rules

```python
def manage_position(position):
    # Trailing stop: 0.5-1% from high water mark
    trailing_stop_pct = 0.005  # 0.5%
    
    if current_price > position.high_water_mark:
        position.high_water_mark = current_price
    
    stop_price = position.high_water_mark * (1 - trailing_stop_pct)
    
    if current_price <= stop_price:
        exit_position()
```

### Position Management

- **Max concurrent positions:** 3
- **Position sizing:** Equal weight (33% each when full)
- **Daily limit:** ~10 signals expected, take top 3 by score

### Scoring System (Unchanged)

| Factor | Points |
|--------|--------|
| Early detection (4-7 AM) | +2 |
| Bullish flow (>80% calls) | +2 |
| High ratio (>=15x) | +2 |
| Concentrated strikes | +1 |
| High sweeps (>=30%) | +1 |
| Large trade size (>25 contracts/print) | +1 |
| Uptrend + bullish | +1 |
| Near 20d support | +1 |

**Minimum score for entry:** 10

---

## Required Fixes Before Production

### FIX 1: Look-ahead Bias in Trend Calculation

**Issue:** 18.2% of signals used EOD close price for trend calculation, but signal fired earlier in day.

**Fix:**
```python
def calculate_trend_at_signal_time(symbol, signal_time):
    # Get 20-day SMA using ONLY closes BEFORE signal
    prior_closes = get_closes_before(symbol, signal_time, days=20)
    sma_20 = mean(prior_closes)
    
    # Get price AT signal time (not EOD)
    price_at_signal = get_price_at_time(symbol, signal_time)
    
    return 1 if price_at_signal > sma_20 else -1
```

**Impact:** May reduce signal count slightly, will improve accuracy

### FIX 2: TA Enrichment with Prior-Day Values

**Issue:** Only 15.8% of signals have TA data because early morning signals lack sufficient same-day bars.

**Fix:** For signals before 11:30 AM, use prior day's closing TA values:

| Indicator | Early Signal (<11:30 AM) | Late Signal (>=11:30 AM) |
|-----------|-------------------------|--------------------------|
| RSI-14 | Prior day's close | Current calculation |
| MACD | Prior day's close | Current calculation |
| SMA/EMA | Prior day's close | Current calculation |
| VWAP | N/A | Current day only |

**Expected outcome:** Coverage 15.8% → 90%+

### FIX 3: Combined Score + TA Filter (Optional Enhancement)

After TA enrichment fix, test combined filters:
- Score >= 10 + RSI < 30 (oversold bounce)
- Score >= 10 + Below VWAP (buy the dip)
- Score >= 7 + RSI < 30 + MACD > 0 (high conviction, lower score threshold)

---

## Updated Phase Status

### Phase 6: Backtesting & Validation

| Component | Status |
|-----------|--------|
| 6.1 E2E Backtest Engine | ✅ COMPLETE |
| 6.2 Signal Enhancement - Strike Analysis | ✅ COMPLETE |
| 6.3 Signal Enhancement - Trade Classification | ✅ COMPLETE |
| 6.4 Signal Enhancement - Price Context | ✅ COMPLETE |
| 6.5 Multi-Factor Scoring System | ✅ COMPLETE |
| 6.6 Strategy Validation Suite | ✅ COMPLETE |
| 6.7 Look-ahead Bias Fix | 🔲 PENDING |
| 6.8 TA Enrichment with Prior-Day Values | 🔲 PENDING |
| 6.9 Paper Trading Validation | 🔲 PENDING |

---

## Next Steps for CLI

### Step 1: Fix Look-ahead Bias
```
File: strategy_validation.py or new fix_lookahead.py
Task: Recalculate trend using signal-time price vs prior-day SMA
Output: Corrected signals, updated win rates
```

### Step 2: TA Enrichment Fix
```
File: analysis/ta_signal_enricher.py
Task: Use prior-day close TA for early morning signals
Output: 90%+ TA coverage, rerun TA filter analysis
```

### Step 3: Combined Filter Testing
```
Task: Test Score + TA combinations
Output: Best combined filter with win rate and signal count
```

### Step 4: Paper Trading Setup
```
Task: Deploy to Alpaca paper trading
Duration: 2 weeks minimum
Metrics: Track fill quality, slippage, actual win rate
```

---

## Expected Production Performance

**Conservative estimate (after all fixes):**

| Metric | Value |
|--------|-------|
| Signals per day | 5-10 |
| Win rate | 60-65% |
| Avg return per trade (after slippage) | +0.80-1.00% |
| Trades taken (max 3 concurrent) | 3-5/day |
| Expected daily return | +0.50-0.80% |
| Expected monthly return | +10-15% |
| Max drawdown | <10% |

**Note:** These are estimates. Paper trading will validate.

---

## Files Created During Validation

| File | Purpose |
|------|---------|
| `e2e_backtest_v2.py` | Enhanced backtest with strikes/sweeps/price |
| `strategy_validation.py` | Main validation suite |
| `test_entry_delay.py` | Entry delay analysis |
| `test_lookahead.py` | Look-ahead bias verification |
| `STRATEGY_VALIDATION_REPORT.md` | Full validation report |
| `BACKTEST_PLAN.md` | Test plan for CLI |

---

## Decision: Ready for Paper Trading?

**After completing fixes 1 & 2:** YES, proceed to paper trading

**Criteria met:**
- [x] Out-of-sample win rate >= 55% (got 57.1%)
- [x] Edge persists with +5 min entry delay (got 61.5%)
- [x] Monte Carlo 5th percentile > 52% (got 57.2%)
- [x] Positive expectancy after slippage (with trailing stop: +1.00%)
- [ ] Look-ahead bias fixed (18.2% pending)
- [ ] TA enrichment coverage >= 90% (pending)
