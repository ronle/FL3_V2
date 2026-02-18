"""
TEST-3: Fix Look-Ahead Bias

Problem: Original trend calculation uses EOD close price, but signals fire during the day.
Fix: Use price at signal time vs SMA-20 of PRIOR days only.

This script:
1. Loads existing signals
2. Recalculates trend using correct (no look-ahead) method
3. Compares original vs corrected signals
4. Saves corrected signals for downstream testing
"""

import json
import gzip
from collections import defaultdict
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
from typing import Optional, Dict, List
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

    # Build lookup
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

    print(f"Loaded {len(signals):,} valid signals")
    return signals


class DailyBarsCache:
    """Cache for daily OHLC bars aggregated from minute data."""

    def __init__(self):
        self.cache: Dict[date, Dict[str, dict]] = {}  # date -> {symbol -> {o,h,l,c}}

    def load_day(self, trade_date: date):
        """Load minute bars and aggregate to daily OHLC."""
        if trade_date in self.cache:
            return

        file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
        if not file_path.exists():
            self.cache[trade_date] = {}
            return

        try:
            df = pd.read_csv(file_path, compression='gzip')

            daily = {}
            for ticker, group in df.groupby('ticker'):
                daily[ticker] = {
                    'o': group['open'].iloc[0],
                    'h': group['high'].max(),
                    'l': group['low'].min(),
                    'c': group['close'].iloc[-1],
                }

            self.cache[trade_date] = daily
        except Exception as e:
            print(f"Error loading {trade_date}: {e}")
            self.cache[trade_date] = {}

    def get_prior_closes(self, symbol: str, trade_date: date, n_days: int = 20) -> List[float]:
        """Get closing prices from prior N trading days (excluding trade_date)."""
        closes = []
        d = trade_date - timedelta(days=1)  # Start from YESTERDAY

        for _ in range(40):  # Look back up to 40 calendar days
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue

            self.load_day(d)

            if d in self.cache and symbol in self.cache[d]:
                closes.append(self.cache[d][symbol]['c'])
                if len(closes) >= n_days:
                    break

            d -= timedelta(days=1)

        return closes


class MinuteBarsCache:
    """Cache for minute bars by date."""

    def __init__(self):
        self.cache: Dict[date, Dict[str, pd.DataFrame]] = {}

    def load_day(self, trade_date: date):
        """Load minute bars for a date."""
        if trade_date in self.cache:
            return

        file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
        if not file_path.exists():
            self.cache[trade_date] = {}
            return

        try:
            df = pd.read_csv(file_path, compression='gzip')
            df['timestamp'] = pd.to_datetime(df['window_start'], unit='ns')

            grouped = {}
            for ticker, group in df.groupby('ticker'):
                grouped[ticker] = group.sort_values('timestamp').reset_index(drop=True)

            self.cache[trade_date] = grouped
        except Exception as e:
            print(f"Error loading {trade_date}: {e}")
            self.cache[trade_date] = {}

    def get_price_at_time(self, symbol: str, signal_time: datetime) -> Optional[float]:
        """Get price at or just before signal time."""
        trade_date = signal_time.date()
        self.load_day(trade_date)

        if trade_date not in self.cache or symbol not in self.cache[trade_date]:
            return None

        bars = self.cache[trade_date][symbol]
        mask = bars['timestamp'] <= signal_time
        valid = bars[mask]

        if len(valid) == 0:
            return None

        return valid.iloc[-1]['close']


def calculate_corrected_trend(
    symbol: str,
    signal_time: datetime,
    daily_cache: DailyBarsCache,
    minute_cache: MinuteBarsCache
) -> Optional[int]:
    """
    Calculate trend using ONLY data available at signal time.

    Returns: 1 (uptrend), -1 (downtrend), or None (insufficient data)
    """
    trade_date = signal_time.date()

    # Get price AT signal time (from minute bars)
    price_at_signal = minute_cache.get_price_at_time(symbol, signal_time)
    if price_at_signal is None:
        return None

    # Get SMA-20 from PRIOR days only (no look-ahead)
    prior_closes = daily_cache.get_prior_closes(symbol, trade_date, n_days=20)
    if len(prior_closes) < 5:  # Need at least 5 days
        return None

    sma_20 = sum(prior_closes) / len(prior_closes)

    # Trend based on signal-time price vs historical SMA
    return 1 if price_at_signal > sma_20 else -1


def fix_signals(signals: list) -> list:
    """Fix look-ahead bias in all signals."""
    print("\nFixing look-ahead bias...")

    daily_cache = DailyBarsCache()
    minute_cache = MinuteBarsCache()

    # Group by date for progress tracking
    by_date = defaultdict(list)
    for s in signals:
        by_date[s['detection_dt'].date()].append(s)

    fixed_count = 0
    changed_count = 0
    no_data_count = 0

    sorted_dates = sorted(by_date.keys())

    for i, trade_date in enumerate(sorted_dates):
        day_sigs = by_date[trade_date]

        for sig in day_sigs:
            corrected_trend = calculate_corrected_trend(
                sig['symbol'],
                sig['detection_dt'],
                daily_cache,
                minute_cache
            )

            if corrected_trend is not None:
                original_trend = sig.get('trend', 0)
                sig['trend_original'] = original_trend
                sig['trend'] = corrected_trend  # Replace with corrected value
                sig['trend_corrected'] = True

                fixed_count += 1
                if corrected_trend != original_trend:
                    changed_count += 1
            else:
                sig['trend_corrected'] = False
                no_data_count += 1

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(sorted_dates)} days... ({fixed_count:,} fixed, {changed_count:,} changed)")

    print(f"\nFix complete:")
    print(f"  Fixed: {fixed_count:,} signals")
    print(f"  Changed: {changed_count:,} signals ({changed_count/fixed_count*100:.1f}%)")
    print(f"  No data: {no_data_count:,} signals")

    return signals


def compare_performance(signals: list):
    """Compare original vs corrected trend filter performance."""
    print("\n" + "="*70)
    print("PERFORMANCE COMPARISON: Original vs Corrected Trend")
    print("="*70)

    # Filter to signals with corrected trend
    corrected = [s for s in signals if s.get('trend_corrected')]

    print(f"\nSignals with corrected trend: {len(corrected):,}")

    # Original filter: trend_original == 1 and score >= 10
    original_filter = [s for s in corrected
                       if s.get('trend_original') == 1 and s.get('score', 0) >= 10]

    # Corrected filter: trend == 1 and score >= 10
    corrected_filter = [s for s in corrected
                        if s.get('trend') == 1 and s.get('score', 0) >= 10]

    def stats(group, name):
        if not group:
            print(f"\n{name}: No signals")
            return None

        returns = [s['pct_to_close'] for s in group]
        wr = len([r for r in returns if r > 0]) / len(returns) * 100
        avg = sum(returns) / len(returns)
        per_day = len(group) / 126

        print(f"\n{name}:")
        print(f"  Signals: {len(group):,} ({per_day:.1f}/day)")
        print(f"  Win Rate: {wr:.1f}%")
        print(f"  Avg Return: {avg:+.3f}%")

        return {'count': len(group), 'wr': wr, 'avg': avg}

    orig_stats = stats(original_filter, "Original (EOD trend)")
    corr_stats = stats(corrected_filter, "Corrected (signal-time trend)")

    # Overlap analysis
    orig_symbols_times = {(s['symbol'], s['detection_time'][:16]) for s in original_filter}
    corr_symbols_times = {(s['symbol'], s['detection_time'][:16]) for s in corrected_filter}

    overlap = orig_symbols_times & corr_symbols_times
    only_orig = orig_symbols_times - corr_symbols_times
    only_corr = corr_symbols_times - orig_symbols_times

    print(f"\n--- Overlap Analysis ---")
    print(f"  Both filters: {len(overlap):,}")
    print(f"  Only original: {len(only_orig):,}")
    print(f"  Only corrected: {len(only_corr):,}")

    # Performance of signals that changed
    if only_orig:
        removed = [s for s in corrected
                   if (s['symbol'], s['detection_time'][:16]) in only_orig]
        removed_returns = [s['pct_to_close'] for s in removed]
        removed_wr = len([r for r in removed_returns if r > 0]) / len(removed_returns) * 100
        removed_avg = sum(removed_returns) / len(removed_returns)
        print(f"\n  Removed signals (were uptrend, now downtrend):")
        print(f"    WR: {removed_wr:.1f}%, Avg: {removed_avg:+.3f}%")

    if only_corr:
        added = [s for s in corrected
                 if (s['symbol'], s['detection_time'][:16]) in only_corr]
        added_returns = [s['pct_to_close'] for s in added]
        added_wr = len([r for r in added_returns if r > 0]) / len(added_returns) * 100
        added_avg = sum(added_returns) / len(added_returns)
        print(f"\n  Added signals (were downtrend, now uptrend):")
        print(f"    WR: {added_wr:.1f}%, Avg: {added_avg:+.3f}%")

    return orig_stats, corr_stats


def save_corrected_signals(signals: list):
    """Save signals with corrected trend for downstream testing."""
    output_path = RESULTS_DIR / "signals_trend_corrected.json"

    # Convert datetime to string for JSON
    output_signals = []
    for s in signals:
        sig_copy = {k: v for k, v in s.items() if k != 'detection_dt'}
        output_signals.append(sig_copy)

    output = {
        'description': 'Signals with look-ahead bias fixed in trend calculation',
        'fix_applied': 'Trend uses signal-time price vs SMA-20 of prior days only',
        'signals': output_signals,
    }

    with open(output_path, 'w') as f:
        json.dump(output, f)

    print(f"\nSaved corrected signals to {output_path}")


def main():
    signals = load_signals()
    signals = fix_signals(signals)
    orig_stats, corr_stats = compare_performance(signals)
    save_corrected_signals(signals)

    # Summary
    print("\n" + "="*70)
    print("LOOK-AHEAD FIX SUMMARY")
    print("="*70)

    if orig_stats and corr_stats:
        wr_diff = corr_stats['wr'] - orig_stats['wr']
        avg_diff = corr_stats['avg'] - orig_stats['avg']

        print(f"\nOriginal: {orig_stats['count']:,} signals, {orig_stats['wr']:.1f}% WR, {orig_stats['avg']:+.3f}%")
        print(f"Corrected: {corr_stats['count']:,} signals, {corr_stats['wr']:.1f}% WR, {corr_stats['avg']:+.3f}%")
        print(f"Change: {wr_diff:+.1f}% WR, {avg_diff:+.3f}% avg")

        if corr_stats['wr'] >= 55:
            print("\n[PASS] Corrected strategy still achieves 55%+ WR")
        else:
            print("\n[WARNING] Corrected WR dropped below 55%")


if __name__ == "__main__":
    main()
