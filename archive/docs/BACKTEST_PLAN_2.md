# FL3 V2 - Rigorous Backtest Plan for Claude CLI

## Context

We found a promising signal: **Uptrend + Score >= 10** showing 59% win rate and +0.52% avg return.
Initial comparison vs SPY showed +83% vs +9% over 6 months.

**HOWEVER**, multiple biases likely inflate these results. This plan addresses them.

## Data Available

| File | Location | Contents |
|------|----------|----------|
| Scored signals | `polygon_data/backtest_results/e2e_backtest_v2_strikes_sweeps_price_scored.json` | 455K signals with scores |
| Outcomes | `polygon_data/backtest_results/e2e_backtest_with_outcomes.json` | Price outcomes |
| Stock minute bars | `polygon_data/stocks/{date}.csv.gz` | OHLCV per minute |
| Options trades | `polygon_data/options/{date}.csv.gz` | Raw options flow |

Base path: `C:\Users\levir\Documents\FL3_V2\`

## Test Period

- Full data: 2025-07-21 to 2026-01-28 (6 months, 126 trading days)
- Train period: 2025-07-21 to 2025-10-31 (3.5 months)
- Test period: 2025-11-01 to 2026-01-28 (2.5 months)

---

## TEST 1: Out-of-Sample Validation

### Objective
Verify scoring system works on unseen data.

### Method
1. Split signals by date: train (Jul-Oct) vs test (Nov-Jan)
2. Calculate win rate and avg return for EACH period separately
3. Compare: Does Score >= 10 perform similarly in both?

### Success Criteria
- Test period win rate within 5% of train period
- Test period avg return within 0.2% of train period
- If large divergence -> scoring is overfit

---

## TEST 2: Realistic Entry Timing

### Objective
Account for execution delay - you can't enter at signal timestamp.

### Method
For each signal:
1. Get signal timestamp (e.g., 2025-08-15 07:30:00)
2. Load that day's stock minute bars
3. Find price at signal_time + 1 min, +5 min, +15 min, +30 min
4. Recalculate return: (close - delayed_entry) / delayed_entry

### Delays to Test
- +1 min (automated system)
- +5 min (fast human)
- +15 min (normal human)
- +30 min (slow/cautious)

### Success Criteria
- Edge should persist with +5 min delay
- If edge disappears at +1 min -> signal is already priced in

---

## TEST 3: Slippage and Transaction Costs

### Objective
Model realistic execution friction.

### Method
Apply costs to each trade:
- Entry slippage: 0.05% (hitting ask)
- Exit slippage: 0.05% (hitting bid)
- Total round-trip: 0.10%

Also test aggressive slippage: 0.20%, 0.30%

### Success Criteria
- Positive expectancy after 0.10% slippage

---

## TEST 4: Verify Trend Filter (No Look-Ahead)

### Objective
Ensure 20-day SMA doesn't use future data.

### Method
For each signal:
1. Get signal date
2. Load previous 20 trading days of closes (NOT including signal date)
3. Calculate SMA from those 20 days only
4. Compare signal price vs SMA
5. Re-classify as uptrend/downtrend
6. Re-run analysis with corrected trend

### Critical Check
- Pre-market signal at 7 AM shouldn't know that day's close
- Must use ONLY prior day closes for SMA

---

## TEST 5: Intraday Exit Timing

### Objective
Test different hold periods instead of only "hold to close".

### Method
For each signal:
1. Record entry time (signal_time + 5 min for realism)
2. Find price at entry + 30 min, +1 hr, +2 hr, +4 hr, close
3. Calculate returns for each exit timing

### Success Criteria
- Identify optimal hold period
- Check if early exit captures most of the move

---

## TEST 6: Position Limits and Correlation

### Objective
Test realistic portfolio constraints.

### Method
1. Limit to max 3 positions at once
2. When >3 signals on same day, take highest scored
3. Calculate portfolio return (not sum of all signals)
4. Track correlation of daily returns

---

## TEST 7: Liquidity Filter

### Objective
Ensure signals are on tradeable stocks.

### Filters
- Min avg volume: 500,000 shares/day
- Min avg dollar volume: $5,000,000/day
- Exclude micro-caps (<$500M market cap)
- Exclude mega-caps (>$100B market cap)

---

## TEST 8: Monte Carlo Simulation

### Objective
Estimate range of possible outcomes.

### Method
1. Take the 1,335 trade returns
2. Randomly sample 252 trades (1 year) with replacement
3. Calculate total return
4. Repeat 10,000 times
5. Get distribution of outcomes

---

## TEST 9: Failure Analysis

### Objective
Understand WHY losing trades fail.

### Method
For each losing trade (especially >5% losers):
1. What sector?
2. What time of day?
3. Earnings related?
4. Any common patterns?

---

## Deliverables

1. **realistic_backtest_results.json** - Adjusted returns
2. **out_of_sample_validation.json** - Train vs test comparison
3. **optimal_parameters.json** - Best entry delay, hold period, position limits
4. **risk_report.md** - Drawdowns, correlations, failure analysis
5. **final_recommendation.md** - Go/No-Go decision

---

## Success Criteria for Production

To proceed to paper trading:
- [ ] Out-of-sample win rate >= 55%
- [ ] Avg return after 0.20% slippage >= +0.20%
- [ ] Edge persists with +5 min entry delay
- [ ] Trend filter verified (no look-ahead)
- [ ] Max drawdown < 15%
- [ ] At least 5 signals/day after all filters

---

## Current Best Strategy

Filter: **Uptrend (price > 20d SMA) + Score >= 10**

Current (potentially inflated) metrics:
- Signals/day: 11
- Win rate: 59.3%
- Avg return: +0.52%
- Profit factor: 2.17

Scoring components:
- +2 pts: Early detection (4-7 AM)
- +2 pts: Bullish flow (>80% calls)  
- +2 pts: High ratio (>=15x)
- +1 pt: Concentrated strikes
- +1 pt: High sweeps (>=30%)
- +1 pt: Large trade size (>25 contracts/print)
- +1 pt: Uptrend + bullish
- +1 pt: Near 20d support

---

## Notes for Implementation

- All data files are local in C:\Users\levir\Documents\FL3_V2\polygon_data\
- Stock minute bars are gzipped CSV, ~22MB/day
- Python 3.12 available at C:\Users\levir\AppData\Local\Programs\Python\Python312\python.exe
- Take time for accuracy over speed
- If any test shows edge disappearing, STOP and report

---

## TEST 10: TA Enrichment Fix - Prior Day Values

### Issue Identified
Current TA enrichment only achieved 15.8% coverage because it calculated indicators at signal time using same-day bars only. Early morning/pre-market signals (75%+ of total) lack sufficient bars.

### Correct Approach
For signals before 11:30 AM, use **prior day's closing TA values**:

| Indicator | Pre-Market Signal | Regular Hours Signal |
|-----------|-------------------|---------------------|
| RSI-14 | Yesterday's 4 PM close RSI | Calculate if 14+ bars, else prior day |
| MACD | Yesterday's close MACD | Calculate if 26+ bars, else prior day |
| SMA-20 | Yesterday's close SMA | Calculate if 20+ bars, else prior day |
| EMA-9 | Yesterday's close EMA | Calculate if 9+ bars, else prior day |
| ATR-14 | Yesterday's ATR | Calculate if 14+ bars, else prior day |
| VWAP | Not available (intraday) | Use current day only |

### Implementation
```python
def get_ta_for_signal(signal_time, symbol):
    signal_hour = signal_time.hour
    
    if signal_hour < 11 or (signal_hour == 11 and signal_time.minute < 30):
        # Early signal: use prior day close TA
        prior_day = get_prior_trading_day(signal_time.date())
        return load_ta_at_close(symbol, prior_day)
    else:
        # Late signal: calculate from current day bars
        return calculate_ta_at_time(symbol, signal_time)
```

### Expected Outcome
- Coverage: 15.8% → 90%+
- Can now apply TA filters to early morning signals (where best edge exists)

### Key Analysis After Fix
1. Re-run "RSI<30 + MACD>0 + Calls" filter with full coverage
2. Check if prior-day oversold (RSI<30) + morning call activity predicts bounce
3. Combine with Score>=10 filter: `Prior_RSI<30 + Score>=10 + Calls`

### Hypothesis
Stocks that closed oversold (RSI<30) yesterday + unusual call activity this morning = institutional buying the dip. This should be highest conviction signal.

---

## TEST 11: Combined Score + TA Filter

### Objective
Test if combining volume-based scoring with TA confirmation improves results.

### Combinations to Test

| Filter | Expected Signals/Day | Target WR |
|--------|---------------------|-----------|
| Score>=10 only | 11 | 59% |
| Below VWAP + Calls only | 66 | 56% |
| Score>=10 + Below VWAP | ? | 60%+ |
| Score>=10 + RSI<40 | ? | 60%+ |
| Score>=10 + RSI<30 + MACD>0 | ? | 65%+ |
| Score>=7 + RSI<30 + Below VWAP | ? | 60%+ |

### Key Question
Do Score and TA filters identify the SAME signals or DIFFERENT ones?
- If same → redundant, pick simpler
- If different → combination adds value

### Analysis
```python
score_signals = set(signals where score >= 10)
ta_signals = set(signals where rsi < 30 and macd > 0)

overlap = score_signals & ta_signals
score_only = score_signals - ta_signals
ta_only = ta_signals - score_signals

# Compare win rates of each subset
```

---

## Updated Success Criteria

To proceed to paper trading:
- [ ] Out-of-sample win rate >= 55%
- [ ] Avg return after 0.20% slippage >= +0.20%
- [ ] Edge persists with +5 min entry delay
- [ ] Trend filter verified (no look-ahead)
- [ ] Max drawdown < 15%
- [ ] At least 5 signals/day after all filters
- [ ] **TA enrichment achieves 90%+ coverage with prior-day values**
- [ ] **Combined Score + TA filter tested**
