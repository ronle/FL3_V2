"""
Analyze outcomes by score from enhanced backtest
"""
import json
from collections import defaultdict

# Find the latest scored results file
import os
results_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results"

# List files to find the scored one
files = os.listdir(results_dir)
scored_files = [f for f in files if 'scored' in f or 'v2' in f]
print(f"Available files: {scored_files}")

# Load the most recent v2 results
for fname in ['e2e_backtest_v2_strikes_sweeps_price_scored.json', 
              'e2e_backtest_v2_scored.json',
              'e2e_backtest_v2.json']:
    fpath = os.path.join(results_dir, fname)
    if os.path.exists(fpath):
        print(f"Loading: {fname}")
        with open(fpath) as f:
            data = json.load(f)
        break
else:
    print("No scored file found!")
    exit()

signals = data['signals']
print(f"Total signals: {len(signals):,}")

# Also load outcomes from original backtest
outcomes_file = os.path.join(results_dir, "e2e_backtest_with_outcomes.json")
if os.path.exists(outcomes_file):
    with open(outcomes_file) as f:
        outcomes_data = json.load(f)
    
    # Build lookup: (symbol, detection_time) -> outcomes
    outcomes_lookup = {}
    for s in outcomes_data['signals']:
        key = (s['symbol'], s['detection_time'][:16])  # Truncate to minute
        outcomes_lookup[key] = {
            'pct_to_close': s.get('pct_to_close'),
            'pct_max_gain': s.get('pct_max_gain'),
            'pct_max_loss': s.get('pct_max_loss'),
            'filtered_out': s.get('filtered_out'),
            'gap_pct': s.get('gap_pct'),
            'is_premarket': s.get('is_premarket'),
        }
    
    # Merge outcomes into scored signals
    matched = 0
    for s in signals:
        key = (s['symbol'], s['detection_time'][:16])
        if key in outcomes_lookup:
            s.update(outcomes_lookup[key])
            matched += 1
    
    print(f"Matched outcomes: {matched:,} / {len(signals):,}")

# Filter to valid signals
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')]

print(f"Valid signals with outcomes: {len(valid):,}")

print("\n" + "="*80)
print("OUTCOMES BY SCORE")
print("="*80)

# Group by score
by_score = defaultdict(list)
for s in valid:
    by_score[s['score']].append(s)

print(f"\n{'Score':<8} {'Count':<10} {'Win%':<8} {'Avg%':<10} {'Med%':<10} {'>5%':<8} {'<-5%':<8}")
print("-"*70)

for score in sorted(by_score.keys()):
    group = by_score[score]
    closes = [s['pct_to_close'] for s in group]
    
    avg = sum(closes) / len(closes)
    median = sorted(closes)[len(closes)//2]
    win_rate = len([c for c in closes if c > 0]) / len(closes) * 100
    big_win = len([c for c in closes if c > 5]) / len(closes) * 100
    big_loss = len([c for c in closes if c < -5]) / len(closes) * 100
    
    print(f"{score:<8} {len(group):<10,} {win_rate:<8.1f} {avg:<+10.2f} {median:<+10.2f} {big_win:<8.1f} {big_loss:<8.1f}")

# Cumulative analysis (score >= X)
print("\n" + "="*80)
print("CUMULATIVE: SCORE >= THRESHOLD")
print("="*80)

print(f"\n{'Threshold':<12} {'Count':<10} {'Per Day':<10} {'Win%':<8} {'Avg%':<10} {'>5%':<8} {'<-5%':<8}")
print("-"*80)

for threshold in [0, 3, 5, 6, 7, 8, 9, 10]:
    group = [s for s in valid if s['score'] >= threshold]
    if not group:
        continue
    
    closes = [s['pct_to_close'] for s in group]
    avg = sum(closes) / len(closes)
    win_rate = len([c for c in closes if c > 0]) / len(closes) * 100
    big_win = len([c for c in closes if c > 5]) / len(closes) * 100
    big_loss = len([c for c in closes if c < -5]) / len(closes) * 100
    per_day = len(group) / 126
    
    print(f">= {threshold:<9} {len(group):<10,} {per_day:<10.0f} {win_rate:<8.1f} {avg:<+10.2f} {big_win:<8.1f} {big_loss:<8.1f}")

# Check if enhanced metrics add value
print("\n" + "="*80)
print("ENHANCED METRICS ANALYSIS")
print("="*80)

# Strike concentration
if any(s.get('strike_concentration') for s in valid):
    print("\n--- Strike Concentration ---")
    low_conc = [s for s in valid if s.get('strike_concentration', 1) < 0.3]
    high_conc = [s for s in valid if s.get('strike_concentration', 0) >= 0.7]
    
    for name, group in [("Low (concentrated)", low_conc), ("High (dispersed)", high_conc)]:
        if group:
            closes = [s['pct_to_close'] for s in group]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(group):,}, WR={wr:.1f}%, avg={avg:+.2f}%")

# Sweep percentage
if any(s.get('sweep_pct') for s in valid):
    print("\n--- Sweep Percentage ---")
    low_sweep = [s for s in valid if s.get('sweep_pct', 0) < 0.1]
    high_sweep = [s for s in valid if s.get('sweep_pct', 0) >= 0.3]
    
    for name, group in [("Low sweeps (<10%)", low_sweep), ("High sweeps (>=30%)", high_sweep)]:
        if group:
            closes = [s['pct_to_close'] for s in group]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(group):,}, WR={wr:.1f}%, avg={avg:+.2f}%")

# OTM percentage
if any(s.get('otm_pct') for s in valid):
    print("\n--- OTM Percentage ---")
    low_otm = [s for s in valid if s.get('otm_pct', 0) < 0.3]
    high_otm = [s for s in valid if s.get('otm_pct', 0) >= 0.7]
    
    for name, group in [("Low OTM (<30%)", low_otm), ("High OTM (>=70%)", high_otm)]:
        if group:
            closes = [s['pct_to_close'] for s in group]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(group):,}, WR={wr:.1f}%, avg={avg:+.2f}%")

# Trend
if any(s.get('trend') is not None for s in valid):
    print("\n--- Price Trend ---")
    uptrend = [s for s in valid if s.get('trend') == 1]
    downtrend = [s for s in valid if s.get('trend') == -1]
    
    for name, group in [("Uptrend", uptrend), ("Downtrend", downtrend)]:
        if group:
            closes = [s['pct_to_close'] for s in group]
            avg = sum(closes) / len(closes)
            wr = len([c for c in closes if c > 0]) / len(closes) * 100
            print(f"  {name}: n={len(group):,}, WR={wr:.1f}%, avg={avg:+.2f}%")

# Best signals
print("\n" + "="*80)
print("TOP 20 HIGHEST SCORING SIGNALS WITH OUTCOMES")
print("="*80)

top_scored = sorted(valid, key=lambda x: (-x['score'], -x['ratio']))[:20]
print(f"\n{'Date':<12} {'Symbol':<8} {'Score':<6} {'Ratio':<8} {'Call%':<8} {'Result':<10}")
print("-"*60)
for s in top_scored:
    print(f"{s['detection_time'][:10]:<12} {s['symbol']:<8} {s['score']:<6} {s['ratio']:<8.1f} {s.get('call_pct',0)*100:<8.0f} {s['pct_to_close']:+.1f}%")
