"""Quick analysis of E2E backtest with outcomes"""
import json

with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']
print(f"Total signals: {len(signals)}")

# Split by filter status
all_with_price = [s for s in signals if s.get('pct_to_close') is not None]
filtered_out = [s for s in all_with_price if s.get('filtered_out')]
valid = [s for s in all_with_price if not s.get('filtered_out')]

print(f"With price data: {len(all_with_price)}")
print(f"Filtered out: {len(filtered_out)}")
print(f"Valid signals: {len(valid)}")

# Filter reasons
reasons = {}
for s in filtered_out:
    r = s.get('filter_reason', 'unknown')
    if 'penny' in r:
        r = 'penny_stock'
    elif 'etf' in r:
        r = 'etf'
    reasons[r] = reasons.get(r, 0) + 1
print(f"\nFilter reasons: {reasons}")

# Split by baseline source
history_based = [s for s in valid if s.get('baseline_source') == 'history']
default_based = [s for s in valid if s.get('baseline_source') == 'default']

print(f"\n=== BY BASELINE SOURCE ===")
print(f"History-based: {len(history_based)}")
print(f"Default-based: {len(default_based)}")

# Analyze history-based only (the good stuff)
if history_based:
    closes = [s['pct_to_close'] for s in history_based]
    print(f"\n=== HISTORY-BASED SIGNALS (filtered, non-ETF, >$5 stocks) ===")
    print(f"Count: {len(history_based)}")
    print(f"Avg % to close: {sum(closes)/len(closes):.2f}%")
    print(f"Median: {sorted(closes)[len(closes)//2]:.2f}%")
    print(f"Win rate: {len([c for c in closes if c > 0])/len(closes)*100:.1f}%")
    print(f"Big winners (>5%): {len([c for c in closes if c > 5])}")
    print(f"Big losers (<-5%): {len([c for c in closes if c < -5])}")
    
    # By ratio
    print(f"\n--- By Ratio ---")
    for name, lo, hi in [('3-5x', 3, 5), ('5-10x', 5, 10), ('10-20x', 10, 20), ('20-50x', 20, 50), ('50x+', 50, 99999)]:
        bucket = [s for s in history_based if lo <= s['ratio'] < hi]
        if bucket:
            avg = sum(s['pct_to_close'] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
            print(f"  {name:10}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # By confidence
    print(f"\n--- By Confidence ---")
    for name, lo, hi in [('low (<0.5)', 0, 0.5), ('med (0.5-0.8)', 0.5, 0.8), ('high (>=0.8)', 0.8, 1.1)]:
        bucket = [s for s in history_based if lo <= s.get('confidence', 0) < hi]
        if bucket:
            avg = sum(s['pct_to_close'] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
            print(f"  {name:15}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # Pre-market vs regular
    print(f"\n--- Pre-market vs Regular Hours ---")
    premarket = [s for s in history_based if s.get('is_premarket')]
    regular = [s for s in history_based if not s.get('is_premarket')]
    
    if premarket:
        avg = sum(s['pct_to_close'] for s in premarket) / len(premarket)
        wr = len([s for s in premarket if s['pct_to_close'] > 0]) / len(premarket) * 100
        print(f"  Pre-market:  n={len(premarket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    if regular:
        avg = sum(s['pct_to_close'] for s in regular) / len(regular)
        wr = len([s for s in regular if s['pct_to_close'] > 0]) / len(regular) * 100
        print(f"  Regular hrs: n={len(regular):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # By call percentage (bullish vs bearish flow)
    print(f"\n--- By Call % (Flow Direction) ---")
    for name, lo, hi in [('Bearish (<30%)', 0, 0.3), ('Mixed (30-70%)', 0.3, 0.7), ('Bullish (>70%)', 0.7, 1.1)]:
        bucket = [s for s in history_based if lo <= s.get('call_pct', 0.5) < hi]
        if bucket:
            avg = sum(s['pct_to_close'] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
            print(f"  {name:18}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # Top 20 performers
    print(f"\n--- Top 20 Performers ---")
    sorted_signals = sorted(history_based, key=lambda x: x['pct_to_close'], reverse=True)
    for s in sorted_signals[:20]:
        t = s['detection_time'][11:16]
        p = f"${s['price_at_signal']:.2f}" if s.get('price_at_signal') else 'N/A'
        print(f"{s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} {p:>8} => {s['pct_to_close']:+.1f}%")
    
    # Bottom 20
    print(f"\n--- Bottom 20 Performers ---")
    for s in sorted_signals[-20:]:
        t = s['detection_time'][11:16]
        p = f"${s['price_at_signal']:.2f}" if s.get('price_at_signal') else 'N/A'
        print(f"{s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} {p:>8} => {s['pct_to_close']:+.1f}%")

# Also check default-based for comparison
if default_based:
    closes = [s['pct_to_close'] for s in default_based]
    print(f"\n=== DEFAULT-BASED SIGNALS (for comparison) ===")
    print(f"Count: {len(default_based)}")
    print(f"Avg % to close: {sum(closes)/len(closes):.2f}%")
    print(f"Win rate: {len([c for c in closes if c > 0])/len(closes)*100:.1f}%")
