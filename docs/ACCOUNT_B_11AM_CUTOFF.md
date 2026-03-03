# Account B: 11 AM ET Entry Cutoff

**Date:** 2026-03-03
**Version:** v73
**Decision:** Stop placing new Account B orders after 11:00 AM ET

## Background

Account B (big-hitter engulfing pattern trader) was observed losing morning gains during afternoon trading. On 2026-03-03, max morning P&L of +$1,600 eroded to +$150 by afternoon. This prompted an investigation into whether time-of-day restrictions would improve performance.

## Findings

### 1. Live Data (181 trades, Feb 2026)

Entry hour breakdown (ET):

| Hour | Trades | P&L | Win Rate |
|------|--------|-----|----------|
| 9 AM | 35 | +$90 | 37% |
| 10 AM | 53 | +$3,092 | 57% |
| 11 AM+ | 57 | +$653 | 54% |

Morning entries (<11 AM): 88 trades, +$3,182 (83% of all profits from 61% of trades)

### 2. Full 3-Year Backtest (559 trades, Feb 2023 - Jan 2026)

From `account_b_trades.csv` (UOA + engulfing backtest):

| Group | Trades | Total P&L | Win Rate | Avg P&L/Trade |
|-------|--------|-----------|----------|---------------|
| Morning (<11 AM) | 465 (83%) | +$17,327 | 50.3% | +$37.26 |
| Afternoon (>=11 AM) | 94 (17%) | -$209 | 48.9% | -$2.22 |

Hourly detail:
- 9:00 ET: 402 trades, +$17,462 (virtually all profit)
- 10:00 ET: 63 trades, -$135
- 11:00 ET: 45 trades, +$486
- 12:00 ET: 49 trades, -$695

### 3. Trailing Stop Investigation (REJECTED)

Tested adding trailing stops after 11 AM on open positions. Two rounds of backtesting:

**Round 1 (flat -2% hard stop baseline):** Appeared modestly helpful (+$4K at 0.5% trail). But this baseline was unrealistic -- Account B uses per-trade engulfing stops/targets, not flat -2%.

**Round 2 (realistic engulfing stop + target baseline):**

Joined all 559 trades back to `engulfing_scores` for actual stop_loss/target_1 per trade:
- Match rate: 100% (559/559)
- Avg stop distance: 3.97% (median 3.13%), range 0.24% to 28.33%
- Avg target distance: 3.76% (roughly 1:1 R:R)

Results with realistic baseline:

| Strategy | Total P&L | vs Baseline |
|----------|-----------|-------------|
| Baseline (engulfing stop+target+EOD) | $26,231 | -- |
| + Trail 0.5% after 11 AM | $22,750 | **-$3,482** |
| + Trail 1.0% after 11 AM | $18,839 | **-$7,393** |
| + Trail 2.0% after 11 AM | $19,032 | **-$7,200** |
| + Trail 3.0% after 11 AM | $17,444 | **-$8,787** |

Trailing stop saves $1-3K on stop-loss trades but destroys $5-10K on EOD winners by exiting on afternoon pullbacks. The engulfing pattern's native stop+target is already well-calibrated.

**Conclusion:** Trailing stops hurt performance. Rejected.

### 4. Key Insight: Flat -2% vs Engulfing Stop

The original 3-year backtest used a flat -2% hard stop for all trades ($17,327 total P&L). Using actual engulfing stop/target per trade yields $26,231 -- a $8,904 improvement. This confirms the engulfing pattern's risk management is significantly better than a uniform stop.

## Implementation

**Change:** `_poll_account_b_patterns()` now checks `ACCOUNT_B_LAST_ENTRY_TIME` (default 11:00 AM ET) before polling for new patterns. Existing positions continue to be monitored for stops, targets, and EOD exit normally.

**Config:** `ACCOUNT_B_LAST_ENTRY_TIME = dt_time(11, 0)` in `paper_trading/config.py`

**Files changed:**
- `paper_trading/config.py` -- added `ACCOUNT_B_LAST_ENTRY_TIME`
- `paper_trading/main.py` -- added time check in `_poll_account_b_patterns()`

**What does NOT change:**
- Stop/target monitoring continues all day for open positions
- Pending limit order fill/expiry checking continues all day
- EOD closer still runs at 3:55 PM
- Accounts A and C are unaffected

## Backtest Scripts (temp/)

- `temp/morning_vs_afternoon.py` -- morning vs afternoon entry analysis
- `temp/backtest_trailing_full.py` -- trailing stop backtest (flat -2% baseline)
- `temp/backtest_trailing_realistic.py` -- trailing stop backtest (engulfing stop+target baseline)
- `temp/join_trades_to_engulfing.py` -- joins 559 trades to engulfing_scores for per-trade stop/target
- `backtest_results/account_b_trades_enriched.csv` -- 559 trades with engulfing stop/target columns
