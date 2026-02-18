"""
TEST-1: Adversarial Backtest v2

Two scenarios:
1. With trailing stop (0.5%)
2. Hold to close (no trailing stop)

Adversarial rules:
- Entry = HIGHEST price in 5-min window after signal
- Exit = LOWEST price in 5-min window after trigger
- Additional 0.1% slippage
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
from typing import Optional, List, Tuple

import pandas as pd

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

MIN_SCORE = 10
REQUIRED_TREND = 1
ENTRY_WINDOW_MINUTES = 5
EXIT_WINDOW_MINUTES = 5
ADDITIONAL_SLIPPAGE = 0.001


def load_signals():
    """Load scored signals with outcomes."""
    with open(RESULTS_DIR / "e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
        scored = json.load(f)
    with open(RESULTS_DIR / "e2e_backtest_with_outcomes.json") as f:
        outcomes = json.load(f)

    outcomes_lookup = {(s['symbol'], s['detection_time'][:16]): s for s in outcomes['signals']}

    signals = []
    for s in scored['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        if key in outcomes_lookup:
            o = outcomes_lookup[key]
            if o.get('pct_to_close') is not None and not o.get('filtered_out'):
                merged = {**s, **o}
                merged['detection_dt'] = datetime.fromisoformat(s['detection_time'])
                signals.append(merged)

    return signals


def load_minute_bars(trade_date: date) -> dict:
    """Load minute bars for a date."""
    file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
    if not file_path.exists():
        return {}

    df = pd.read_csv(file_path, compression='gzip')
    df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

    return {ticker: group.sort_values('timestamp').reset_index(drop=True)
            for ticker, group in df.groupby('ticker')}


def adversarial_entry(bars_df, signal_time):
    """Entry = HIGHEST price in 5-min window."""
    window_end = signal_time + timedelta(minutes=ENTRY_WINDOW_MINUTES)
    mask = (bars_df['timestamp'] >= signal_time) & (bars_df['timestamp'] < window_end)
    window = bars_df[mask]

    if len(window) == 0:
        after = bars_df[bars_df['timestamp'] >= signal_time]
        if len(after) == 0:
            return None, None
        return after.iloc[0]['high'], after.iloc[0]['timestamp']

    max_idx = window['high'].idxmax()
    return window.loc[max_idx, 'high'], window.loc[max_idx, 'timestamp']


def adversarial_exit(bars_df, exit_time):
    """Exit = LOWEST price in 5-min window."""
    window_end = exit_time + timedelta(minutes=EXIT_WINDOW_MINUTES)
    mask = (bars_df['timestamp'] >= exit_time) & (bars_df['timestamp'] < window_end)
    window = bars_df[mask]

    if len(window) == 0:
        after = bars_df[bars_df['timestamp'] >= exit_time]
        if len(after) == 0:
            return None
        return after.iloc[0]['low']

    return window['low'].min()


def simulate_hold_to_close(bars_df, entry_time, market_close):
    """Simply hold until market close."""
    mask = bars_df['timestamp'] <= market_close
    eod = bars_df[mask]
    if len(eod) == 0:
        return None
    return eod.iloc[-1]['timestamp']


def simulate_trailing_stop(bars_df, entry_time, entry_price, market_close, trail_pct=0.5, hard_stop_pct=2.0):
    """Simulate trailing stop, return exit time and reason."""
    mask = (bars_df['timestamp'] > entry_time) & (bars_df['timestamp'] <= market_close)
    holding = bars_df[mask].sort_values('timestamp')

    if len(holding) == 0:
        return market_close, 'eod'

    hwm = entry_price
    for _, bar in holding.iterrows():
        if bar['high'] > hwm:
            hwm = bar['high']

        trail_level = hwm * (1 - trail_pct / 100)
        hard_stop = entry_price * (1 - hard_stop_pct / 100)

        if bar['low'] <= hard_stop:
            return bar['timestamp'], 'hard_stop'

        if hwm > entry_price and bar['low'] <= trail_level:
            return bar['timestamp'], 'trail'

    return market_close, 'eod'


def run_test(signals, use_trailing_stop=True, trail_pct=0.5):
    """Run adversarial test with or without trailing stop."""

    filtered = [s for s in signals
                if s.get('trend') == REQUIRED_TREND and s.get('score', 0) >= MIN_SCORE]

    by_date = defaultdict(list)
    for s in filtered:
        by_date[s['detection_dt'].date()].append(s)

    results = []

    for trade_date in sorted(by_date.keys()):
        day_bars = load_minute_bars(trade_date)
        market_close = datetime.combine(trade_date, dt_time(15, 55))

        for sig in by_date[trade_date]:
            if sig['symbol'] not in day_bars:
                continue

            bars = day_bars[sig['symbol']]
            signal_time = sig['detection_dt']

            # Adversarial entry
            entry_price, entry_time = adversarial_entry(bars, signal_time)
            if entry_price is None:
                continue

            # Determine exit time
            if use_trailing_stop:
                exit_time, reason = simulate_trailing_stop(bars, entry_time, entry_price, market_close, trail_pct)
            else:
                exit_time = simulate_hold_to_close(bars, entry_time, market_close)
                reason = 'eod'

            if exit_time is None:
                continue

            # Adversarial exit
            exit_price = adversarial_exit(bars, exit_time)
            if exit_price is None:
                continue

            # Calculate return
            raw_ret = (exit_price - entry_price) / entry_price * 100
            adv_ret = raw_ret - (ADDITIONAL_SLIPPAGE * 100)

            results.append({
                'symbol': sig['symbol'],
                'entry': entry_price,
                'exit': exit_price,
                'reason': reason,
                'raw_return': raw_ret,
                'adv_return': adv_ret,
            })

    return results


def analyze(results, label):
    """Analyze results."""
    if not results:
        print(f"\n{label}: No trades")
        return

    returns = [r['adv_return'] for r in results]
    raw_returns = [r['raw_return'] for r in results]

    wr = len([r for r in returns if r > 0]) / len(returns) * 100
    avg = sum(returns) / len(returns)
    raw_wr = len([r for r in raw_returns if r > 0]) / len(raw_returns) * 100
    raw_avg = sum(raw_returns) / len(raw_returns)

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"Trades: {len(results):,}")
    print(f"Raw WR: {raw_wr:.1f}%, Raw Avg: {raw_avg:+.3f}%")
    print(f"Adversarial WR: {wr:.1f}%, Adversarial Avg: {avg:+.3f}%")

    # By exit reason
    by_reason = defaultdict(list)
    for r in results:
        by_reason[r['reason']].append(r['adv_return'])

    print(f"\nBy Exit Reason:")
    for reason in ['trail', 'hard_stop', 'eod']:
        if reason in by_reason:
            rets = by_reason[reason]
            rwr = len([r for r in rets if r > 0]) / len(rets) * 100
            ravg = sum(rets) / len(rets)
            print(f"  {reason:12}: {len(rets):>4} ({len(rets)/len(results)*100:>5.1f}%) | WR: {rwr:.1f}% | Avg: {ravg:+.3f}%")

    # Verdict
    if avg >= 0.20:
        print(f"\n[PASS] Adversarial avg {avg:+.3f}% >= +0.20%")
    elif avg > 0:
        print(f"\n[MARGINAL] Adversarial avg {avg:+.3f}% positive but < +0.20%")
    else:
        print(f"\n[FAIL] Adversarial avg {avg:+.3f}% is negative")

    return {'wr': wr, 'avg': avg, 'trades': len(results)}


def main():
    print("Loading signals...")
    signals = load_signals()
    print(f"Loaded {len(signals):,} signals")

    print("\nRunning adversarial tests...")

    # Test 1: With trailing stop (0.5%)
    results_trail = run_test(signals, use_trailing_stop=True, trail_pct=0.5)
    stats_trail = analyze(results_trail, "SCENARIO 1: Trailing Stop (0.5%)")

    # Test 2: Hold to close (no trailing stop)
    results_hold = run_test(signals, use_trailing_stop=False)
    stats_hold = analyze(results_hold, "SCENARIO 2: Hold to Close (No Trailing Stop)")

    # Test 3: Looser trailing stop (1.0%)
    results_trail_1 = run_test(signals, use_trailing_stop=True, trail_pct=1.0)
    stats_trail_1 = analyze(results_trail_1, "SCENARIO 3: Trailing Stop (1.0%)")

    # Test 4: Tighter trailing stop (0.3%)
    results_trail_03 = run_test(signals, use_trailing_stop=True, trail_pct=0.3)
    stats_trail_03 = analyze(results_trail_03, "SCENARIO 4: Trailing Stop (0.3%)")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"\n{'Scenario':<35} {'WR':<10} {'Avg Return':<15}")
    print("-"*60)
    if stats_trail:
        print(f"{'Trailing Stop 0.5%':<35} {stats_trail['wr']:.1f}%     {stats_trail['avg']:+.3f}%")
    if stats_trail_1:
        print(f"{'Trailing Stop 1.0%':<35} {stats_trail_1['wr']:.1f}%     {stats_trail_1['avg']:+.3f}%")
    if stats_trail_03:
        print(f"{'Trailing Stop 0.3%':<35} {stats_trail_03['wr']:.1f}%     {stats_trail_03['avg']:+.3f}%")
    if stats_hold:
        print(f"{'Hold to Close':<35} {stats_hold['wr']:.1f}%     {stats_hold['avg']:+.3f}%")

    print("\n" + "="*60)
    print("CONCLUSION")
    print("="*60)

    best = max([stats_trail, stats_trail_1, stats_trail_03, stats_hold], key=lambda x: x['avg'] if x else -999)
    if best and best['avg'] >= 0.20:
        print("\n[PASS] At least one scenario shows +0.20%+ under adversarial conditions")
    elif best and best['avg'] > 0:
        print(f"\n[MARGINAL] Best scenario shows {best['avg']:+.3f}% - edge exists but thin")
    else:
        print("\n[FAIL] No scenario shows positive returns under adversarial conditions")
        print("       The apparent edge is due to perfect timing assumptions")


if __name__ == "__main__":
    main()
