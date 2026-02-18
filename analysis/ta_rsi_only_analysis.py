"""
RSI-only analysis with lower bar requirement for better coverage.

RSI needs only 15 bars vs 35 for MACD.
"""

import pandas as pd
from pathlib import Path

def analyze():
    base_dir = Path("C:/Users/levir/Documents/FL3_V2")
    parquet_path = base_dir / "polygon_data/backtest_results/signals_with_ta.parquet"
    df = pd.read_parquet(parquet_path)

    # Filter to signals with outcomes
    df = df[df['pct_to_close'].notna()].copy()
    print(f"Total signals with outcomes: {len(df):,}")

    df['win'] = df['pct_to_close'] > 0
    df['is_call'] = df['call_pct'] > 0.5

    # Signals with RSI (lower bar requirement)
    df_rsi = df[df['rsi_14'].notna()].copy()
    print(f"Signals with RSI: {len(df_rsi):,} ({len(df_rsi)/len(df)*100:.1f}% coverage)")

    # Signals with VWAP (any bars)
    df_vwap = df[df['price_vs_vwap'].notna()].copy()
    print(f"Signals with VWAP: {len(df_vwap):,} ({len(df_vwap)/len(df)*100:.1f}% coverage)")

    # Signals with bars_count >= 15 (for RSI)
    df_bars = df[df['bars_count'] >= 15].copy()
    print(f"Signals with 15+ bars: {len(df_bars):,} ({len(df_bars)/len(df)*100:.1f}% coverage)")

    print("\n" + "=" * 70)
    print("RSI-ONLY ANALYSIS (without MACD requirement)")
    print("=" * 70)

    baseline = df_rsi['win'].mean() * 100
    print(f"\nBaseline (RSI available): {baseline:.2f}%")

    # RSI zones
    print("\nBy RSI zone:")
    for name, lo, hi in [('oversold <30', 0, 30), ('30-50', 30, 50), ('50-70', 50, 70), ('overbought >70', 70, 100)]:
        mask = (df_rsi['rsi_14'] >= lo) & (df_rsi['rsi_14'] < hi)
        zone = df_rsi[mask]
        print(f"  {name:>15}: {len(zone):>6,} | Win: {zone['win'].mean()*100:.2f}%")

    # Call + RSI oversold
    print("\nKey combinations:")
    combos = [
        ('call + RSI<30', (df_rsi['is_call']) & (df_rsi['rsi_14'] < 30)),
        ('call + RSI<40', (df_rsi['is_call']) & (df_rsi['rsi_14'] < 40)),
        ('call + RSI 30-50', (df_rsi['is_call']) & (df_rsi['rsi_14'] >= 30) & (df_rsi['rsi_14'] < 50)),
        ('put + RSI>60', (~df_rsi['is_call']) & (df_rsi['rsi_14'] > 60)),
        ('put + RSI>70', (~df_rsi['is_call']) & (df_rsi['rsi_14'] > 70)),
    ]

    for name, mask in combos:
        subset = df_rsi[mask]
        if len(subset) > 0:
            print(f"  {name:>20}: {len(subset):>6,} | Win: {subset['win'].mean()*100:.2f}% | Avg: {subset['pct_to_close'].mean():.2f}%")

    # VWAP only (available with fewer bars)
    print("\n" + "=" * 70)
    print("VWAP-ONLY ANALYSIS")
    print("=" * 70)

    df_vwap_only = df[df['price_vs_vwap'].notna()].copy()
    print(f"\nSignals with VWAP: {len(df_vwap_only):,}")

    # VWAP distance buckets
    df_vwap_only['vwap_bucket'] = pd.cut(df_vwap_only['price_vs_vwap'],
                                          bins=[-100, -1, -0.5, 0, 0.5, 1, 100],
                                          labels=['< -1%', '-1 to -0.5%', '-0.5 to 0%', '0 to 0.5%', '0.5 to 1%', '> 1%'])

    print("\nBy distance from VWAP:")
    by_vwap = df_vwap_only.groupby('vwap_bucket', observed=True).agg({
        'win': ['count', 'mean'],
        'pct_to_close': 'mean'
    }).round(4)
    by_vwap.columns = ['count', 'win_rate', 'avg_return']
    by_vwap['win_rate'] = by_vwap['win_rate'] * 100
    print(by_vwap.to_string())

    # Far below VWAP + call
    print("\nFar below VWAP combinations:")
    far_below = df_vwap_only['price_vs_vwap'] < -0.5
    calls = df_vwap_only['is_call']

    for name, mask in [
        ('far below VWAP + call', far_below & calls),
        ('far below VWAP', far_below),
        ('call only', calls),
    ]:
        subset = df_vwap_only[mask]
        print(f"  {name:>25}: {len(subset):>6,} | Win: {subset['win'].mean()*100:.2f}% | Avg: {subset['pct_to_close'].mean():.2f}%")

    # Best simple filter with high coverage
    print("\n" + "=" * 70)
    print("RECOMMENDED FILTERS")
    print("=" * 70)

    print("""
Based on analysis:

1. HIGH COVERAGE + EDGE:
   - "far below VWAP" (price_vs_vwap < -0.5%)
   - Coverage: ~16% of signals with outcomes
   - Win rate: ~55% (vs 53% baseline)

2. STRONG EDGE (lower coverage):
   - "call + RSI < 30"
   - Win rate: ~55.4%

3. BEST COMBO (if available):
   - "call + RSI < 30 + MACD > 0"
   - Win rate: ~64% but only 178 signals

Practical application:
- Use VWAP as primary filter (always available with any bars)
- Add RSI confirmation when 15+ bars available
- Add MACD confirmation when 35+ bars available
""")


if __name__ == "__main__":
    analyze()
