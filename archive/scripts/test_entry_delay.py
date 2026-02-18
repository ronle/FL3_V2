"""
Test entry delay separately (requires loading minute bars - slower)
"""
import json
import gzip
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

def load_signals():
    """Load scored signals with outcomes."""
    print("Loading signals...")
    with open(RESULTS_DIR / "e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
        scored = json.load(f)
    with open(RESULTS_DIR / "e2e_backtest_with_outcomes.json") as f:
        outcomes = json.load(f)

    outcomes_lookup = {}
    for s in outcomes['signals']:
        key = (s['symbol'], s['detection_time'][:16])
        outcomes_lookup[key] = s

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

    grouped = {}
    for ticker, group in df.groupby('ticker'):
        grouped[ticker] = group.sort_values('timestamp').reset_index(drop=True)
    return grouped


def get_price_after_delay(bars_df, signal_time: datetime, delay_minutes: int):
    """Get price after delay."""
    target_time = signal_time + timedelta(minutes=delay_minutes)
    mask = bars_df['timestamp'] <= target_time
    valid = bars_df[mask]
    if len(valid) == 0:
        return None
    return valid.iloc[-1]['close']


def main():
    signals = load_signals()

    # Filter to target strategy
    filtered = [s for s in signals
                if s.get('trend') == 1 and s.get('score', 0) >= 10]

    print(f"Target signals: {len(filtered):,}")

    # Group by date
    by_date = defaultdict(list)
    for s in filtered:
        by_date[s['detection_dt'].date()].append(s)

    delays = [0, 1, 5, 15, 30]
    results_by_delay = {d: [] for d in delays}

    print(f"\nProcessing {len(by_date)} days...")

    processed = 0
    for trade_date in sorted(by_date.keys()):
        day_bars = load_minute_bars(trade_date)
        day_sigs = by_date[trade_date]

        for sig in day_sigs:
            if sig['symbol'] not in day_bars:
                continue

            symbol_bars = day_bars[sig['symbol']]
            close_price = sig.get('day_close', 0)
            if close_price <= 0:
                continue

            for delay in delays:
                entry_price = get_price_after_delay(symbol_bars, sig['detection_dt'], delay)
                if entry_price and entry_price > 0:
                    ret = (close_price - entry_price) / entry_price * 100
                    results_by_delay[delay].append(ret)

        processed += 1
        if processed % 20 == 0:
            print(f"  Processed {processed}/{len(by_date)} days...")

    print("\n" + "="*60)
    print("ENTRY DELAY RESULTS")
    print("="*60)

    print(f"\n{'Delay':<10} {'Signals':<10} {'Win Rate':<12} {'Avg Return':<12}")
    print("-"*50)

    for delay in delays:
        rets = results_by_delay[delay]
        if rets:
            wr = len([r for r in rets if r > 0]) / len(rets) * 100
            avg = sum(rets) / len(rets)
            print(f"{delay} min     {len(rets):<10} {wr:<12.1f} {avg:<+12.2f}")

    # Check targets
    if results_by_delay[5]:
        rets = results_by_delay[5]
        wr = len([r for r in rets if r > 0]) / len(rets) * 100
        avg = sum(rets) / len(rets)
        passed = wr >= 55 and avg >= 0.20
        print(f"\n5-min delay: WR={wr:.1f}%, Avg={avg:+.2f}%")
        print(f"{'PASS' if passed else 'FAIL'}: 55%+ WR and +0.20% avg target")


if __name__ == "__main__":
    main()
