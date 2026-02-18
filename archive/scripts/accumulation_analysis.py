"""
Critical Analysis: Accumulation Pattern vs Single Spike

HYPOTHESIS: Signals preceded by building activity (accumulation) perform 
better than isolated spikes.

CRITICAL QUESTIONS:
1. Do accumulation patterns actually exist in our data?
2. If so, do they predict better outcomes?
3. Or is this just another way to slice noise?

We'll test multiple definitions of "accumulation" to avoid cherry-picking.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

# Load existing backtest results
with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']

# Filter to valid signals with outcomes
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history']

print("="*80)
print("CRITICAL ANALYSIS: ACCUMULATION PATTERN VS SINGLE SPIKE")
print("="*80)
print(f"\nTotal valid signals: {len(valid):,}")

# =============================================================================
# PROBLEM 1: We don't have bucket history in the signal data
# We only have the triggered bucket, not preceding buckets
# =============================================================================

print("\n" + "="*80)
print("LIMITATION: Signal data only contains triggered bucket, not history")
print("="*80)
print("""
Each signal has:
- The bucket that triggered (notional, baseline, ratio)
- NOT the preceding buckets

To properly test accumulation, we'd need to go back to raw bucket data.
However, we CAN proxy this using multiple signals for same symbol on same day.
""")

# =============================================================================
# PROXY ANALYSIS: Multiple signals per symbol per day
# =============================================================================

print("\n" + "="*80)
print("PROXY 1: Multiple Signals Per Symbol Per Day")
print("="*80)
print("""
LOGIC: If a symbol triggers multiple times in a day, that could indicate
sustained/building activity vs a one-off spike.

CRITICAL QUESTION: Is multi-trigger actually accumulation, or just 
a very active day that keeps re-triggering?
""")

# Group signals by symbol + date
by_symbol_date = defaultdict(list)
for s in valid:
    key = (s['symbol'], s['detection_time'][:10])
    by_symbol_date[key].append(s)

# Classify by trigger count
single_trigger = []
multi_trigger_2_3 = []
multi_trigger_4_plus = []

for key, sigs in by_symbol_date.items():
    # Use first signal of the day for outcome (entry point)
    first_sig = min(sigs, key=lambda x: x['detection_time'])
    
    if len(sigs) == 1:
        single_trigger.append(first_sig)
    elif len(sigs) <= 3:
        multi_trigger_2_3.append(first_sig)
    else:
        multi_trigger_4_plus.append(first_sig)

def analyze_group(group, name):
    if not group:
        print(f"\n{name}: No signals")
        return None
    
    closes = [s['pct_to_close'] for s in group]
    avg = sum(closes) / len(closes)
    median = sorted(closes)[len(closes)//2]
    win_rate = len([c for c in closes if c > 0]) / len(closes) * 100
    big_win = len([c for c in closes if c > 5]) / len(closes) * 100
    big_loss = len([c for c in closes if c < -5]) / len(closes) * 100
    
    print(f"\n{name}:")
    print(f"  Count: {len(group):,} ({len(group)/len(valid)*100:.1f}% of signals)")
    print(f"  Win rate: {win_rate:.1f}%")
    print(f"  Avg return: {avg:+.2f}%")
    print(f"  Median return: {median:+.2f}%")
    print(f"  Big winners (>5%): {big_win:.1f}%")
    print(f"  Big losers (<-5%): {big_loss:.1f}%")
    
    return {'count': len(group), 'win_rate': win_rate, 'avg': avg}

analyze_group(single_trigger, "Single trigger per day")
analyze_group(multi_trigger_2_3, "2-3 triggers per day")
analyze_group(multi_trigger_4_plus, "4+ triggers per day")

print("\n" + "-"*80)
print("CRITICAL INTERPRETATION:")
print("-"*80)
print("""
If multi-trigger performs BETTER: Suggests sustained activity has value
If multi-trigger performs WORSE: Could be chasing momentum already exhausted
If NO DIFFERENCE: Trigger count is noise, not signal
""")

# =============================================================================
# PROXY 2: Time-based spread of triggers
# =============================================================================

print("\n" + "="*80)
print("PROXY 2: Early vs Late First Trigger")
print("="*80)
print("""
LOGIC: If accumulation thesis is correct, the FIRST trigger of the day
should be the best entry. Late triggers = already missed the move.
""")

# For multi-trigger days, compare first vs last trigger outcomes
multi_days = [(k, sigs) for k, sigs in by_symbol_date.items() if len(sigs) >= 3]

if multi_days:
    first_triggers = []
    last_triggers = []
    
    for key, sigs in multi_days:
        sorted_sigs = sorted(sigs, key=lambda x: x['detection_time'])
        first_triggers.append(sorted_sigs[0])
        last_triggers.append(sorted_sigs[-1])
    
    print(f"\nDays with 3+ triggers: {len(multi_days)}")
    analyze_group(first_triggers, "First trigger of day")
    analyze_group(last_triggers, "Last trigger of day")
    
    print("\n" + "-"*80)
    print("CRITICAL INTERPRETATION:")
    print("-"*80)
    print("""
If first trigger better: Early detection matters, accumulation thesis has merit
If last trigger better: Momentum is building, wait for confirmation
If same: Timing within day doesn't matter
""")

# =============================================================================
# PROXY 3: Ratio trend within day
# =============================================================================

print("\n" + "="*80)
print("PROXY 3: Ratio Trend (Increasing vs Decreasing)")
print("="*80)
print("""
LOGIC: If later triggers have HIGHER ratios than earlier ones,
activity is accelerating (bullish). If lower, momentum fading.
""")

increasing_ratio = []
decreasing_ratio = []
flat_ratio = []

for key, sigs in multi_days:
    if len(sigs) < 2:
        continue
    
    sorted_sigs = sorted(sigs, key=lambda x: x['detection_time'])
    first_ratio = sorted_sigs[0]['ratio']
    last_ratio = sorted_sigs[-1]['ratio']
    
    ratio_change = (last_ratio - first_ratio) / first_ratio
    
    if ratio_change > 0.2:  # 20% increase
        increasing_ratio.append(sorted_sigs[0])
    elif ratio_change < -0.2:  # 20% decrease
        decreasing_ratio.append(sorted_sigs[0])
    else:
        flat_ratio.append(sorted_sigs[0])

analyze_group(increasing_ratio, "Increasing ratio through day (accelerating)")
analyze_group(decreasing_ratio, "Decreasing ratio through day (fading)")
analyze_group(flat_ratio, "Flat ratio through day")

# =============================================================================
# PROXY 4: Pre-market build into regular hours
# =============================================================================

print("\n" + "="*80)
print("PROXY 4: Pre-market Activity Continuing into Regular Hours")
print("="*80)
print("""
LOGIC: If there's pre-market activity AND it continues into regular hours,
that's sustained interest vs just overnight positioning.
""")

premarket_only = []
premarket_into_regular = []
regular_only = []

for key, sigs in by_symbol_date.items():
    has_premarket = any(s.get('is_premarket') for s in sigs)
    has_regular = any(not s.get('is_premarket') for s in sigs)
    
    first_sig = min(sigs, key=lambda x: x['detection_time'])
    
    if has_premarket and has_regular:
        premarket_into_regular.append(first_sig)
    elif has_premarket:
        premarket_only.append(first_sig)
    else:
        regular_only.append(first_sig)

analyze_group(premarket_only, "Pre-market only (no follow-through)")
analyze_group(premarket_into_regular, "Pre-market INTO regular hours (sustained)")
analyze_group(regular_only, "Regular hours only")

# =============================================================================
# CRITICAL REALITY CHECK
# =============================================================================

print("\n" + "="*80)
print("CRITICAL REALITY CHECK")
print("="*80)

# Check if any of these filters actually move the needle
print("""
QUESTION: Do ANY of these accumulation proxies beat our best previous filter?

Previous best: Large trades + Bullish + Early + >=15x ratio
  - Win rate: 54.0%
  - Avg return: +0.36%
  - Count: 1,250
""")

# Combine best accumulation proxy with previous best filters
best_candidates = []
for key, sigs in by_symbol_date.items():
    if len(sigs) < 2:  # Need multiple triggers
        continue
    
    has_premarket = any(s.get('is_premarket') for s in sigs)
    has_regular = any(not s.get('is_premarket') for s in sigs)
    
    if not (has_premarket and has_regular):  # Need sustained activity
        continue
    
    first_sig = min(sigs, key=lambda x: x['detection_time'])
    
    # Add previous best filters
    if first_sig.get('call_pct', 0) < 0.7:  # Bullish
        continue
    if first_sig['ratio'] < 10:  # High ratio
        continue
    
    best_candidates.append(first_sig)

analyze_group(best_candidates, "COMBINED: Multi-trigger + PM->Regular + Bullish + >=10x")

# Even stricter
very_strict = [s for s in best_candidates if s['ratio'] >= 15]
analyze_group(very_strict, "COMBINED + >=15x ratio")

# =============================================================================
# THE HARD TRUTH
# =============================================================================

print("\n" + "="*80)
print("THE HARD TRUTH")
print("="*80)
print("""
Let's calculate: What win rate do we NEED for profitability?

Assumptions:
- Average winner: +3% (take profit)
- Average loser: -2% (stop loss)
- Commission/slippage: 0.1% per trade

Break-even calculation:
  WinRate * 3% - (1 - WinRate) * 2% - 0.1% = 0
  3*WR - 2 + 2*WR - 0.1 = 0
  5*WR = 2.1
  WR = 42%

So 42% win rate with 3:2 reward:risk = break even.
51% win rate = profitable IF we can maintain 3:2 RR.

BUT: Our current data shows average winner and loser are roughly equal.
With 1:1 RR, we need >50% win rate just to break even after costs.

Current: 51.4% with ~1:1 RR = MARGINAL at best
Target: 55%+ with 2:1 RR = Actually profitable
""")

print("\n" + "="*80)
print("ACCUMULATION PATTERN VERDICT")
print("="*80)
