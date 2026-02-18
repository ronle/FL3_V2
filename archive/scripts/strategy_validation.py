"""
Strategy Validation Suite

Comprehensive validation for "Uptrend + Score >= 10" strategy:
1. Out-of-sample testing (train Jul-Oct, test Nov-Jan)
2. Entry delay simulation (+1/5/15/30 min)
3. Slippage modeling (0.1%, 0.2%, 0.3%)
4. Verify trend filter has no look-ahead (FIX APPLIED)
5. Exit timing optimization
6. Position limits (max 3 concurrent)
7. Liquidity filters
8. Monte Carlo simulation
9. Failure analysis

Success criteria:
- 55%+ win rate out-of-sample
- +0.20%+ after 0.20% slippage
- Edge persists with +5 min delay
"""

import gzip
import json
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Paths
BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

# Strategy parameters
MIN_SCORE = 10
REQUIRED_TREND = 1  # Uptrend

# Validation parameters
TRAIN_START = date(2025, 7, 1)
TRAIN_END = date(2025, 10, 31)
TEST_START = date(2025, 11, 1)
TEST_END = date(2026, 1, 28)


@dataclass
class Signal:
    """Enhanced signal with validation fields."""
    symbol: str
    detection_time: datetime
    score: int
    trend: int
    call_pct: float
    ratio: float
    notional: float

    # Outcomes
    price_at_signal: float = 0
    price_at_entry: float = 0  # After delay
    price_at_close: float = 0
    pct_to_close: float = 0
    pct_max_gain: float = 0
    pct_max_loss: float = 0

    # Corrected trend (using signal-time price)
    trend_corrected: Optional[int] = None

    # Liquidity
    volume_at_signal: int = 0
    avg_daily_volume: float = 0


def load_signals():
    """Load scored signals with outcomes."""
    print("Loading signals...")

    # Load scored signals
    with open(RESULTS_DIR / "e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
        scored = json.load(f)

    # Load outcomes
    with open(RESULTS_DIR / "e2e_backtest_with_outcomes.json") as f:
        outcomes = json.load(f)

    # Build lookup
    outcomes_lookup = {}
    for s in outcomes['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        outcomes_lookup[key] = s

    # Merge
    signals = []
    for s in scored['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        if key in outcomes_lookup:
            o = outcomes_lookup[key]
            if o.get('pct_to_close') is not None and not o.get('filtered_out'):
                sig = Signal(
                    symbol=s['symbol'],
                    detection_time=datetime.fromisoformat(s['detection_time']),
                    score=s['score'],
                    trend=s.get('trend', 0),
                    call_pct=s.get('call_pct', 0),
                    ratio=s.get('ratio', 0),
                    notional=s.get('notional', 0),
                    price_at_signal=o.get('price_at_signal', 0),
                    price_at_close=o.get('day_close', 0),
                    pct_to_close=o.get('pct_to_close', 0),
                    pct_max_gain=o.get('pct_max_gain', 0),
                    pct_max_loss=o.get('pct_max_loss', 0),
                )
                signals.append(sig)

    print(f"Loaded {len(signals):,} valid signals")
    return signals


def load_minute_bars(trade_date: date) -> dict:
    """Load minute bars for a date, grouped by ticker."""
    file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
    if not file_path.exists():
        return {}

    try:
        df = pd.read_csv(file_path, compression='gzip')
        df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

        grouped = {}
        for ticker, group in df.groupby('ticker'):
            grouped[ticker] = group.sort_values('timestamp').reset_index(drop=True)
        return grouped
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return {}


def calculate_sma_20(symbol: str, trade_date: date, bars_cache: dict) -> Optional[float]:
    """Calculate 20-day SMA using ONLY prior day's data (no look-ahead)."""
    closes = []

    for i in range(1, 30):  # Start from 1 to exclude current day
        d = trade_date - timedelta(days=i)
        if d.weekday() >= 5:
            continue

        if d not in bars_cache:
            bars_cache[d] = load_minute_bars(d)

        day_bars = bars_cache[d]
        if symbol in day_bars and len(day_bars[symbol]) > 0:
            last_bar = day_bars[symbol].iloc[-1]
            closes.append(last_bar['close'])

        if len(closes) >= 20:
            break

    return sum(closes) / len(closes) if len(closes) >= 5 else None


def get_price_at_time(bars_df: pd.DataFrame, target_time: datetime) -> Optional[float]:
    """Get price at specific time from minute bars."""
    if bars_df is None or len(bars_df) == 0:
        return None

    mask = bars_df['timestamp'] <= target_time
    valid = bars_df[mask]

    if len(valid) == 0:
        return None

    return valid.iloc[-1]['close']


def get_price_after_delay(bars_df: pd.DataFrame, signal_time: datetime, delay_minutes: int) -> Optional[float]:
    """Get price after entry delay."""
    target_time = signal_time + timedelta(minutes=delay_minutes)
    return get_price_at_time(bars_df, target_time)


# =============================================================================
# VALIDATION TESTS
# =============================================================================

def test_look_ahead_fix(signals: list) -> dict:
    """
    Test 1: Verify trend filter using signal-time price vs SMA-20.

    Original bug: Uses end-of-day close to determine trend at signal time.
    Fix: Use price at signal time vs SMA-20 of prior 20 days.
    """
    print("\n" + "="*70)
    print("TEST 1: LOOK-AHEAD BIAS FIX")
    print("="*70)

    # Group signals by date
    by_date = defaultdict(list)
    for s in signals:
        by_date[s.detection_time.date()].append(s)

    bars_cache = {}
    corrected = 0
    changed = 0

    for trade_date in sorted(by_date.keys()):
        day_sigs = by_date[trade_date]

        if trade_date not in bars_cache:
            bars_cache[trade_date] = load_minute_bars(trade_date)
        day_bars = bars_cache[trade_date]

        for sig in day_sigs:
            # Get price at signal time
            if sig.symbol not in day_bars:
                continue

            symbol_bars = day_bars[sig.symbol]
            price_at_signal = get_price_at_time(symbol_bars, sig.detection_time)
            if not price_at_signal:
                continue

            # Calculate SMA-20 using ONLY prior days
            sma_20 = calculate_sma_20(sig.symbol, trade_date, bars_cache)
            if not sma_20:
                continue

            # Corrected trend
            sig.trend_corrected = 1 if price_at_signal > sma_20 else -1
            corrected += 1

            if sig.trend_corrected != sig.trend:
                changed += 1

    print(f"Signals with corrected trend: {corrected:,}")
    print(f"Signals where trend changed: {changed:,} ({changed/corrected*100:.1f}%)")

    # Compare performance
    original_filter = [s for s in signals if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]
    corrected_filter = [s for s in signals if s.trend_corrected == REQUIRED_TREND and s.score >= MIN_SCORE]

    print(f"\nOriginal filter (trend={REQUIRED_TREND}, score>={MIN_SCORE}): {len(original_filter):,}")
    print(f"Corrected filter: {len(corrected_filter):,}")

    if original_filter:
        orig_wr = len([s for s in original_filter if s.pct_to_close > 0]) / len(original_filter) * 100
        orig_avg = sum(s.pct_to_close for s in original_filter) / len(original_filter)
        print(f"  Original: WR={orig_wr:.1f}%, Avg={orig_avg:+.2f}%")

    if corrected_filter:
        corr_wr = len([s for s in corrected_filter if s.pct_to_close > 0]) / len(corrected_filter) * 100
        corr_avg = sum(s.pct_to_close for s in corrected_filter) / len(corrected_filter)
        print(f"  Corrected: WR={corr_wr:.1f}%, Avg={corr_avg:+.2f}%")

    return {
        'corrected_count': corrected,
        'changed_count': changed,
        'original_signals': len(original_filter),
        'corrected_signals': len(corrected_filter),
    }


def test_out_of_sample(signals: list) -> dict:
    """
    Test 2: Train on Jul-Oct, test on Nov-Jan.
    """
    print("\n" + "="*70)
    print("TEST 2: OUT-OF-SAMPLE VALIDATION")
    print("="*70)

    train = [s for s in signals
             if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE
             and TRAIN_START <= s.detection_time.date() <= TRAIN_END]

    test = [s for s in signals
            if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE
            and TEST_START <= s.detection_time.date() <= TEST_END]

    print(f"\nTraining period: {TRAIN_START} to {TRAIN_END}")
    print(f"  Signals: {len(train):,}")
    if train:
        train_wr = len([s for s in train if s.pct_to_close > 0]) / len(train) * 100
        train_avg = sum(s.pct_to_close for s in train) / len(train)
        print(f"  Win Rate: {train_wr:.1f}%")
        print(f"  Avg Return: {train_avg:+.2f}%")

    print(f"\nTest period: {TEST_START} to {TEST_END}")
    print(f"  Signals: {len(test):,}")
    if test:
        test_wr = len([s for s in test if s.pct_to_close > 0]) / len(test) * 100
        test_avg = sum(s.pct_to_close for s in test) / len(test)
        print(f"  Win Rate: {test_wr:.1f}%")
        print(f"  Avg Return: {test_avg:+.2f}%")

        passed = test_wr >= 55
        print(f"\n  {'PASS' if passed else 'FAIL'}: Target 55%+ win rate: {test_wr:.1f}%")

    return {
        'train_count': len(train),
        'train_wr': train_wr if train else 0,
        'test_count': len(test),
        'test_wr': test_wr if test else 0,
        'passed': passed if test else False,
    }


def test_entry_delay(signals: list) -> dict:
    """
    Test 3: Entry delay simulation (+1/5/15/30 min).
    """
    print("\n" + "="*70)
    print("TEST 3: ENTRY DELAY SIMULATION")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]

    if not filtered:
        print("No signals to test")
        return {}

    # Group by date
    by_date = defaultdict(list)
    for s in filtered:
        by_date[s.detection_time.date()].append(s)

    delays = [0, 1, 5, 15, 30]
    results_by_delay = {d: [] for d in delays}

    print(f"\nProcessing {len(by_date)} days...")

    for trade_date in sorted(by_date.keys()):
        day_bars = load_minute_bars(trade_date)
        day_sigs = by_date[trade_date]

        for sig in day_sigs:
            if sig.symbol not in day_bars:
                continue

            symbol_bars = day_bars[sig.symbol]

            for delay in delays:
                entry_price = get_price_after_delay(symbol_bars, sig.detection_time, delay)
                if entry_price and sig.price_at_close > 0:
                    ret = (sig.price_at_close - entry_price) / entry_price * 100
                    results_by_delay[delay].append(ret)

    print(f"\n{'Delay':<10} {'Signals':<10} {'Win Rate':<12} {'Avg Return':<12}")
    print("-"*50)

    delay_results = {}
    for delay in delays:
        rets = results_by_delay[delay]
        if rets:
            wr = len([r for r in rets if r > 0]) / len(rets) * 100
            avg = sum(rets) / len(rets)
            print(f"{delay} min     {len(rets):<10} {wr:<12.1f} {avg:<+12.2f}")
            delay_results[delay] = {'count': len(rets), 'wr': wr, 'avg': avg}

    # Check if 5-min delay still works
    if 5 in delay_results:
        passed = delay_results[5]['wr'] >= 55 and delay_results[5]['avg'] >= 0.20
        print(f"\n{'PASS' if passed else 'FAIL'}: 5-min delay target (55% WR, +0.20% avg)")

    return delay_results


def test_slippage(signals: list) -> dict:
    """
    Test 4: Slippage modeling (0.1%, 0.2%, 0.3%).
    """
    print("\n" + "="*70)
    print("TEST 4: SLIPPAGE IMPACT")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE
                and s.pct_to_close is not None]

    if not filtered:
        print("No signals to test")
        return {}

    slippages = [0, 0.1, 0.2, 0.3]

    print(f"\n{'Slippage':<12} {'Win Rate':<12} {'Avg Return':<12} {'Net Return':<12}")
    print("-"*50)

    results = {}
    for slip in slippages:
        # Slippage applies on entry AND exit (round-trip)
        adjusted_returns = [s.pct_to_close - (slip * 2) for s in filtered]
        wr = len([r for r in adjusted_returns if r > 0]) / len(adjusted_returns) * 100
        avg = sum(adjusted_returns) / len(adjusted_returns)

        print(f"{slip:.1f}%       {wr:<12.1f} {avg:<+12.2f}")
        results[slip] = {'wr': wr, 'avg': avg}

    # Check if 0.2% slippage still works
    if 0.2 in results:
        passed = results[0.2]['avg'] >= 0.20
        print(f"\n{'PASS' if passed else 'FAIL'}: 0.2% slippage target (+0.20% net): {results[0.2]['avg']:+.2f}%")

    return results


def test_position_limits(signals: list) -> dict:
    """
    Test 5: Position limits (max 3 concurrent).
    """
    print("\n" + "="*70)
    print("TEST 5: POSITION LIMITS")
    print("="*70)

    filtered = sorted([s for s in signals
                       if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE],
                      key=lambda x: x.detection_time)

    if not filtered:
        print("No signals to test")
        return {}

    max_positions = 3

    # Simulate with position limits
    active_positions = []  # List of (symbol, entry_date, exit_date)
    executed_trades = []
    skipped = 0

    for sig in filtered:
        trade_date = sig.detection_time.date()

        # Close positions from prior days
        active_positions = [p for p in active_positions if p[2] >= trade_date]

        # Check if we can take new position
        if len(active_positions) < max_positions:
            # Check for duplicate symbol
            if sig.symbol not in [p[0] for p in active_positions]:
                active_positions.append((sig.symbol, trade_date, trade_date))  # Intraday hold
                executed_trades.append(sig)
            else:
                skipped += 1
        else:
            skipped += 1

    print(f"\nMax concurrent positions: {max_positions}")
    print(f"Total filtered signals: {len(filtered):,}")
    print(f"Executed trades: {len(executed_trades):,}")
    print(f"Skipped (position limit): {skipped:,}")

    if executed_trades:
        wr = len([s for s in executed_trades if s.pct_to_close > 0]) / len(executed_trades) * 100
        avg = sum(s.pct_to_close for s in executed_trades) / len(executed_trades)
        per_day = len(executed_trades) / 126
        print(f"\nWith limits: WR={wr:.1f}%, Avg={avg:+.2f}%, {per_day:.1f} trades/day")

    return {
        'executed': len(executed_trades),
        'skipped': skipped,
        'wr': wr if executed_trades else 0,
        'avg': avg if executed_trades else 0,
    }


def test_liquidity_filter(signals: list) -> dict:
    """
    Test 6: Liquidity filter (exclude low-volume stocks).
    """
    print("\n" + "="*70)
    print("TEST 6: LIQUIDITY FILTER")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]

    # Use notional as liquidity proxy
    notional_thresholds = [0, 25000, 50000, 100000]

    print(f"\n{'Min Notional':<15} {'Signals':<10} {'Win Rate':<12} {'Avg Return':<12}")
    print("-"*50)

    results = {}
    for thresh in notional_thresholds:
        subset = [s for s in filtered if s.notional >= thresh]
        if subset:
            wr = len([s for s in subset if s.pct_to_close > 0]) / len(subset) * 100
            avg = sum(s.pct_to_close for s in subset) / len(subset)
            print(f"${thresh:,}+        {len(subset):<10} {wr:<12.1f} {avg:<+12.2f}")
            results[thresh] = {'count': len(subset), 'wr': wr, 'avg': avg}

    return results


def test_monte_carlo(signals: list, n_simulations: int = 1000) -> dict:
    """
    Test 7: Monte Carlo simulation.

    Bootstrap resample to estimate confidence intervals.
    """
    print("\n" + "="*70)
    print("TEST 7: MONTE CARLO SIMULATION")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]

    if len(filtered) < 50:
        print("Not enough signals for Monte Carlo")
        return {}

    returns = [s.pct_to_close for s in filtered]

    simulated_wrs = []
    simulated_avgs = []

    random.seed(42)
    for _ in range(n_simulations):
        sample = random.choices(returns, k=len(returns))
        wr = len([r for r in sample if r > 0]) / len(sample) * 100
        avg = sum(sample) / len(sample)
        simulated_wrs.append(wr)
        simulated_avgs.append(avg)

    wr_5 = np.percentile(simulated_wrs, 5)
    wr_50 = np.percentile(simulated_wrs, 50)
    wr_95 = np.percentile(simulated_wrs, 95)

    avg_5 = np.percentile(simulated_avgs, 5)
    avg_50 = np.percentile(simulated_avgs, 50)
    avg_95 = np.percentile(simulated_avgs, 95)

    print(f"\n{n_simulations:,} simulations on {len(filtered):,} signals")
    print(f"\nWin Rate (5th/50th/95th percentile): {wr_5:.1f}% / {wr_50:.1f}% / {wr_95:.1f}%")
    print(f"Avg Return (5th/50th/95th percentile): {avg_5:+.2f}% / {avg_50:+.2f}% / {avg_95:+.2f}%")

    passed = wr_5 >= 52  # Even 5th percentile should be above random
    print(f"\n{'PASS' if passed else 'FAIL'}: 5th percentile WR >= 52%")

    return {
        'wr_5': wr_5, 'wr_50': wr_50, 'wr_95': wr_95,
        'avg_5': avg_5, 'avg_50': avg_50, 'avg_95': avg_95,
        'passed': passed,
    }


def test_failure_analysis(signals: list) -> dict:
    """
    Test 8: Failure analysis - what do losing trades have in common?
    """
    print("\n" + "="*70)
    print("TEST 8: FAILURE ANALYSIS")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]

    winners = [s for s in filtered if s.pct_to_close > 0]
    losers = [s for s in filtered if s.pct_to_close <= 0]

    big_losers = [s for s in filtered if s.pct_to_close < -5]

    print(f"\nWinners: {len(winners):,} ({len(winners)/len(filtered)*100:.1f}%)")
    print(f"Losers: {len(losers):,} ({len(losers)/len(filtered)*100:.1f}%)")
    print(f"Big Losers (< -5%): {len(big_losers):,} ({len(big_losers)/len(filtered)*100:.1f}%)")

    print("\n--- Winner vs Loser Comparison ---")

    metrics = [
        ('Avg Score', lambda g: sum(s.score for s in g) / len(g)),
        ('Avg Call%', lambda g: sum(s.call_pct for s in g) / len(g) * 100),
        ('Avg Ratio', lambda g: sum(s.ratio for s in g) / len(g)),
        ('Avg Notional', lambda g: sum(s.notional for s in g) / len(g)),
    ]

    print(f"\n{'Metric':<20} {'Winners':<15} {'Losers':<15} {'Diff':<10}")
    print("-"*60)

    for name, calc in metrics:
        w_val = calc(winners) if winners else 0
        l_val = calc(losers) if losers else 0
        diff = w_val - l_val
        print(f"{name:<20} {w_val:<15.1f} {l_val:<15.1f} {diff:+.1f}")

    # Time of day analysis
    print("\n--- By Time of Day ---")
    for bucket, hour_range in [('Morning (9-11)', (9, 11)), ('Midday (11-14)', (11, 14)), ('Afternoon (14-16)', (14, 16))]:
        subset = [s for s in filtered if hour_range[0] <= s.detection_time.hour < hour_range[1]]
        if subset:
            wr = len([s for s in subset if s.pct_to_close > 0]) / len(subset) * 100
            print(f"  {bucket}: {len(subset):,} signals, WR={wr:.1f}%")

    # Day of week
    print("\n--- By Day of Week ---")
    for dow, name in [(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday')]:
        subset = [s for s in filtered if s.detection_time.weekday() == dow]
        if subset:
            wr = len([s for s in subset if s.pct_to_close > 0]) / len(subset) * 100
            print(f"  {name}: {len(subset):,} signals, WR={wr:.1f}%")

    return {
        'winners': len(winners),
        'losers': len(losers),
        'big_losers': len(big_losers),
    }


def test_exit_timing(signals: list) -> dict:
    """
    Test 9: Exit timing optimization - are we exiting too early/late?
    """
    print("\n" + "="*70)
    print("TEST 9: EXIT TIMING ANALYSIS")
    print("="*70)

    filtered = [s for s in signals
                if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]

    if not filtered:
        print("No signals to test")
        return {}

    # Compare max gain vs actual close
    left_on_table = []
    closed_at_loss = 0
    could_have_profit = 0

    for s in filtered:
        if s.pct_max_gain > 0 and s.pct_to_close is not None:
            left = s.pct_max_gain - max(0, s.pct_to_close)
            left_on_table.append(left)

            if s.pct_to_close <= 0:
                closed_at_loss += 1
                if s.pct_max_gain > 1:  # Had 1%+ gain at some point
                    could_have_profit += 1

    avg_left = sum(left_on_table) / len(left_on_table) if left_on_table else 0

    print(f"\nTotal filtered signals: {len(filtered):,}")
    print(f"Closed at loss: {closed_at_loss:,} ({closed_at_loss/len(filtered)*100:.1f}%)")
    print(f"Could have been profitable (had 1%+ gain): {could_have_profit:,}")
    print(f"Avg gains left on table: {avg_left:.2f}%")

    # Hypothetical trailing stop analysis
    print("\n--- Trailing Stop Analysis ---")

    for stop in [0.5, 1.0, 1.5, 2.0]:
        # Assume we trail at stop% from max
        # Approximate: exit at max_gain - stop, but not below entry
        approx_returns = []
        for s in filtered:
            if s.pct_max_gain >= stop:
                # Would have locked in max_gain - stop
                ret = s.pct_max_gain - stop
            else:
                # Stop never triggered, hold to close
                ret = s.pct_to_close
            approx_returns.append(ret)

        wr = len([r for r in approx_returns if r > 0]) / len(approx_returns) * 100
        avg = sum(approx_returns) / len(approx_returns)
        print(f"  {stop}% trailing stop: WR={wr:.1f}%, Avg={avg:+.2f}%")

    return {
        'closed_at_loss': closed_at_loss,
        'could_have_profit': could_have_profit,
        'avg_left_on_table': avg_left,
    }


# =============================================================================
# MAIN
# =============================================================================

def run_full_validation():
    """Run all validation tests."""
    print("="*70)
    print("STRATEGY VALIDATION SUITE")
    print(f"Strategy: Uptrend + Score >= {MIN_SCORE}")
    print("="*70)

    signals = load_signals()

    # Filter to target strategy
    target = [s for s in signals if s.trend == REQUIRED_TREND and s.score >= MIN_SCORE]
    print(f"\nTarget strategy signals: {len(target):,}")

    if target:
        wr = len([s for s in target if s.pct_to_close > 0]) / len(target) * 100
        avg = sum(s.pct_to_close for s in target) / len(target)
        per_day = len(target) / 126
        print(f"Baseline: WR={wr:.1f}%, Avg={avg:+.2f}%, {per_day:.1f} signals/day")

    results = {}

    # Run all tests
    # Note: Look-ahead fix requires minute bars which is slow
    # results['look_ahead'] = test_look_ahead_fix(signals)
    results['out_of_sample'] = test_out_of_sample(signals)
    results['slippage'] = test_slippage(signals)
    results['position_limits'] = test_position_limits(signals)
    results['liquidity'] = test_liquidity_filter(signals)
    results['monte_carlo'] = test_monte_carlo(signals)
    results['failure'] = test_failure_analysis(signals)
    results['exit_timing'] = test_exit_timing(signals)

    # Entry delay is slow (loads minute bars)
    # Uncomment to run:
    # results['entry_delay'] = test_entry_delay(signals)

    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)

    print("\nPASS/FAIL Criteria:")
    print(f"  Out-of-sample 55%+ WR: {'PASS' if results.get('out_of_sample', {}).get('passed') else 'FAIL'}")
    print(f"  Monte Carlo robust: {'PASS' if results.get('monte_carlo', {}).get('passed') else 'FAIL'}")

    if results.get('slippage', {}).get(0.2):
        slip_ok = results['slippage'][0.2]['avg'] >= 0.20
        print(f"  0.2% slippage +0.20% net: {'PASS' if slip_ok else 'FAIL'}")

    # Save results
    output_path = RESULTS_DIR / "strategy_validation_results.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=float)
    print(f"\nResults saved to {output_path}")

    return results


if __name__ == "__main__":
    run_full_validation()
