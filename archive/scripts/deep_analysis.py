"""
Deep dive analysis with tighter filters to find edge
"""
import json
from collections import defaultdict

with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']

# Base filter: has price, not filtered out, history-based
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history']

print(f"Base valid signals: {len(valid)}")
print("="*70)

def analyze(subset, label):
    if not subset:
        print(f"\n{label}: No signals")
        return
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes)/len(closes)
    median = sorted(closes)[len(closes)//2]
    win_rate = len([c for c in closes if c > 0])/len(closes)*100
    big_win = len([c for c in closes if c > 5])
    big_loss = len([c for c in closes if c < -5])
    
    # Max gain analysis
    gains = [s.get('pct_max_gain', 0) or 0 for s in subset]
    avg_max_gain = sum(gains)/len(gains) if gains else 0
    
    print(f"\n{label}")
    print(f"  Count: {len(subset):,}")
    print(f"  Avg % to close: {avg:+.2f}%")
    print(f"  Median: {median:+.2f}%")
    print(f"  Win rate: {win_rate:.1f}%")
    print(f"  Big winners (>5%): {big_win} ({big_win/len(subset)*100:.1f}%)")
    print(f"  Big losers (<-5%): {big_loss} ({big_loss/len(subset)*100:.1f}%)")
    print(f"  Avg max intraday gain: {avg_max_gain:.2f}%")
    return {'count': len(subset), 'avg': avg, 'win_rate': win_rate}


print("\n" + "="*70)
print("FILTER 1: HIGHER RATIO THRESHOLDS")
print("="*70)

for min_ratio in [3, 5, 10, 15, 20, 30, 50]:
    subset = [s for s in valid if s['ratio'] >= min_ratio]
    analyze(subset, f"Ratio >= {min_ratio}x")


print("\n" + "="*70)
print("FILTER 2: HIGHER NOTIONAL THRESHOLDS")
print("="*70)

for min_notional in [10000, 25000, 50000, 100000, 250000, 500000]:
    subset = [s for s in valid if s['notional'] >= min_notional]
    analyze(subset, f"Notional >= ${min_notional:,}")


print("\n" + "="*70)
print("FILTER 3: RATIO + NOTIONAL COMBINED")
print("="*70)

combos = [
    (5, 50000),
    (10, 50000),
    (10, 100000),
    (15, 100000),
    (20, 100000),
    (20, 250000),
]
for min_ratio, min_notional in combos:
    subset = [s for s in valid if s['ratio'] >= min_ratio and s['notional'] >= min_notional]
    analyze(subset, f"Ratio >= {min_ratio}x AND Notional >= ${min_notional:,}")


print("\n" + "="*70)
print("FILTER 4: FIRST SIGNAL OF DAY ONLY (per symbol)")
print("="*70)

# Group by symbol+date, take first signal only
first_signals = {}
for s in sorted(valid, key=lambda x: x['detection_time']):
    key = (s['symbol'], s['detection_time'][:10])
    if key not in first_signals:
        first_signals[key] = s

first_only = list(first_signals.values())
analyze(first_only, "First signal per symbol per day")

# First signal + higher ratio
for min_ratio in [5, 10, 20]:
    subset = [s for s in first_only if s['ratio'] >= min_ratio]
    analyze(subset, f"First signal + Ratio >= {min_ratio}x")


print("\n" + "="*70)
print("FILTER 5: PRE-MARKET ONLY (<9:30)")
print("="*70)

premarket = [s for s in valid if s.get('is_premarket')]
analyze(premarket, "Pre-market signals")

# Pre-market + first signal
premarket_first = [s for s in first_only if s.get('is_premarket')]
analyze(premarket_first, "Pre-market + First signal only")

# Pre-market + first + high ratio
for min_ratio in [5, 10, 20]:
    subset = [s for s in premarket_first if s['ratio'] >= min_ratio]
    analyze(subset, f"Pre-market + First + Ratio >= {min_ratio}x")


print("\n" + "="*70)
print("FILTER 6: BULLISH FLOW ONLY (>80% calls)")
print("="*70)

bullish = [s for s in valid if s.get('call_pct', 0) > 0.8]
analyze(bullish, "Bullish flow (>80% calls)")

# Bullish + first + premarket
bullish_first_pm = [s for s in premarket_first if s.get('call_pct', 0) > 0.8]
analyze(bullish_first_pm, "Bullish + Pre-market + First signal")

# Bullish + first + premarket + high ratio
for min_ratio in [5, 10]:
    subset = [s for s in bullish_first_pm if s['ratio'] >= min_ratio]
    analyze(subset, f"Bullish + PM + First + Ratio >= {min_ratio}x")


print("\n" + "="*70)
print("FILTER 7: EXCLUDE STOCKS THAT ALREADY MOVED (gap < 2%)")
print("="*70)

# Pre-market signals where stock hasn't gapped much yet
no_gap = [s for s in premarket_first if s.get('gap_pct') is not None and abs(s['gap_pct']) < 2]
analyze(no_gap, "Pre-market + First + Gap < 2%")

for min_ratio in [5, 10]:
    subset = [s for s in no_gap if s['ratio'] >= min_ratio]
    analyze(subset, f"PM + First + Gap<2% + Ratio >= {min_ratio}x")

# Add bullish filter
bullish_no_gap = [s for s in no_gap if s.get('call_pct', 0) > 0.8]
analyze(bullish_no_gap, "Bullish + PM + First + Gap<2%")


print("\n" + "="*70)
print("BEST CANDIDATES - TOP PERFORMERS FROM TIGHT FILTERS")
print("="*70)

# Our tightest promising filter
best_filter = [s for s in premarket_first 
               if s['ratio'] >= 10 
               and s.get('call_pct', 0) > 0.8
               and s.get('gap_pct') is not None 
               and abs(s['gap_pct']) < 2]

if best_filter:
    print(f"\nBullish + Pre-market + First + Ratio>=10x + Gap<2%")
    print(f"Count: {len(best_filter)}")
    
    sorted_best = sorted(best_filter, key=lambda x: x['pct_to_close'], reverse=True)
    
    print("\nTop 15:")
    for s in sorted_best[:15]:
        t = s['detection_time'][11:16]
        print(f"  {s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} "
              f"gap:{s['gap_pct']:+.1f}% => close:{s['pct_to_close']:+.1f}% max:{s.get('pct_max_gain',0):.1f}%")
    
    print("\nBottom 15:")
    for s in sorted_best[-15:]:
        t = s['detection_time'][11:16]
        print(f"  {s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} "
              f"gap:{s['gap_pct']:+.1f}% => close:{s['pct_to_close']:+.1f}% max:{s.get('pct_max_gain',0):.1f}%")
