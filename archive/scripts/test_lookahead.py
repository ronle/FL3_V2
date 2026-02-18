"""
Test look-ahead bias in trend filter.

The original code uses day's closing price for trend calculation,
but signals fire during the day. This test:
1. Recalculates trend using only data available at signal time
2. Compares original vs corrected performance
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"


def load_signals():
    """Load scored signals with outcomes."""
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


def get_price_at_time(bars_df, target_time: datetime):
    """Get price at specific time."""
    mask = bars_df['timestamp'] <= target_time
    valid = bars_df[mask]
    if len(valid) == 0:
        return None
    return valid.iloc[-1]['close']


def get_prior_closes(symbol: str, trade_date: date, bars_cache: dict, n_days: int = 20):
    """Get closing prices from prior N trading days."""
    closes = []
    d = trade_date - timedelta(days=1)  # Start from yesterday

    for _ in range(40):  # Look back up to 40 calendar days
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue

        if d not in bars_cache:
            bars_cache[d] = load_minute_bars(d)

        day_bars = bars_cache[d]
        if symbol in day_bars and len(day_bars[symbol]) > 0:
            last_bar = day_bars[symbol].iloc[-1]
            closes.append(last_bar['close'])

            if len(closes) >= n_days:
                break

        d -= timedelta(days=1)

    return closes


def main():
    print("="*70)
    print("LOOK-AHEAD BIAS VERIFICATION")
    print("="*70)

    signals = load_signals()
    print(f"\nTotal valid signals: {len(signals):,}")

    # Filter to our strategy
    strategy_sigs = [s for s in signals if s.get('trend') == 1 and s.get('score', 0) >= 10]
    print(f"Strategy signals (trend=1, score>=10): {len(strategy_sigs):,}")

    # Group by date
    by_date = defaultdict(list)
    for s in strategy_sigs:
        by_date[s['detection_dt'].date()].append(s)

    bars_cache = {}
    corrected_trend = []
    changed = 0
    total_checked = 0

    print(f"\nChecking {len(by_date)} trading days...")

    for trade_date in sorted(by_date.keys())[:30]:  # Sample 30 days for speed
        if trade_date not in bars_cache:
            bars_cache[trade_date] = load_minute_bars(trade_date)

        day_bars = bars_cache[trade_date]
        day_sigs = by_date[trade_date]

        for sig in day_sigs:
            if sig['symbol'] not in day_bars:
                continue

            symbol_bars = day_bars[sig['symbol']]

            # Get price at signal time
            price_at_signal = get_price_at_time(symbol_bars, sig['detection_dt'])
            if not price_at_signal:
                continue

            # Get SMA-20 from prior days only (no look-ahead)
            prior_closes = get_prior_closes(sig['symbol'], trade_date, bars_cache)
            if len(prior_closes) < 5:
                continue

            sma_20 = sum(prior_closes) / len(prior_closes)

            # Corrected trend
            corrected = 1 if price_at_signal > sma_20 else -1

            total_checked += 1
            if corrected != sig.get('trend', 0):
                changed += 1

            corrected_trend.append({
                'symbol': sig['symbol'],
                'date': trade_date.isoformat(),
                'original_trend': sig.get('trend', 0),
                'corrected_trend': corrected,
                'price_at_signal': price_at_signal,
                'sma_20': sma_20,
                'pct_to_close': sig.get('pct_to_close', 0),
            })

    print(f"\nChecked: {total_checked:,} signals")
    print(f"Trend changed after correction: {changed:,} ({changed/total_checked*100:.1f}%)")

    # Compare performance
    print("\n" + "-"*50)
    print("PERFORMANCE COMPARISON")
    print("-"*50)

    # Original (all had trend=1 by definition)
    orig_returns = [s['pct_to_close'] for s in corrected_trend]
    orig_wr = len([r for r in orig_returns if r > 0]) / len(orig_returns) * 100
    orig_avg = sum(orig_returns) / len(orig_returns)

    print(f"\nOriginal (trend=1 via EOD close):")
    print(f"  Signals: {len(orig_returns)}")
    print(f"  Win Rate: {orig_wr:.1f}%")
    print(f"  Avg Return: {orig_avg:+.2f}%")

    # Corrected - still uptrend
    still_uptrend = [s for s in corrected_trend if s['corrected_trend'] == 1]
    if still_uptrend:
        corr_returns = [s['pct_to_close'] for s in still_uptrend]
        corr_wr = len([r for r in corr_returns if r > 0]) / len(corr_returns) * 100
        corr_avg = sum(corr_returns) / len(corr_returns)

        print(f"\nCorrected uptrend (price at signal > SMA-20):")
        print(f"  Signals: {len(still_uptrend)}")
        print(f"  Win Rate: {corr_wr:.1f}%")
        print(f"  Avg Return: {corr_avg:+.2f}%")

    # Wrongly classified as uptrend
    false_uptrend = [s for s in corrected_trend if s['corrected_trend'] == -1]
    if false_uptrend:
        false_returns = [s['pct_to_close'] for s in false_uptrend]
        false_wr = len([r for r in false_returns if r > 0]) / len(false_returns) * 100
        false_avg = sum(false_returns) / len(false_returns)

        print(f"\nFalse uptrend (was actually downtrend at signal time):")
        print(f"  Signals: {len(false_uptrend)}")
        print(f"  Win Rate: {false_wr:.1f}%")
        print(f"  Avg Return: {false_avg:+.2f}%")

    # Summary
    print("\n" + "="*70)
    print("CONCLUSION")
    print("="*70)

    if changed / total_checked > 0.1:  # More than 10% changed
        print("\nWARNING: Significant look-ahead bias detected!")
        print(f"  {changed/total_checked*100:.1f}% of signals had wrong trend classification")
    else:
        print("\nLook-ahead bias is minimal (< 10% of signals affected)")

    if still_uptrend and len(still_uptrend) >= 10:
        if corr_wr >= 55:
            print(f"\nGOOD: Corrected uptrend signals still achieve {corr_wr:.1f}% win rate")
        else:
            print(f"\nCONCERN: Corrected win rate dropped to {corr_wr:.1f}%")


if __name__ == "__main__":
    main()
