"""
Deep TA Analysis - Find actionable signal combinations.

Explores multi-factor filters to find edges above 55% win rate.
"""

import pandas as pd
from pathlib import Path
import json

def load_data():
    """Load enriched signals."""
    base_dir = Path("C:/Users/levir/Documents/FL3_V2")
    parquet_path = base_dir / "polygon_data/backtest_results/signals_with_ta.parquet"
    df = pd.read_parquet(parquet_path)

    # Filter to signals with outcomes and TA
    df = df[df['pct_to_close'].notna() & df['rsi_14'].notna() & df['macd_histogram'].notna()].copy()
    df['win'] = df['pct_to_close'] > 0
    df['is_call'] = df['call_pct'] > 0.5

    print(f"Loaded {len(df):,} signals with TA and outcomes")
    return df


def analyze_combinations(df: pd.DataFrame):
    """Test various filter combinations."""
    results = []

    # Define filter conditions
    filters = {
        # RSI conditions
        'rsi_oversold': df['rsi_14'] < 30,
        'rsi_30_50': (df['rsi_14'] >= 30) & (df['rsi_14'] < 50),
        'rsi_50_70': (df['rsi_14'] >= 50) & (df['rsi_14'] < 70),
        'rsi_overbought': df['rsi_14'] >= 70,
        'rsi_neutral': (df['rsi_14'] >= 30) & (df['rsi_14'] < 70),

        # MACD conditions
        'macd_bullish': df['macd_histogram'] > 0,
        'macd_bearish': df['macd_histogram'] < 0,
        'macd_strong_bull': df['macd_histogram'] > 0.1,
        'macd_strong_bear': df['macd_histogram'] < -0.1,

        # VWAP conditions
        'above_vwap': df['price_vs_vwap'] > 0,
        'below_vwap': df['price_vs_vwap'] < 0,
        'far_above_vwap': df['price_vs_vwap'] > 0.5,
        'far_below_vwap': df['price_vs_vwap'] < -0.5,

        # Call/Put
        'is_call': df['is_call'],
        'is_put': ~df['is_call'],

        # High confidence
        'high_conf': df['confidence'] >= 0.8,
        'low_conf': df['confidence'] < 0.8,

        # High ratio
        'high_ratio': df['ratio'] >= 5,
        'very_high_ratio': df['ratio'] >= 10,

        # Time of day (from bucket_start)
        'morning': df['bucket_start'].isin(['09:30', '10:00', '10:30', '11:00']),
        'midday': df['bucket_start'].isin(['11:30', '12:00', '12:30', '13:00', '13:30']),
        'afternoon': df['bucket_start'].isin(['14:00', '14:30', '15:00', '15:30']),
    }

    # Test single filters
    print("\n" + "=" * 70)
    print("SINGLE FILTER ANALYSIS")
    print("=" * 70)

    for name, mask in filters.items():
        subset = df[mask]
        if len(subset) >= 100:  # Need minimum sample
            win_rate = subset['win'].mean() * 100
            avg_ret = subset['pct_to_close'].mean()
            results.append({
                'filter': name,
                'count': len(subset),
                'win_rate': win_rate,
                'avg_return': avg_ret,
            })

    results_df = pd.DataFrame(results).sort_values('win_rate', ascending=False)
    print(results_df.to_string(index=False))

    # Test combinations
    print("\n" + "=" * 70)
    print("COMBINATION FILTERS (2-3 factors)")
    print("=" * 70)

    combo_results = []

    # Bullish call setups
    combos = [
        ('call + oversold', filters['is_call'] & filters['rsi_oversold']),
        ('call + macd_bull + below_vwap', filters['is_call'] & filters['macd_bullish'] & filters['below_vwap']),
        ('call + oversold + macd_bull', filters['is_call'] & filters['rsi_oversold'] & filters['macd_bullish']),
        ('call + rsi_30_50 + macd_bull', filters['is_call'] & filters['rsi_30_50'] & filters['macd_bullish']),
        ('call + high_ratio + rsi_neutral', filters['is_call'] & filters['high_ratio'] & filters['rsi_neutral']),
        ('call + morning + macd_bull', filters['is_call'] & filters['morning'] & filters['macd_bullish']),
        ('call + high_conf + rsi_30_50', filters['is_call'] & filters['high_conf'] & filters['rsi_30_50']),

        # Bearish put setups
        ('put + overbought', filters['is_put'] & filters['rsi_overbought']),
        ('put + macd_bear + above_vwap', filters['is_put'] & filters['macd_bearish'] & filters['above_vwap']),
        ('put + overbought + macd_bear', filters['is_put'] & filters['rsi_overbought'] & filters['macd_bearish']),
        ('put + rsi_50_70 + macd_bear', filters['is_put'] & filters['rsi_50_70'] & filters['macd_bearish']),

        # Contrarian
        ('call + overbought + macd_bull', filters['is_call'] & filters['rsi_overbought'] & filters['macd_bullish']),
        ('put + oversold + macd_bear', filters['is_put'] & filters['rsi_oversold'] & filters['macd_bearish']),

        # Time-based
        ('morning + high_ratio', filters['morning'] & filters['high_ratio']),
        ('afternoon + high_ratio', filters['afternoon'] & filters['high_ratio']),

        # Extremes
        ('far_below_vwap + macd_bull', filters['far_below_vwap'] & filters['macd_bullish']),
        ('far_above_vwap + macd_bear', filters['far_above_vwap'] & filters['macd_bearish']),

        # Signal strength
        ('very_high_ratio + rsi_neutral', filters['very_high_ratio'] & filters['rsi_neutral']),
        ('high_conf + macd_bull + below_vwap', filters['high_conf'] & filters['macd_bullish'] & filters['below_vwap']),
    ]

    for name, mask in combos:
        subset = df[mask]
        if len(subset) >= 50:
            win_rate = subset['win'].mean() * 100
            avg_ret = subset['pct_to_close'].mean()
            combo_results.append({
                'combo': name,
                'count': len(subset),
                'win_rate': win_rate,
                'avg_return': avg_ret,
            })

    combo_df = pd.DataFrame(combo_results).sort_values('win_rate', ascending=False)
    print(combo_df.to_string(index=False))

    # Find best filters with 55%+ win rate
    print("\n" + "=" * 70)
    print("FILTERS WITH 55%+ WIN RATE (min 100 signals)")
    print("=" * 70)

    high_wr = results_df[results_df['win_rate'] >= 55]
    if len(high_wr) > 0:
        print(high_wr.to_string(index=False))
    else:
        print("No single filters achieve 55%+ win rate with 100+ signals")

    high_wr_combo = combo_df[combo_df['win_rate'] >= 55]
    if len(high_wr_combo) > 0:
        print("\nCombinations with 55%+ win rate:")
        print(high_wr_combo.to_string(index=False))
    else:
        print("\nNo combinations achieve 55%+ win rate with 50+ signals")

    # Extreme filters - less restrictive count
    print("\n" + "=" * 70)
    print("BEST WIN RATES (any sample size >= 30)")
    print("=" * 70)

    all_results = results + combo_results
    all_df = pd.DataFrame(all_results)
    all_df = all_df.rename(columns={'filter': 'combo', 'combo': 'combo'})
    if 'filter' in all_df.columns:
        all_df['combo'] = all_df['filter'].fillna(all_df.get('combo', ''))
    best = all_df[all_df['count'] >= 30].nlargest(15, 'win_rate')
    print(best[['combo', 'count', 'win_rate', 'avg_return']].to_string(index=False))

    return results_df, combo_df


def analyze_by_time(df: pd.DataFrame):
    """Analyze win rate by time of day."""
    print("\n" + "=" * 70)
    print("WIN RATE BY TIME OF DAY")
    print("=" * 70)

    by_time = df.groupby('bucket_start').agg({
        'win': ['count', 'mean'],
        'pct_to_close': 'mean'
    }).round(4)
    by_time.columns = ['count', 'win_rate', 'avg_return']
    by_time['win_rate'] = by_time['win_rate'] * 100
    print(by_time.to_string())


def analyze_by_ratio(df: pd.DataFrame):
    """Analyze win rate by volume ratio."""
    print("\n" + "=" * 70)
    print("WIN RATE BY VOLUME RATIO")
    print("=" * 70)

    df['ratio_bucket'] = pd.cut(df['ratio'], bins=[0, 3, 5, 7, 10, 20, 100],
                                 labels=['3-5x', '5-7x', '7-10x', '10-20x', '20x+', '>100x'])
    by_ratio = df.groupby('ratio_bucket', observed=True).agg({
        'win': ['count', 'mean'],
        'pct_to_close': 'mean'
    }).round(4)
    by_ratio.columns = ['count', 'win_rate', 'avg_return']
    by_ratio['win_rate'] = by_ratio['win_rate'] * 100
    print(by_ratio.to_string())


def analyze_calls_vs_puts(df: pd.DataFrame):
    """Deep dive into calls vs puts."""
    print("\n" + "=" * 70)
    print("CALLS vs PUTS DEEP ANALYSIS")
    print("=" * 70)

    for name, subset in [('CALLS', df[df['is_call']]), ('PUTS', df[~df['is_call']])]:
        print(f"\n--- {name} ({len(subset):,} signals) ---")

        # By RSI
        for zone, lo, hi in [('oversold', 0, 30), ('neutral', 30, 70), ('overbought', 70, 100)]:
            mask = (subset['rsi_14'] >= lo) & (subset['rsi_14'] < hi)
            z = subset[mask]
            if len(z) > 0:
                print(f"  RSI {zone}: {len(z):,} | Win: {z['win'].mean()*100:.1f}% | Avg: {z['pct_to_close'].mean():.2f}%")

        # By MACD
        bull = subset[subset['macd_histogram'] > 0]
        bear = subset[subset['macd_histogram'] < 0]
        print(f"  MACD bull: {len(bull):,} | Win: {bull['win'].mean()*100:.1f}% | Avg: {bull['pct_to_close'].mean():.2f}%")
        print(f"  MACD bear: {len(bear):,} | Win: {bear['win'].mean()*100:.1f}% | Avg: {bear['pct_to_close'].mean():.2f}%")


if __name__ == "__main__":
    df = load_data()

    print(f"\nBaseline: {len(df):,} signals | Win: {df['win'].mean()*100:.2f}% | Avg: {df['pct_to_close'].mean():.2f}%")

    analyze_by_time(df)
    analyze_by_ratio(df)
    analyze_calls_vs_puts(df)
    analyze_combinations(df)
