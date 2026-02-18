"""
Analyze enhanced backtest results + Quick TA sample test
"""

import json
import gzip
import os
from collections import defaultdict
from datetime import datetime, timedelta
import random

print("="*80)
print("ENHANCED BACKTEST ANALYSIS + TA SAMPLE TEST")
print("="*80)

# =============================================================================
# PART 1: Analyze Enhanced Backtest Results (strikes, sweeps, price)
# =============================================================================

print("\nLoading enhanced backtest results...")
with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_v2_strikes_sweeps_price_scored.json") as f:
    data = json.load(f)

signals = data['signals']
print(f"Total signals: {len(signals):,}")

# Check what fields we have
sample = signals[0]
print(f"\nFields available: {list(sample.keys())}")

# Filter to valid (need to join with outcomes)
# Load outcomes from original file
print("\nLoading outcomes data...")
with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    outcomes_data = json.load(f)

outcomes_signals = outcomes_data['signals']

# Build lookup: (symbol, detection_time) -> outcome
outcome_lookup = {}
for s in outcomes_signals:
    if s.get('pct_to_close') is not None and not s.get('filtered_out'):
        key = (s['symbol'], s['detection_time'])
        outcome_lookup[key] = {
            'pct_to_close': s['pct_to_close'],
            'pct_max_gain': s.get('pct_max_gain'),
            'pct_max_loss': s.get('pct_max_loss'),
            'gap_pct': s.get('gap_pct'),
            'is_premarket': s.get('is_premarket'),
        }

print(f"Outcomes available: {len(outcome_lookup):,}")

# Join
valid = []
for s in signals:
    key = (s['symbol'], s['detection_time'])
    if key in outcome_lookup:
        s.update(outcome_lookup[key])
        valid.append(s)

print(f"Matched signals: {len(valid):,}")

# =============================================================================
# ANALYZE NEW METRICS
# =============================================================================

print("\n" + "="*80)
print("STRIKE ANALYSIS")
print("="*80)

# Strike concentration
def analyze_by_metric(signals, metric_name, buckets):
    results = {}
    for bucket_name, filter_fn in buckets.items():
        subset = [s for s in signals if filter_fn(s)]
        if len(subset) < 100:
            continue
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        results[bucket_name] = {'count': len(subset), 'win_rate': wr, 'avg': avg}
        print(f"  {bucket_name}: n={len(subset):,}, WR={wr:.1f}%, avg={avg:+.2f}%")
    return results

print("\nBy Strike Concentration (lower = more concentrated):")
analyze_by_metric(valid, 'strike_concentration', {
    'Very concentrated (<0.2)': lambda s: s.get('strike_concentration', 1) < 0.2,
    'Concentrated (0.2-0.4)': lambda s: 0.2 <= s.get('strike_concentration', 1) < 0.4,
    'Moderate (0.4-0.6)': lambda s: 0.4 <= s.get('strike_concentration', 1) < 0.6,
    'Spread (0.6-0.8)': lambda s: 0.6 <= s.get('strike_concentration', 1) < 0.8,
    'Very spread (>0.8)': lambda s: s.get('strike_concentration', 1) >= 0.8,
})

print("\nBy OTM Percentage:")
analyze_by_metric(valid, 'otm_pct', {
    'ITM heavy (<20% OTM)': lambda s: s.get('otm_pct', 0) < 0.2,
    'Mixed ITM (20-40% OTM)': lambda s: 0.2 <= s.get('otm_pct', 0) < 0.4,
    'Balanced (40-60% OTM)': lambda s: 0.4 <= s.get('otm_pct', 0) < 0.6,
    'OTM leaning (60-80% OTM)': lambda s: 0.6 <= s.get('otm_pct', 0) < 0.8,
    'OTM heavy (>80% OTM)': lambda s: s.get('otm_pct', 0) >= 0.8,
})

print("\n" + "="*80)
print("SWEEP ANALYSIS")
print("="*80)

print("\nBy Sweep Percentage:")
analyze_by_metric(valid, 'sweep_pct', {
    'No sweeps (0%)': lambda s: s.get('sweep_pct', 0) == 0,
    'Low sweep (1-10%)': lambda s: 0 < s.get('sweep_pct', 0) < 0.1,
    'Moderate sweep (10-30%)': lambda s: 0.1 <= s.get('sweep_pct', 0) < 0.3,
    'High sweep (30-50%)': lambda s: 0.3 <= s.get('sweep_pct', 0) < 0.5,
    'Very high sweep (>50%)': lambda s: s.get('sweep_pct', 0) >= 0.5,
})

print("\n" + "="*80)
print("PRICE CONTEXT ANALYSIS")
print("="*80)

# Filter to signals with price context
with_price = [s for s in valid if s.get('dist_from_20d_low') is not None]
print(f"\nSignals with price context: {len(with_price):,}")

if with_price:
    print("\nBy Distance from 20d Low:")
    analyze_by_metric(with_price, 'dist_from_20d_low', {
        'At support (<5%)': lambda s: s.get('dist_from_20d_low', 1) < 0.05,
        'Near support (5-15%)': lambda s: 0.05 <= s.get('dist_from_20d_low', 1) < 0.15,
        'Mid-range (15-30%)': lambda s: 0.15 <= s.get('dist_from_20d_low', 1) < 0.30,
        'Extended (30-50%)': lambda s: 0.30 <= s.get('dist_from_20d_low', 1) < 0.50,
        'Very extended (>50%)': lambda s: s.get('dist_from_20d_low', 1) >= 0.50,
    })
    
    print("\nBy Trend:")
    analyze_by_metric(with_price, 'trend', {
        'Uptrend (price > SMA)': lambda s: s.get('trend') == 1,
        'Downtrend (price < SMA)': lambda s: s.get('trend') == -1,
    })

print("\n" + "="*80)
print("SCORE ANALYSIS")
print("="*80)

print("\nBy Score:")
analyze_by_metric(valid, 'score', {
    'Score 0-1': lambda s: s.get('score', 0) <= 1,
    'Score 2-3': lambda s: 2 <= s.get('score', 0) <= 3,
    'Score 4-5': lambda s: 4 <= s.get('score', 0) <= 5,
    'Score 6-7': lambda s: 6 <= s.get('score', 0) <= 7,
    'Score 8+': lambda s: s.get('score', 0) >= 8,
})

# =============================================================================
# BEST COMBINATIONS
# =============================================================================

print("\n" + "="*80)
print("BEST COMBINATIONS")
print("="*80)

# High score + sweep + concentrated
best = [s for s in valid 
        if s.get('score', 0) >= 6
        and s.get('sweep_pct', 0) >= 0.1
        and s.get('strike_concentration', 1) < 0.4]

if len(best) >= 20:
    closes = [s['pct_to_close'] for s in best]
    avg = sum(closes) / len(closes)
    wr = len([c for c in closes if c > 0]) / len(closes) * 100
    print(f"\nScore>=6 + Sweep>=10% + Concentrated strikes:")
    print(f"  Count: {len(best)}")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Avg return: {avg:+.2f}%")

# High score + uptrend + near support
if with_price:
    best2 = [s for s in with_price 
             if s.get('score', 0) >= 5
             and s.get('trend') == 1
             and s.get('dist_from_20d_low', 1) < 0.15]
    
    if len(best2) >= 20:
        closes = [s['pct_to_close'] for s in best2]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        print(f"\nScore>=5 + Uptrend + Near support (<15%):")
        print(f"  Count: {len(best2)}")
        print(f"  Win rate: {wr:.1f}%")
        print(f"  Avg return: {avg:+.2f}%")

# =============================================================================
# PART 2: TA SAMPLE TEST
# =============================================================================

print("\n" + "="*80)
print("TA SAMPLE TEST (1,000 signals)")
print("="*80)

STOCKS_DIR = r"C:\Users\levir\Documents\FL3_V2\polygon_data\stocks"

def load_stock_bars(trade_date):
    """Load minute bars for a date, return dict of symbol -> list of bars."""
    filepath = os.path.join(STOCKS_DIR, f"{trade_date}.csv.gz")
    if not os.path.exists(filepath):
        return {}
    
    bars = defaultdict(list)
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            ticker = parts[idx["ticker"]]
            bars[ticker].append({
                'timestamp': parts[idx["window_start"]],
                'open': float(parts[idx["open"]]),
                'high': float(parts[idx["high"]]),
                'low': float(parts[idx["low"]]),
                'close': float(parts[idx["close"]]),
                'volume': int(parts[idx["volume"]]),
            })
    
    return bars

def calculate_rsi(closes, period=14):
    """Calculate RSI from closing prices."""
    if len(closes) < period + 1:
        return None
    
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(closes, fast=12, slow=26, signal=9):
    """Calculate MACD and signal line."""
    if len(closes) < slow + signal:
        return None, None, None
    
    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result
    
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line[slow-1:], signal)
    
    macd_val = macd_line[-1]
    signal_val = signal_line[-1] if signal_line else None
    histogram = macd_val - signal_val if signal_val else None
    
    return macd_val, signal_val, histogram

def calculate_vwap(bars):
    """Calculate VWAP from bars."""
    cumulative_tpv = 0
    cumulative_vol = 0
    
    for bar in bars:
        tp = (bar['high'] + bar['low'] + bar['close']) / 3
        cumulative_tpv += tp * bar['volume']
        cumulative_vol += bar['volume']
    
    return cumulative_tpv / cumulative_vol if cumulative_vol > 0 else None

# Sample 1,000 signals with outcomes
sample_signals = random.sample(valid, min(1000, len(valid)))
print(f"Sampled {len(sample_signals)} signals")

# Group by date for efficient loading
by_date = defaultdict(list)
for s in sample_signals:
    date_str = s['detection_time'][:10]
    by_date[date_str].append(s)

print(f"Across {len(by_date)} unique dates")

# Calculate TA for each signal
ta_results = []
dates_processed = 0

for date_str, day_signals in by_date.items():
    bars_by_symbol = load_stock_bars(date_str)
    dates_processed += 1
    
    if dates_processed % 20 == 0:
        print(f"  Processed {dates_processed}/{len(by_date)} dates...")
    
    for sig in day_signals:
        symbol = sig['symbol']
        signal_time = sig['detection_time'][11:16]  # HH:MM
        
        if symbol not in bars_by_symbol:
            continue
        
        # Get bars up to signal time
        all_bars = bars_by_symbol[symbol]
        bars_before = [b for b in all_bars if b['timestamp'][11:16] <= signal_time]
        
        if len(bars_before) < 30:  # Need enough history
            continue
        
        closes = [b['close'] for b in bars_before]
        
        # Calculate TA
        rsi = calculate_rsi(closes[-50:]) if len(closes) >= 50 else None
        macd, macd_signal, macd_hist = calculate_macd(closes[-50:]) if len(closes) >= 50 else (None, None, None)
        vwap = calculate_vwap(bars_before)
        
        current_price = closes[-1]
        price_vs_vwap = (current_price - vwap) / vwap if vwap else None
        
        ta_results.append({
            'symbol': symbol,
            'detection_time': sig['detection_time'],
            'pct_to_close': sig['pct_to_close'],
            'rsi': rsi,
            'macd_hist': macd_hist,
            'price_vs_vwap': price_vs_vwap,
            'score': sig.get('score', 0),
        })

print(f"\nTA calculated for {len(ta_results)} signals")

if ta_results:
    print("\n" + "="*80)
    print("TA CORRELATION ANALYSIS")
    print("="*80)
    
    # By RSI
    with_rsi = [r for r in ta_results if r['rsi'] is not None]
    print(f"\nSignals with RSI: {len(with_rsi)}")
    
    print("\nBy RSI Level:")
    for name, filter_fn in [
        ('Oversold (<30)', lambda r: r['rsi'] < 30),
        ('Neutral-Low (30-50)', lambda r: 30 <= r['rsi'] < 50),
        ('Neutral-High (50-70)', lambda r: 50 <= r['rsi'] < 70),
        ('Overbought (>70)', lambda r: r['rsi'] >= 70),
    ]:
        subset = [r for r in with_rsi if filter_fn(r)]
        if len(subset) >= 20:
            closes = [r['pct_to_close'] for r in subset]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(subset)}, WR={wr:.1f}%, avg={avg:+.2f}%")
    
    # By MACD
    with_macd = [r for r in ta_results if r['macd_hist'] is not None]
    print(f"\nSignals with MACD: {len(with_macd)}")
    
    print("\nBy MACD Histogram:")
    for name, filter_fn in [
        ('Strongly bearish (<-0.5)', lambda r: r['macd_hist'] < -0.5),
        ('Bearish (-0.5 to 0)', lambda r: -0.5 <= r['macd_hist'] < 0),
        ('Bullish (0 to 0.5)', lambda r: 0 <= r['macd_hist'] < 0.5),
        ('Strongly bullish (>0.5)', lambda r: r['macd_hist'] >= 0.5),
    ]:
        subset = [r for r in with_macd if filter_fn(r)]
        if len(subset) >= 20:
            closes = [r['pct_to_close'] for r in subset]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(subset)}, WR={wr:.1f}%, avg={avg:+.2f}%")
    
    # By Price vs VWAP
    with_vwap = [r for r in ta_results if r['price_vs_vwap'] is not None]
    print(f"\nSignals with VWAP: {len(with_vwap)}")
    
    print("\nBy Price vs VWAP:")
    for name, filter_fn in [
        ('Well below VWAP (<-2%)', lambda r: r['price_vs_vwap'] < -0.02),
        ('Below VWAP (-2% to 0)', lambda r: -0.02 <= r['price_vs_vwap'] < 0),
        ('Above VWAP (0 to 2%)', lambda r: 0 <= r['price_vs_vwap'] < 0.02),
        ('Well above VWAP (>2%)', lambda r: r['price_vs_vwap'] >= 0.02),
    ]:
        subset = [r for r in with_vwap if filter_fn(r)]
        if len(subset) >= 20:
            closes = [r['pct_to_close'] for r in subset]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(subset)}, WR={wr:.1f}%, avg={avg:+.2f}%")
    
    # Combined: RSI crossing + MACD
    print("\n" + "="*80)
    print("TA COMBINATIONS")
    print("="*80)
    
    # Bullish setup: RSI 40-60 (not overbought) + MACD positive
    bullish_ta = [r for r in ta_results 
                  if r['rsi'] is not None and 40 <= r['rsi'] <= 60
                  and r['macd_hist'] is not None and r['macd_hist'] > 0]
    
    if len(bullish_ta) >= 20:
        closes = [r['pct_to_close'] for r in bullish_ta]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        print(f"\nBullish TA (RSI 40-60 + MACD positive):")
        print(f"  Count: {len(bullish_ta)}")
        print(f"  Win rate: {wr:.1f}%")
        print(f"  Avg return: {avg:+.2f}%")
    
    # Oversold bounce: RSI < 40 + MACD turning positive
    oversold_bounce = [r for r in ta_results 
                       if r['rsi'] is not None and r['rsi'] < 40
                       and r['macd_hist'] is not None and r['macd_hist'] > 0]
    
    if len(oversold_bounce) >= 20:
        closes = [r['pct_to_close'] for r in oversold_bounce]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        print(f"\nOversold bounce (RSI < 40 + MACD positive):")
        print(f"  Count: {len(oversold_bounce)}")
        print(f"  Win rate: {wr:.1f}%")
        print(f"  Avg return: {avg:+.2f}%")
    
    # Momentum: RSI > 50 + MACD strongly positive + above VWAP
    momentum = [r for r in ta_results 
                if r['rsi'] is not None and r['rsi'] > 50
                and r['macd_hist'] is not None and r['macd_hist'] > 0.2
                and r['price_vs_vwap'] is not None and r['price_vs_vwap'] > 0]
    
    if len(momentum) >= 20:
        closes = [r['pct_to_close'] for r in momentum]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        print(f"\nMomentum (RSI>50 + MACD>0.2 + above VWAP):")
        print(f"  Count: {len(momentum)}")
        print(f"  Win rate: {wr:.1f}%")
        print(f"  Avg return: {avg:+.2f}%")

print("\n" + "="*80)
print("ANALYSIS COMPLETE")
print("="*80)
