"""
Time-based analysis - when do signals arrive vs when gaps happen?
"""
import json
from collections import defaultdict

with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']

# Base filter
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history'
         and s.get('is_premarket')]

print(f"Pre-market signals: {len(valid)}")

# First signal per symbol per day
first_signals = {}
for s in sorted(valid, key=lambda x: x['detection_time']):
    key = (s['symbol'], s['detection_time'][:10])
    if key not in first_signals:
        first_signals[key] = s
first_only = list(first_signals.values())

print(f"First pre-market signal per symbol/day: {len(first_only)}")
print("="*70)

# Split by detection hour
print("\n=== BY DETECTION HOUR ===")
by_hour = defaultdict(list)
for s in first_only:
    hour = int(s['detection_time'][11:13])
    by_hour[hour].append(s)

for hour in sorted(by_hour.keys()):
    subset = by_hour[hour]
    if len(subset) < 50:
        continue
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes)/len(closes)
    wr = len([c for c in closes if c > 0])/len(closes)*100
    
    # Gap stats
    with_gap = [s for s in subset if s.get('gap_pct') is not None]
    if with_gap:
        gaps = [s['gap_pct'] for s in with_gap]
        avg_gap = sum(gaps)/len(gaps)
        gapped_up = len([g for g in gaps if g > 2])
        pct_gapped = gapped_up/len(with_gap)*100
    else:
        avg_gap = 0
        pct_gapped = 0
    
    print(f"  {hour:02d}:00 - {hour:02d}:59: n={len(subset):5}, avg={avg:+.2f}%, win={wr:.1f}%, avg_gap={avg_gap:+.2f}%, gapped_up:{pct_gapped:.0f}%")


print("\n=== EARLY DETECTION (4-7 AM) vs LATE (7-9:30 AM) ===")

early = [s for s in first_only if int(s['detection_time'][11:13]) < 7]
late = [s for s in first_only if 7 <= int(s['detection_time'][11:13]) < 10]

for name, subset in [("Early (4-7 AM)", early), ("Late (7-9:30 AM)", late)]:
    if not subset:
        continue
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes)/len(closes)
    wr = len([c for c in closes if c > 0])/len(closes)*100
    big_win = len([c for c in closes if c > 5])
    
    with_gap = [s for s in subset if s.get('gap_pct') is not None]
    if with_gap:
        gaps = [s['gap_pct'] for s in with_gap]
        avg_gap = sum(gaps)/len(gaps)
        gapped_up_pct = len([g for g in gaps if g > 2])/len(gaps)*100
    else:
        avg_gap = 0
        gapped_up_pct = 0
    
    print(f"\n{name}:")
    print(f"  Count: {len(subset)}")
    print(f"  Avg return: {avg:+.2f}%")
    print(f"  Win rate: {wr:.1f}%")
    print(f"  Big winners (>5%): {big_win} ({big_win/len(subset)*100:.1f}%)")
    print(f"  Avg gap at open: {avg_gap:+.2f}%")
    print(f"  % that gapped up >2%: {gapped_up_pct:.1f}%")


print("\n=== EARLY + BULLISH + HIGH RATIO ===")

early_bullish = [s for s in early if s.get('call_pct', 0) > 0.8]
for min_ratio in [3, 5, 10, 15, 20]:
    subset = [s for s in early_bullish if s['ratio'] >= min_ratio]
    if len(subset) < 20:
        continue
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes)/len(closes)
    wr = len([c for c in closes if c > 0])/len(closes)*100
    big_win = len([c for c in closes if c > 5])
    
    with_gap = [s for s in subset if s.get('gap_pct') is not None]
    gapped_up_pct = len([s for s in with_gap if s['gap_pct'] > 2])/len(with_gap)*100 if with_gap else 0
    
    print(f"  Early + Bullish + >={min_ratio}x: n={len(subset):4}, avg={avg:+.2f}%, win={wr:.1f}%, big_win:{big_win}, gap_up:{gapped_up_pct:.0f}%")


print("\n=== LATE + BULLISH + HIGH RATIO ===")

late_bullish = [s for s in late if s.get('call_pct', 0) > 0.8]
for min_ratio in [3, 5, 10, 15, 20]:
    subset = [s for s in late_bullish if s['ratio'] >= min_ratio]
    if len(subset) < 20:
        continue
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes)/len(closes)
    wr = len([c for c in closes if c > 0])/len(closes)*100
    big_win = len([c for c in closes if c > 5])
    
    with_gap = [s for s in subset if s.get('gap_pct') is not None]
    gapped_up_pct = len([s for s in with_gap if s['gap_pct'] > 2])/len(with_gap)*100 if with_gap else 0
    
    print(f"  Late + Bullish + >={min_ratio}x: n={len(subset):4}, avg={avg:+.2f}%, win={wr:.1f}%, big_win:{big_win}, gap_up:{gapped_up_pct:.0f}%")


print("\n" + "="*70)
print("=== LOOK AT SIGNALS THAT PRECEDED BIG GAPS ===")
print("="*70)

# Find signals where gap_pct > 5% (big movers)
big_gaps = [s for s in first_only if s.get('gap_pct') is not None and s['gap_pct'] > 5]
print(f"\nSignals that preceded >5% gap up: {len(big_gaps)}")

if big_gaps:
    # What time were they detected?
    hours = [int(s['detection_time'][11:13]) for s in big_gaps]
    hour_dist = defaultdict(int)
    for h in hours:
        hour_dist[h] += 1
    print("Detection hour distribution:")
    for h in sorted(hour_dist.keys()):
        print(f"  {h:02d}:00: {hour_dist[h]}")
    
    # What ratios?
    ratios = [s['ratio'] for s in big_gaps]
    print(f"\nRatio stats:")
    print(f"  Min: {min(ratios):.1f}x")
    print(f"  Median: {sorted(ratios)[len(ratios)//2]:.1f}x")
    print(f"  Max: {max(ratios):.1f}x")
    
    # Call pct?
    calls = [s.get('call_pct', 0.5) for s in big_gaps]
    bullish_pct = len([c for c in calls if c > 0.7])/len(calls)*100
    print(f"  % Bullish (>70% calls): {bullish_pct:.1f}%")
    
    # Show top 20
    sorted_gaps = sorted(big_gaps, key=lambda x: x['gap_pct'], reverse=True)
    print(f"\nTop 20 gap-up signals:")
    for s in sorted_gaps[:20]:
        t = s['detection_time'][11:16]
        print(f"  {s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} "
              f"calls:{s.get('call_pct',0)*100:.0f}% gap:{s['gap_pct']:+.1f}% => close:{s['pct_to_close']:+.1f}%")


print("\n" + "="*70)
print("=== PREDICTIVE VALUE: CAN EARLY SIGNALS PREDICT GAPS? ===")
print("="*70)

# For early signals (4-7 AM), how often do they lead to gaps?
early_with_gap = [s for s in early if s.get('gap_pct') is not None]
print(f"\nEarly signals (4-7 AM) with gap data: {len(early_with_gap)}")

# By ratio, what % gap up?
print("\nEarly signals - gap prediction by ratio:")
for min_ratio in [3, 5, 10, 15, 20, 30]:
    subset = [s for s in early_with_gap if s['ratio'] >= min_ratio]
    if len(subset) < 20:
        continue
    gaps = [s['gap_pct'] for s in subset]
    gapped_up = len([g for g in gaps if g > 2])
    gapped_down = len([g for g in gaps if g < -2])
    avg_gap = sum(gaps)/len(gaps)
    print(f"  >={min_ratio:2}x: n={len(subset):4}, gap_up:{gapped_up/len(subset)*100:5.1f}%, gap_dn:{gapped_down/len(subset)*100:5.1f}%, avg_gap:{avg_gap:+.2f}%")

# By ratio + bullish
print("\nEarly BULLISH signals - gap prediction by ratio:")
early_bullish_gap = [s for s in early_with_gap if s.get('call_pct', 0) > 0.8]
for min_ratio in [3, 5, 10, 15, 20]:
    subset = [s for s in early_bullish_gap if s['ratio'] >= min_ratio]
    if len(subset) < 20:
        continue
    gaps = [s['gap_pct'] for s in subset]
    gapped_up = len([g for g in gaps if g > 2])
    gapped_down = len([g for g in gaps if g < -2])
    avg_gap = sum(gaps)/len(gaps)
    print(f"  >={min_ratio:2}x: n={len(subset):4}, gap_up:{gapped_up/len(subset)*100:5.1f}%, gap_dn:{gapped_down/len(subset)*100:5.1f}%, avg_gap:{avg_gap:+.2f}%")
