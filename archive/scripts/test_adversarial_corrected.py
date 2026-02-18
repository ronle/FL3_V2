"""
TEST-4: Adversarial test on corrected signals (look-ahead bias fixed).

Uses signals from signals_trend_corrected.json
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
import pandas as pd

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

MIN_SCORE = 10
ENTRY_WINDOW_MINUTES = 5
EXIT_WINDOW_MINUTES = 5
ADDITIONAL_SLIPPAGE = 0.001


def load_corrected_signals():
    """Load signals with corrected trend."""
    # First try corrected file
    corrected_path = RESULTS_DIR / "signals_trend_corrected.json"
    if corrected_path.exists():
        print("Loading corrected signals...")
        with open(corrected_path) as f:
            data = json.load(f)
        signals = data['signals']
    else:
        print("Corrected file not found, using original...")
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
                    signals.append({**s, **o})

    # Add datetime
    for s in signals:
        s['detection_dt'] = datetime.fromisoformat(s['detection_time'])

    print(f"Loaded {len(signals):,} signals")
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
            return None
        return after.iloc[0]['high']

    return window['high'].max()


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


def run_adversarial(signals, use_corrected_trend=True):
    """Run adversarial test with hold-to-close."""

    # Filter signals
    if use_corrected_trend:
        # Use corrected trend if available, else original
        filtered = [s for s in signals
                    if s.get('trend', s.get('trend_original', 0)) == 1
                    and s.get('score', 0) >= MIN_SCORE]
    else:
        # Use original trend
        filtered = [s for s in signals
                    if s.get('trend_original', s.get('trend', 0)) == 1
                    and s.get('score', 0) >= MIN_SCORE]

    print(f"\nFiltered signals: {len(filtered):,}")

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
            entry_price = adversarial_entry(bars, signal_time)
            if entry_price is None:
                continue

            # Hold to close - get EOD price
            eod_bars = bars[bars['timestamp'] <= market_close]
            if len(eod_bars) == 0:
                continue

            exit_time = eod_bars.iloc[-1]['timestamp']

            # Adversarial exit
            exit_price = adversarial_exit(bars, exit_time)
            if exit_price is None:
                exit_price = eod_bars.iloc[-1]['low']

            # Calculate return
            raw_ret = (exit_price - entry_price) / entry_price * 100
            adv_ret = raw_ret - (ADDITIONAL_SLIPPAGE * 100)

            results.append({
                'symbol': sig['symbol'],
                'date': trade_date.isoformat(),
                'raw_return': raw_ret,
                'adv_return': adv_ret,
            })

    return results


def analyze(results, label):
    """Analyze results."""
    if not results:
        print(f"\n{label}: No trades")
        return None

    returns = [r['adv_return'] for r in results]
    raw_returns = [r['raw_return'] for r in results]

    wr = len([r for r in returns if r > 0]) / len(returns) * 100
    avg = sum(returns) / len(returns)
    raw_wr = len([r for r in raw_returns if r > 0]) / len(raw_returns) * 100
    raw_avg = sum(raw_returns) / len(raw_returns)

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"Trades: {len(results):,} ({len(results)/126:.1f}/day)")
    print(f"Raw: WR={raw_wr:.1f}%, Avg={raw_avg:+.3f}%")
    print(f"Adversarial: WR={wr:.1f}%, Avg={avg:+.3f}%")

    if avg >= 0.20:
        print(f"\n[PASS] Adversarial avg {avg:+.3f}% >= +0.20%")
    elif avg > 0:
        print(f"\n[MARGINAL] Adversarial avg {avg:+.3f}% positive but < +0.20%")
    else:
        print(f"\n[FAIL] Adversarial avg {avg:+.3f}% is negative")

    return {'trades': len(results), 'wr': wr, 'avg': avg}


def main():
    signals = load_corrected_signals()

    # Count signals by trend status
    has_corrected = [s for s in signals if s.get('trend_corrected')]
    print(f"\nSignals with corrected trend: {len(has_corrected):,}")

    # Test with corrected trend
    print("\n" + "="*60)
    print("ADVERSARIAL TEST: CORRECTED TREND (Hold to Close)")
    print("="*60)

    results_corrected = run_adversarial(signals, use_corrected_trend=True)
    stats_corrected = analyze(results_corrected, "Corrected Trend")

    # Test with original trend for comparison
    print("\n" + "="*60)
    print("ADVERSARIAL TEST: ORIGINAL TREND (Hold to Close)")
    print("="*60)

    results_original = run_adversarial(signals, use_corrected_trend=False)
    stats_original = analyze(results_original, "Original Trend")

    # Summary
    print("\n" + "="*60)
    print("COMPARISON SUMMARY")
    print("="*60)

    print(f"\n{'Trend':<20} {'Trades':<10} {'WR':<10} {'Avg Return':<15}")
    print("-"*55)
    if stats_original:
        print(f"{'Original':<20} {stats_original['trades']:<10} {stats_original['wr']:.1f}%     {stats_original['avg']:+.3f}%")
    if stats_corrected:
        print(f"{'Corrected':<20} {stats_corrected['trades']:<10} {stats_corrected['wr']:.1f}%     {stats_corrected['avg']:+.3f}%")

    if stats_corrected and stats_corrected['avg'] >= 0.20:
        print("\n[PASS] Strategy valid with look-ahead bias fixed")
    elif stats_corrected and stats_corrected['avg'] > 0:
        print("\n[MARGINAL] Edge exists but reduced after look-ahead fix")
    else:
        print("\n[FAIL] Look-ahead fix reveals no real edge")


if __name__ == "__main__":
    main()
