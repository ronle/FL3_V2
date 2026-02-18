# Backtest Validation Plan

**Strategy:** Uptrend + Score >= 10
**Dataset:** 455K signals (July 2025 - January 2026)
**Last Updated:** 2026-01-29

---

## Completed Tests ✅

### TEST 1: Out-of-Sample Validation ✅
- **Train:** Jul 2025 - Oct 2025 (720 signals, 61.3% WR)
- **Test:** Nov 2025 - Jan 2026 (615 signals, 57.1% WR)
- **Result:** PASS — Strategy generalizes

### TEST 2: Entry Delay Simulation ✅
- **Delays tested:** 0, 1, 5, 15, 30 min
- **5-min result:** 61.5% WR, +0.45% avg
- **Result:** PASS — Edge persists with realistic delay

### TEST 3: Slippage Modeling ✅
- **Slippages tested:** 0%, 0.1%, 0.2%, 0.3%
- **0.2% result:** 42.2% WR, +0.12% avg
- **Result:** FAIL — Need trailing stops to compensate

### TEST 4: Look-Ahead Bias Check ✅
- **Signals affected:** 18.2%
- **Corrected WR:** 72.2%
- **Result:** WARNING — Bug exists, needs fix

### TEST 5: Position Limits ✅
- **Max 3 concurrent:** 62.4% WR (vs 59.3%)
- **Executed:** 378 of 1,335 signals
- **Result:** IMPROVES performance

### TEST 6: Liquidity Filter ✅
- **$100K+ notional:** 60.1% WR, +0.74% avg
- **Result:** IMPROVES performance

### TEST 7: Monte Carlo ✅
- **Simulations:** 1,000
- **5th/50th/95th WR:** 57.2% / 59.4% / 61.6%
- **Result:** PASS — Statistically robust

### TEST 8: Failure Analysis ✅
- **Big losers (<-5%):** Only 1.3% of trades
- **Best day:** Thursday (64.6% WR)
- **Worst day:** Friday (56.1% WR)

### TEST 9: Exit Timing Optimization ✅
- **0.5% trailing stop:** 77.8% WR, +1.20% avg
- **Left on table:** 0.84% avg
- **Could-have-been-winners:** 130 trades
- **Result:** Trailing stops dramatically improve

---

## Pending Tests 🔄

### TEST 10: TA Enrichment with Prior-Day Values

**Objective:** Increase TA coverage from 15.8% to 90%+ by using prior-day indicator values.

**Current Problem:**
- RSI-14 needs 15+ bars → Only available after 15+ minutes of trading
- MACD needs 35+ bars → Only available after 35+ minutes
- Many signals fire early morning with insufficient bars

**Solution:**
Use prior trading day's end-of-day indicators:
- RSI-14 from prior day close
- MACD(12,26,9) from prior day close
- VWAP from current day (up to signal time)

**Implementation:**

```python
def enrich_with_prior_day_ta(signals, daily_bars_dir, minute_bars_dir):
    """
    Enrich signals with:
    - RSI-14: From prior 14 daily closes
    - MACD: From prior 35 daily closes
    - VWAP: From current day minute bars up to signal time
    """
    enriched = []

    for signal in signals:
        trade_date = signal['detection_time'].date()
        symbol = signal['symbol']

        # Load prior daily bars
        daily_bars = load_daily_bars(symbol, end_date=trade_date - 1, days=35)

        # Calculate prior-day indicators
        if len(daily_bars) >= 15:
            signal['rsi_14_prior'] = calculate_rsi(daily_bars['close'], 14)

        if len(daily_bars) >= 35:
            macd = calculate_macd(daily_bars['close'])
            signal['macd_line_prior'] = macd['line']
            signal['macd_signal_prior'] = macd['signal']
            signal['macd_hist_prior'] = macd['histogram']

        # Calculate current-day VWAP
        minute_bars = load_minute_bars(symbol, trade_date)
        bars_before = minute_bars[minute_bars['timestamp'] <= signal['detection_time']]
        if len(bars_before) > 0:
            signal['vwap'] = calculate_vwap(bars_before)
            signal['price_vs_vwap'] = (signal['price'] - signal['vwap']) / signal['vwap'] * 100

        enriched.append(signal)

    return enriched
```

**Expected Coverage:**
| Indicator | Current (intraday) | After (prior-day) |
|-----------|-------------------|-------------------|
| RSI-14 | 15.8% | ~95% |
| MACD | 9.5% | ~90% |
| VWAP | 15.8% | ~50%+ |

**Validation Steps:**
1. Run enrichment on all 455K signals
2. Measure coverage improvement
3. Compare indicator distributions (prior-day vs intraday)
4. Verify no look-ahead bias (only use T-1 data)

---

### TEST 11: Combined Score + TA Filter Testing

**Objective:** Find optimal combination of Score + TA indicators for best risk-adjusted returns.

**Combinations to Test:**

| # | Filter | Expected Signals | Hypothesis |
|---|--------|------------------|------------|
| 1 | Score≥10 + Uptrend + RSI<50 | ~500 | Momentum not overbought |
| 2 | Score≥10 + Uptrend + RSI<30 | ~150 | Strong oversold bounce |
| 3 | Score≥10 + Uptrend + MACD>0 | ~600 | Bullish momentum |
| 4 | Score≥10 + Below VWAP | ~400 | Buying dip |
| 5 | Score≥10 + RSI<50 + MACD>0 | ~300 | Multi-factor confirmation |
| 6 | Score≥10 + Far below VWAP (<-0.5%) | ~200 | Extreme dip |

**Implementation:**

```python
def test_combined_filters(signals):
    """Test various Score + TA combinations."""

    # Filter to base strategy
    base = [s for s in signals if s.get('score', 0) >= 10 and s.get('trend') == 1]

    combinations = [
        # (name, filter_func)
        ("Score≥10 + Uptrend", lambda s: True),
        ("+ RSI < 50", lambda s: s.get('rsi_14_prior', 50) < 50),
        ("+ RSI < 30", lambda s: s.get('rsi_14_prior', 50) < 30),
        ("+ MACD > 0", lambda s: s.get('macd_hist_prior', 0) > 0),
        ("+ Below VWAP", lambda s: s.get('price_vs_vwap', 0) < 0),
        ("+ Far below VWAP", lambda s: s.get('price_vs_vwap', 0) < -0.5),
        ("+ RSI<50 + MACD>0", lambda s: s.get('rsi_14_prior', 50) < 50 and s.get('macd_hist_prior', 0) > 0),
        ("+ RSI<30 + Below VWAP", lambda s: s.get('rsi_14_prior', 50) < 30 and s.get('price_vs_vwap', 0) < 0),
    ]

    results = []
    for name, filter_func in combinations:
        subset = [s for s in base if filter_func(s)]
        if len(subset) >= 50:
            wr = sum(1 for s in subset if s['pct_to_close'] > 0) / len(subset) * 100
            avg = sum(s['pct_to_close'] for s in subset) / len(subset)
            per_day = len(subset) / 126

            results.append({
                'filter': name,
                'signals': len(subset),
                'per_day': per_day,
                'win_rate': wr,
                'avg_return': avg,
            })

    return pd.DataFrame(results).sort_values('win_rate', ascending=False)
```

**Success Criteria:**
- Find filter with 60%+ WR
- Maintain 5+ signals/day
- +0.30%+ avg return after slippage

---

## Next Steps (Prioritized)

### Priority 1: Fix Look-Ahead Bias
**File:** `e2e_backtest_v2.py`
**Impact:** 18.2% of signals have wrong trend

```bash
# Task
1. Modify get_context() to use signal-time price
2. Calculate SMA-20 from prior days only
3. Re-run backtest
4. Validate WR doesn't drop below 55%
```

### Priority 2: TA Enrichment with Prior-Day Values
**New File:** `analysis/ta_prior_day_enricher.py`
**Impact:** Coverage 15.8% → 90%+

```bash
# Task
1. Create prior-day TA enrichment script
2. Load daily bars (ORATS or Polygon)
3. Calculate RSI-14, MACD from prior day
4. Add VWAP from current day minute bars
5. Re-run TA analysis with improved coverage
```

### Priority 3: Combined Filter Testing
**New File:** `analysis/combined_filter_analysis.py`
**Impact:** Find optimal Score + TA combination

```bash
# Task
1. Apply prior-day TA to all signals
2. Test 8 filter combinations
3. Run out-of-sample on best filters
4. Monte Carlo validation
```

### Priority 4: Paper Trading Setup
**Impact:** Real-world validation

```bash
# Task
1. Deploy signal generation to paper account
2. Implement trailing stop logic
3. Track fills vs expected
4. Measure actual slippage
5. Run for 2 weeks minimum
```

---

## Appendix: Test Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `strategy_validation.py` | Tests 1-9 | ✅ Complete |
| `test_entry_delay.py` | Entry delay impact | ✅ Complete |
| `test_lookahead.py` | Look-ahead verification | ✅ Complete |
| `analysis/ta_signal_enricher.py` | Intraday TA | ✅ Complete |
| `analysis/ta_prior_day_enricher.py` | Prior-day TA | 🔄 TODO |
| `analysis/combined_filter_analysis.py` | Score + TA | 🔄 TODO |

---

## Appendix: Key Findings Summary

| Finding | Impact | Action |
|---------|--------|--------|
| 57.1% out-of-sample WR | Strategy is valid | Proceed |
| 0.2% slippage kills edge | Need better exits | Add trailing stops |
| 0.5% trailing = 77.8% WR | Game changer | Implement |
| 18.2% look-ahead bias | Overstated WR | Fix before prod |
| Position limits help | +3% WR | Use max 3 |
| Liquidity filter helps | +1% WR, +0.2% avg | Use $50K+ |
| Thursday best, Friday worst | Trade timing matters | Weight by day |
| 130 trades could have won | Exit timing crucial | Trailing stop |
