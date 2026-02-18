"""
Quick check: Uptrend + High Score combination
"""
import json
import os

results_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results"

with open(os.path.join(results_dir, "e2e_backtest_v2_strikes_sweeps_price_scored.json")) as f:
    data = json.load(f)

with open(os.path.join(results_dir, "e2e_backtest_with_outcomes.json")) as f:
    outcomes = json.load(f)

# Build lookup
outcomes_lookup = {}
for s in outcomes['signals']:
    key = (s['symbol'], s['detection_time'][:16])
    outcomes_lookup[key] = s

# Merge
signals = data['signals']
for s in signals:
    key = (s['symbol'], s['detection_time'][:16])
    if key in outcomes_lookup:
        s.update(outcomes_lookup[key])

valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')]

print("="*70)
print("UPTREND + SCORE COMBINATIONS")
print("="*70)

uptrend = [s for s in valid if s.get('trend') == 1]
downtrend = [s for s in valid if s.get('trend') == -1]

print(f"\nUptrend signals: {len(uptrend):,}")
print(f"Downtrend signals: {len(downtrend):,}")

def analyze(group, name):
    if not group:
        return
    closes = [s['pct_to_close'] for s in group]
    avg = sum(closes)/len(closes)
    wr = len([c for c in closes if c > 0])/len(closes)*100
    big_win = len([c for c in closes if c > 5])/len(closes)*100
    big_loss = len([c for c in closes if c < -5])/len(closes)*100
    per_day = len(group) / 126
    print(f"  {name}: n={len(group):,} ({per_day:.0f}/day), WR={wr:.1f}%, avg={avg:+.2f}%, +5%={big_win:.1f}%, -5%={big_loss:.1f}%")

print("\n--- UPTREND + SCORE ---")
for min_score in [5, 6, 7, 8, 9, 10]:
    subset = [s for s in uptrend if s['score'] >= min_score]
    analyze(subset, f"Uptrend + Score>={min_score}")

print("\n--- DOWNTREND + SCORE (for comparison) ---")
for min_score in [5, 7, 9]:
    subset = [s for s in downtrend if s['score'] >= min_score]
    analyze(subset, f"Downtrend + Score>={min_score}")

# Now add more filters
print("\n" + "="*70)
print("UPTREND + SCORE + ADDITIONAL FILTERS")
print("="*70)

# Uptrend + high score + bullish flow
print("\n--- + Bullish (>80% calls) ---")
for min_score in [7, 8, 9]:
    subset = [s for s in uptrend if s['score'] >= min_score and s.get('call_pct', 0) > 0.8]
    analyze(subset, f"Uptrend + Score>={min_score} + Bullish")

# Uptrend + high score + high ratio
print("\n--- + High Ratio (>=15x) ---")
for min_score in [7, 8, 9]:
    subset = [s for s in uptrend if s['score'] >= min_score and s.get('ratio', 0) >= 15]
    analyze(subset, f"Uptrend + Score>={min_score} + >=15x")

# Uptrend + high score + high sweeps
print("\n--- + High Sweeps (>=30%) ---")
for min_score in [7, 8, 9]:
    subset = [s for s in uptrend if s['score'] >= min_score and s.get('sweep_pct', 0) >= 0.3]
    analyze(subset, f"Uptrend + Score>={min_score} + Sweeps")

# THE GOLDEN COMBO
print("\n" + "="*70)
print("GOLDEN COMBO: Uptrend + Score>=8 + Bullish + Sweeps")
print("="*70)

golden = [s for s in uptrend 
          if s['score'] >= 8 
          and s.get('call_pct', 0) > 0.8
          and s.get('sweep_pct', 0) >= 0.3]

analyze(golden, "GOLDEN COMBO")

if golden:
    print("\n  Top 20 signals:")
    sorted_golden = sorted(golden, key=lambda x: x['pct_to_close'], reverse=True)
    for s in sorted_golden[:20]:
        print(f"    {s['detection_time'][:10]} {s['symbol']:8} score={s['score']} ratio={s['ratio']:.1f}x => {s['pct_to_close']:+.1f}%")
    
    print("\n  Bottom 10 signals:")
    for s in sorted_golden[-10:]:
        print(f"    {s['detection_time'][:10]} {s['symbol']:8} score={s['score']} ratio={s['ratio']:.1f}x => {s['pct_to_close']:+.1f}%")

# Target: 10/day with 55%+ win rate
print("\n" + "="*70)
print("FINDING THE 10/DAY TARGET")
print("="*70)

# Sort by increasingly strict filters
filters = [
    ("Uptrend + Score>=9", [s for s in uptrend if s['score'] >= 9]),
    ("Uptrend + Score>=9 + Bullish", [s for s in uptrend if s['score'] >= 9 and s.get('call_pct', 0) > 0.8]),
    ("Uptrend + Score>=9 + Sweeps", [s for s in uptrend if s['score'] >= 9 and s.get('sweep_pct', 0) >= 0.3]),
    ("Uptrend + Score>=10", [s for s in uptrend if s['score'] >= 10]),
    ("Uptrend + Score>=8 + Bullish + Sweeps", golden),
]

print(f"\n{'Filter':<45} {'N':<8} {'N/Day':<8} {'WR%':<8} {'Avg%':<10}")
print("-"*80)
for name, subset in filters:
    if subset:
        closes = [s['pct_to_close'] for s in subset]
        wr = len([c for c in closes if c > 0])/len(closes)*100
        avg = sum(closes)/len(closes)
        per_day = len(subset)/126
        print(f"{name:<45} {len(subset):<8} {per_day:<8.1f} {wr:<8.1f} {avg:<+10.2f}")
