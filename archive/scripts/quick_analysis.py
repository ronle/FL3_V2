"""Quick analysis of backtest outcomes"""
import json

with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = [s for s in data['signals'] if s.get('pct_to_close') is not None]
print(f"Total signals with outcomes: {len(signals)}")

closes = [s['pct_to_close'] for s in signals]
print(f"\n=== OVERALL ===")
print(f"Avg % to close: {sum(closes)/len(closes):.2f}%")
print(f"Median: {sorted(closes)[len(closes)//2]:.2f}%")
print(f"Win rate: {len([c for c in closes if c > 0])/len(closes)*100:.1f}%")
print(f"Big winners (>5%): {len([c for c in closes if c > 5])}")
print(f"Big losers (<-5%): {len([c for c in closes if c < -5])}")

# Pre-market
print(f"\n=== PRE-MARKET SIGNALS ===")
pm = [s for s in signals if s.get('is_premarket')]
pm_closes = [s['pct_to_close'] for s in pm]
print(f"Count: {len(pm)}")
print(f"Avg: {sum(pm_closes)/len(pm_closes):.2f}%")
print(f"Win rate: {len([c for c in pm_closes if c > 0])/len(pm_closes)*100:.1f}%")

# By ratio
print(f"\n=== BY RATIO BUCKET ===")
for name, lo, hi in [('2-5x', 2, 5), ('5-10x', 5, 10), ('10-50x', 10, 50), ('50-100x', 50, 100), ('100x+', 100, 9999999)]:
    bucket = [s for s in signals if lo <= s['ratio'] < hi]
    if bucket:
        avg = sum(s['pct_to_close'] for s in bucket) / len(bucket)
        wr = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
        print(f"  {name:10}: n={len(bucket):4}, avg={avg:+.2f}%, win={wr:.1f}%")

# Top 15
print(f"\n=== TOP 15 PERFORMERS ===")
sorted_signals = sorted(signals, key=lambda x: x['pct_to_close'], reverse=True)
for s in sorted_signals[:15]:
    t = s['detection_time'][11:16]
    print(f"{s['date']} {s['underlying']:8} {s['ratio']:>6.1f}x @ {t}  => {s['pct_to_close']:+.1f}%")

# Bottom 15
print(f"\n=== BOTTOM 15 PERFORMERS ===")
for s in sorted_signals[-15:]:
    t = s['detection_time'][11:16]
    print(f"{s['date']} {s['underlying']:8} {s['ratio']:>6.1f}x @ {t}  => {s['pct_to_close']:+.1f}%")

# AAOI
print(f"\n=== AAOI ===")
aaoi = [s for s in signals if s['underlying'] == 'AAOI']
for s in aaoi:
    print(f"{s['date']} @ {s['detection_time'][11:16]}: ratio={s['ratio']:.1f}x, price=${s['price_at_detection']}, close={s['pct_to_close']:+.2f}%, max_gain={s.get('pct_max_gain')}%")
