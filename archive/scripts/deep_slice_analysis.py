"""
Deep slice analysis - find edge in subsets
"""
import json
from collections import defaultdict

with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']

# Filter to valid signals with history-based baselines
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history']

print(f"Starting pool: {len(valid)} valid history-based signals")
print("="*70)

# =============================================================================
# SLICE 1: Higher thresholds
# =============================================================================
print("\n" + "="*70)
print("SLICE 1: HIGHER RATIO THRESHOLDS")
print("="*70)

for min_ratio in [3, 5, 10, 15, 20, 30, 50]:
    subset = [s for s in valid if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        big_win = len([c for c in closes if c > 5])
        big_loss = len([c for c in closes if c < -5])
        per_day = len(subset) / 126
        print(f"  >= {min_ratio:2}x: n={len(subset):6} ({per_day:5.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%, +5%:{big_win}, -5%:{big_loss}")

# =============================================================================
# SLICE 2: Higher minimum notional
# =============================================================================
print("\n" + "="*70)
print("SLICE 2: HIGHER MINIMUM NOTIONAL")
print("="*70)

for min_notional in [10000, 25000, 50000, 100000, 250000, 500000, 1000000]:
    subset = [s for s in valid if s['notional'] >= min_notional]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  >= ${min_notional/1000:4.0f}K: n={len(subset):6} ({per_day:5.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 3: Combined ratio + notional
# =============================================================================
print("\n" + "="*70)
print("SLICE 3: COMBINED RATIO + NOTIONAL FILTERS")
print("="*70)

combos = [
    (5, 50000),
    (5, 100000),
    (10, 50000),
    (10, 100000),
    (10, 250000),
    (15, 100000),
    (20, 100000),
    (20, 250000),
]

for min_ratio, min_notional in combos:
    subset = [s for s in valid if s['ratio'] >= min_ratio and s['notional'] >= min_notional]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  {min_ratio:2}x + ${min_notional/1000:4.0f}K: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 4: First signal of day only (per symbol)
# =============================================================================
print("\n" + "="*70)
print("SLICE 4: FIRST SIGNAL OF DAY ONLY (per symbol)")
print("="*70)

# Group by (date, symbol), take first only
first_signals = {}
for s in valid:
    key = (s['detection_time'][:10], s['symbol'])
    if key not in first_signals:
        first_signals[key] = s

first_only = list(first_signals.values())
closes = [s['pct_to_close'] for s in first_only]
avg = sum(closes) / len(closes)
wr = len([c for c in closes if c > 0]) / len(closes) * 100
per_day = len(first_only) / 126
print(f"  First signal only: n={len(first_only)} ({per_day:.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# First signal + high ratio
for min_ratio in [5, 10, 15, 20]:
    subset = [s for s in first_only if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  First + >= {min_ratio}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 5: Pre-market only + filters
# =============================================================================
print("\n" + "="*70)
print("SLICE 5: PRE-MARKET ONLY (before 9:30)")
print("="*70)

premarket = [s for s in valid if s.get('is_premarket')]
closes = [s['pct_to_close'] for s in premarket]
avg = sum(closes) / len(closes)
wr = len([c for c in closes if c > 0]) / len(closes) * 100
per_day = len(premarket) / 126
print(f"  All pre-market: n={len(premarket)} ({per_day:.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# Pre-market + ratio filters
for min_ratio in [5, 10, 15, 20]:
    subset = [s for s in premarket if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  Pre-mkt + >= {min_ratio}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# Pre-market first signal only
pm_first = {}
for s in premarket:
    key = (s['detection_time'][:10], s['symbol'])
    if key not in pm_first:
        pm_first[key] = s
pm_first_list = list(pm_first.values())

for min_ratio in [5, 10, 15]:
    subset = [s for s in pm_first_list if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  PM first + >= {min_ratio}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 6: Bullish flow only (>80% calls)
# =============================================================================
print("\n" + "="*70)
print("SLICE 6: BULLISH FLOW (>80% calls)")
print("="*70)

bullish = [s for s in valid if s.get('call_pct', 0) > 0.8]
closes = [s['pct_to_close'] for s in bullish]
avg = sum(closes) / len(closes)
wr = len([c for c in closes if c > 0]) / len(closes) * 100
per_day = len(bullish) / 126
print(f"  Bullish (>80% calls): n={len(bullish)} ({per_day:.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

for min_ratio in [5, 10, 15, 20]:
    subset = [s for s in bullish if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  Bullish + >= {min_ratio}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# Bullish + pre-market + first signal
bull_pm_first = {}
for s in bullish:
    if s.get('is_premarket'):
        key = (s['detection_time'][:10], s['symbol'])
        if key not in bull_pm_first:
            bull_pm_first[key] = s
bull_pm_first_list = list(bull_pm_first.values())

for min_ratio in [5, 10, 15]:
    subset = [s for s in bull_pm_first_list if s['ratio'] >= min_ratio]
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        print(f"  Bull + PM first + >= {min_ratio}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 7: Gap analysis (pre-market signals with gap at open)
# =============================================================================
print("\n" + "="*70)
print("SLICE 7: GAP AT OPEN ANALYSIS (pre-market signals)")
print("="*70)

with_gap = [s for s in premarket if s.get('gap_pct') is not None]
print(f"  Pre-market signals with gap data: {len(with_gap)}")

# Signal before gap vs after gap
no_gap_yet = [s for s in with_gap if -1 < s['gap_pct'] < 1]  # Gap < 1%
gapped_up = [s for s in with_gap if s['gap_pct'] >= 2]
gapped_down = [s for s in with_gap if s['gap_pct'] <= -2]

for name, subset in [("No gap yet (<1%)", no_gap_yet), ("Gapped up (>2%)", gapped_up), ("Gapped down (<-2%)", gapped_down)]:
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        print(f"  {name:20}: n={len(subset):5}, avg={avg:+.2f}%, win={wr:.1f}%")

# =============================================================================
# SLICE 8: Best combo - find the sweet spot
# =============================================================================
print("\n" + "="*70)
print("SLICE 8: OPTIMAL COMBINATIONS")
print("="*70)

# Pre-market + first signal + bullish + high ratio + no gap yet
candidates = []
for s in valid:
    if not s.get('is_premarket'):
        continue
    if s.get('call_pct', 0) < 0.7:
        continue
    if s.get('gap_pct') is not None and abs(s['gap_pct']) > 2:
        continue  # Skip if already gapped
    candidates.append(s)

# First signal only
best_first = {}
for s in candidates:
    key = (s['detection_time'][:10], s['symbol'])
    if key not in best_first:
        best_first[key] = s
best_list = list(best_first.values())

print(f"  Candidate pool (PM + bullish + no gap + first): {len(best_list)}")

for min_ratio in [3, 5, 7, 10, 15]:
    subset = [s for s in best_list if s['ratio'] >= min_ratio]
    if len(subset) >= 20:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes) / len(closes)
        wr = len([c for c in closes if c > 0]) / len(closes) * 100
        per_day = len(subset) / 126
        big_win = len([c for c in closes if c > 5])
        big_loss = len([c for c in closes if c < -5])
        print(f"  + >= {min_ratio:2}x: n={len(subset):5} ({per_day:4.1f}/day), avg={avg:+.2f}%, win={wr:.1f}%, +5%:{big_win}, -5%:{big_loss}")

# =============================================================================
# TOP SIGNALS FROM BEST COMBO
# =============================================================================
print("\n" + "="*70)
print("TOP 30 SIGNALS FROM BEST COMBO (PM + bullish + first + >=10x)")
print("="*70)

best_subset = [s for s in best_list if s['ratio'] >= 10]
sorted_best = sorted(best_subset, key=lambda x: x['pct_to_close'], reverse=True)

print("\nTop 15 winners:")
for s in sorted_best[:15]:
    t = s['detection_time'][11:16]
    gap = f"gap:{s['gap_pct']:+.1f}%" if s.get('gap_pct') else "no gap"
    print(f"  {s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} ${s.get('price_at_signal',0):>7.2f} => {s['pct_to_close']:+6.1f}% ({gap})")

print("\nBottom 15 losers:")
for s in sorted_best[-15:]:
    t = s['detection_time'][11:16]
    gap = f"gap:{s['gap_pct']:+.1f}%" if s.get('gap_pct') else "no gap"
    print(f"  {s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} ${s.get('price_at_signal',0):>7.2f} => {s['pct_to_close']:+6.1f}% ({gap})")
