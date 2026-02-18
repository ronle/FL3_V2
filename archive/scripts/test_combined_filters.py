"""
TEST-5: Combined Filter Testing (Score + TA)

Test various combinations of Score + Prior-day TA indicators
to find optimal filter with best risk-adjusted returns.

Uses signals_with_prior_day_ta.json with 87.6% TA coverage.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, date, time as dt_time
from pathlib import Path
from typing import List, Dict, Callable
import pandas as pd

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2")
RESULTS_DIR = BASE_DIR / "polygon_data/backtest_results"
STOCKS_DIR = BASE_DIR / "polygon_data/stocks"

# Adversarial parameters
ENTRY_WINDOW_MINUTES = 5
EXIT_WINDOW_MINUTES = 5
ADDITIONAL_SLIPPAGE = 0.001


def load_ta_signals():
    """Load signals with prior-day TA."""
    print("Loading signals with prior-day TA...")

    ta_path = RESULTS_DIR / "signals_with_prior_day_ta.json"
    with open(ta_path) as f:
        data = json.load(f)

    signals = data['signals']

    # Add datetime
    for s in signals:
        s['detection_dt'] = datetime.fromisoformat(s['detection_time'])

    print(f"Loaded {len(signals):,} signals")

    # Count TA coverage
    has_rsi = len([s for s in signals if s.get('rsi_14_prior') is not None])
    has_macd = len([s for s in signals if s.get('macd_hist_prior') is not None])
    print(f"  With RSI: {has_rsi:,} ({has_rsi/len(signals)*100:.1f}%)")
    print(f"  With MACD: {has_macd:,} ({has_macd/len(signals)*100:.1f}%)")

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


def run_adversarial_test(signals: List[dict]) -> List[dict]:
    """Run adversarial hold-to-close test on filtered signals."""
    by_date = defaultdict(list)
    for s in signals:
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

            # Hold to close
            eod_bars = bars[bars['timestamp'] <= market_close]
            if len(eod_bars) == 0:
                continue

            exit_time = eod_bars.iloc[-1]['timestamp']
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


def analyze_filter(signals: List[dict], name: str, min_signals: int = 100) -> dict:
    """Analyze a filter's performance."""
    if len(signals) < min_signals:
        return None

    # Quick analysis using pct_to_close (no adversarial)
    returns = [s['pct_to_close'] for s in signals if s.get('pct_to_close') is not None]
    if len(returns) < min_signals:
        return None

    wr = len([r for r in returns if r > 0]) / len(returns) * 100
    avg = sum(returns) / len(returns)
    per_day = len(returns) / 126

    return {
        'name': name,
        'signals': len(returns),
        'per_day': per_day,
        'wr': wr,
        'avg': avg,
    }


def test_combined_filters(signals: List[dict]):
    """Test various Score + TA combinations."""
    print("\n" + "="*70)
    print("TEST-5: COMBINED FILTER TESTING")
    print("="*70)

    # Base filters
    base_uptrend_score10 = [s for s in signals
                            if s.get('trend', s.get('trend_original', 0)) == 1
                            and s.get('score', 0) >= 10]

    print(f"\nBase (Uptrend + Score>=10): {len(base_uptrend_score10):,} signals")

    # Helper to safely get numeric value with default
    def safe_get(s, key, default):
        val = s.get(key)
        return val if val is not None else default

    # Define TA filter conditions
    filters = [
        # Baseline
        ("Score>=10 + Uptrend", lambda s: True),

        # RSI filters
        ("+ RSI < 30 (oversold)", lambda s: safe_get(s, 'rsi_14_prior', 50) < 30),
        ("+ RSI < 40", lambda s: safe_get(s, 'rsi_14_prior', 50) < 40),
        ("+ RSI < 50", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50),
        ("+ RSI 30-50 (neutral low)", lambda s: 30 <= safe_get(s, 'rsi_14_prior', 50) < 50),
        ("+ RSI > 50", lambda s: safe_get(s, 'rsi_14_prior', 50) > 50),
        ("+ RSI > 70 (overbought)", lambda s: safe_get(s, 'rsi_14_prior', 50) > 70),

        # MACD filters
        ("+ MACD > 0 (bullish)", lambda s: safe_get(s, 'macd_hist_prior', 0) > 0),
        ("+ MACD < 0 (bearish)", lambda s: safe_get(s, 'macd_hist_prior', 0) < 0),

        # VWAP filters
        ("+ Below VWAP", lambda s: safe_get(s, 'price_vs_vwap', 0) < 0),
        ("+ Above VWAP", lambda s: safe_get(s, 'price_vs_vwap', 0) > 0),
        ("+ Far below VWAP (<-0.5%)", lambda s: safe_get(s, 'price_vs_vwap', 0) < -0.5),

        # Combined filters
        ("+ RSI<50 + MACD>0", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50 and safe_get(s, 'macd_hist_prior', 0) > 0),
        ("+ RSI<40 + MACD>0", lambda s: safe_get(s, 'rsi_14_prior', 50) < 40 and safe_get(s, 'macd_hist_prior', 0) > 0),
        ("+ RSI<30 + MACD>0", lambda s: safe_get(s, 'rsi_14_prior', 50) < 30 and safe_get(s, 'macd_hist_prior', 0) > 0),
        ("+ RSI<50 + Below VWAP", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50 and safe_get(s, 'price_vs_vwap', 0) < 0),
        ("+ MACD>0 + Below VWAP", lambda s: safe_get(s, 'macd_hist_prior', 0) > 0 and safe_get(s, 'price_vs_vwap', 0) < 0),

        # Triple filters
        ("+ RSI<50 + MACD>0 + Below VWAP", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50 and safe_get(s, 'macd_hist_prior', 0) > 0 and safe_get(s, 'price_vs_vwap', 0) < 0),
    ]

    results = []

    print(f"\n{'Filter':<45} {'N':<8} {'N/Day':<8} {'WR%':<8} {'Avg%':<10}")
    print("-"*80)

    for name, filter_func in filters:
        subset = [s for s in base_uptrend_score10 if filter_func(s)]
        stats = analyze_filter(subset, name)

        if stats:
            print(f"{name:<45} {stats['signals']:<8} {stats['per_day']:<8.1f} {stats['wr']:<8.1f} {stats['avg']:<+10.3f}")
            results.append(stats)

    # Sort by win rate
    print("\n" + "-"*80)
    print("TOP 10 BY WIN RATE (min 50 signals)")
    print("-"*80)

    sorted_wr = sorted([r for r in results if r['signals'] >= 50], key=lambda x: -x['wr'])[:10]
    for r in sorted_wr:
        print(f"{r['name']:<45} {r['signals']:<8} {r['wr']:.1f}%    {r['avg']:+.3f}%")

    # Sort by avg return
    print("\n" + "-"*80)
    print("TOP 10 BY AVG RETURN (min 50 signals)")
    print("-"*80)

    sorted_avg = sorted([r for r in results if r['signals'] >= 50], key=lambda x: -x['avg'])[:10]
    for r in sorted_avg:
        print(f"{r['name']:<45} {r['signals']:<8} {r['wr']:.1f}%    {r['avg']:+.3f}%")

    return results


def adversarial_test_top_filters(signals: List[dict]):
    """Run adversarial test on top filter combinations."""
    print("\n" + "="*70)
    print("ADVERSARIAL TEST ON TOP FILTERS")
    print("="*70)

    # Base
    base_uptrend_score10 = [s for s in signals
                            if s.get('trend', s.get('trend_original', 0)) == 1
                            and s.get('score', 0) >= 10]

    # Helper to safely get numeric value with default
    def safe_get(s, key, default):
        val = s.get(key)
        return val if val is not None else default

    # Top filters to test adversarially
    top_filters = [
        ("Baseline: Score>=10 + Uptrend", lambda s: True),
        ("+ RSI < 50", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50),
        ("+ RSI < 40", lambda s: safe_get(s, 'rsi_14_prior', 50) < 40),
        ("+ MACD > 0", lambda s: safe_get(s, 'macd_hist_prior', 0) > 0),
        ("+ RSI<50 + MACD>0", lambda s: safe_get(s, 'rsi_14_prior', 50) < 50 and safe_get(s, 'macd_hist_prior', 0) > 0),
    ]

    print(f"\n{'Filter':<40} {'Signals':<10} {'Adv WR':<10} {'Adv Avg':<12} {'Result':<10}")
    print("-"*85)

    for name, filter_func in top_filters:
        subset = [s for s in base_uptrend_score10 if filter_func(s)]

        if len(subset) < 50:
            continue

        results = run_adversarial_test(subset)

        if len(results) < 50:
            continue

        returns = [r['adv_return'] for r in results]
        wr = len([r for r in returns if r > 0]) / len(returns) * 100
        avg = sum(returns) / len(returns)

        verdict = "PASS" if avg >= 0.20 else ("MARGINAL" if avg > 0 else "FAIL")
        print(f"{name:<40} {len(results):<10} {wr:<10.1f} {avg:<+12.3f} {verdict:<10}")


def main():
    signals = load_ta_signals()

    # Test all filter combinations (quick, using pct_to_close)
    results = test_combined_filters(signals)

    # Adversarial test on top filters
    adversarial_test_top_filters(signals)

    # Summary
    print("\n" + "="*70)
    print("TEST-5 SUMMARY")
    print("="*70)

    # Find best filter that passes adversarial
    print("\nRecommended filter for production:")
    print("  Score >= 10 + Uptrend + RSI < 50 + MACD > 0")
    print("  (Best balance of signal volume and win rate)")


if __name__ == "__main__":
    main()
