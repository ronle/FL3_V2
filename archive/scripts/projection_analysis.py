"""
Projection Analysis: Potential Value of Additional Signals

We have 400K signals with 51.4% win rate and +0.08% avg return (essentially random).
Question: What would different filters need to achieve to create tradeable edge?

This analysis projects potential value of adding:
1. Greeks (Delta, IV Rank)
2. Strike clustering / OTM concentration  
3. Price TA (support/resistance, trend)
4. Trade classification (sweeps vs blocks)
"""

import json

# Load current results
with open(r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json") as f:
    data = json.load(f)

signals = data['signals']
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history']

print("="*80)
print("PROJECTION ANALYSIS: POTENTIAL VALUE OF ADDITIONAL SIGNALS")
print("="*80)

# Current baseline stats
closes = [s['pct_to_close'] for s in valid]
current_avg = sum(closes)/len(closes)
current_wr = len([c for c in closes if c > 0])/len(closes)*100
big_winners = [s for s in valid if s['pct_to_close'] > 5]
big_losers = [s for s in valid if s['pct_to_close'] < -5]

print(f"\n=== CURRENT BASELINE (Volume-Only Detection) ===")
print(f"Total signals: {len(valid):,}")
print(f"Signals/day: {len(valid)/126:.0f}")
print(f"Win rate: {current_wr:.1f}%")
print(f"Avg return: {current_avg:+.2f}%")
print(f"Big winners (>5%): {len(big_winners):,} ({len(big_winners)/len(valid)*100:.2f}%)")
print(f"Big losers (<-5%): {len(big_losers):,} ({len(big_losers)/len(valid)*100:.2f}%)")

# What the big winners look like (our target)
print(f"\n=== BIG WINNER CHARACTERISTICS ===")
bw_ratios = [s['ratio'] for s in big_winners]
bw_calls = [s.get('call_pct', 0.5) for s in big_winners]
bw_premarket = [s for s in big_winners if s.get('is_premarket')]
bw_early = [s for s in big_winners if s.get('is_premarket') and int(s['detection_time'][11:13]) < 7]

print(f"Median ratio: {sorted(bw_ratios)[len(bw_ratios)//2]:.1f}x")
print(f"% Bullish (>80% calls): {len([c for c in bw_calls if c > 0.8])/len(bw_calls)*100:.1f}%")
print(f"% Pre-market: {len(bw_premarket)/len(big_winners)*100:.1f}%")
print(f"% Early (4-7 AM): {len(bw_early)/len(big_winners)*100:.1f}%")

# What the big losers look like (what to avoid)
print(f"\n=== BIG LOSER CHARACTERISTICS ===")
bl_ratios = [s['ratio'] for s in big_losers]
bl_calls = [s.get('call_pct', 0.5) for s in big_losers]
bl_premarket = [s for s in big_losers if s.get('is_premarket')]
bl_early = [s for s in big_losers if s.get('is_premarket') and int(s['detection_time'][11:13]) < 7]

print(f"Median ratio: {sorted(bl_ratios)[len(bl_ratios)//2]:.1f}x")
print(f"% Bullish (>80% calls): {len([c for c in bl_calls if c > 0.8])/len(bl_calls)*100:.1f}%")
print(f"% Pre-market: {len(bl_premarket)/len(big_losers)*100:.1f}%")
print(f"% Early (4-7 AM): {len(bl_early)/len(big_losers)*100:.1f}%")

print("\n" + "="*80)
print("PROJECTION: WHAT EACH FILTER WOULD NEED TO ACHIEVE")
print("="*80)

print("""
For a filter to be valuable, it needs to do ONE of:
  A) Increase win rate significantly (to 55%+ for tradeable edge)
  B) Increase avg return (to 0.5%+ for meaningful expectancy)
  C) Dramatically reduce signal count while maintaining/improving metrics
  D) Identify big winners more often OR filter out big losers

Current state: 2.0% are big winners, 1.6% are big losers
Target state: 5%+ big winners, <1% big losers = strong edge
""")

print("\n" + "-"*80)
print("1. GREEKS (Delta, Gamma, IV Rank)")
print("-"*80)
print("""
HYPOTHESIS: 
- High delta exposure = directional bet (informed trader)
- Low delta / high gamma = lottery ticket (speculative)
- High IV rank = expensive premium (event expected)
- Low IV rank + volume spike = cheap positioning before move

POTENTIAL VALUE:
- Delta weighting could separate hedging from speculation
- IV rank could filter to "pre-event" setups
- Gamma concentration could identify "squeeze" setups

PROJECTION:
- If 30% of bullish signals have >0.7 delta concentration -> those might have 55%+ win rate
- If low IV rank (<30%) + volume spike -> could predict 2x more big winners
- Estimated lift: +2-5% win rate, +0.2-0.5% avg return

DATA NEEDED:
- ORATS provides Greeks at EOD
- Would need intraday Greeks from options prices (Black-Scholes calc)
- Strike/expiry from trade data (we have this!)
""")

print("\n" + "-"*80)
print("2. STRIKE CLUSTERING / OTM CONCENTRATION")
print("-"*80)
print("""
HYPOTHESIS:
- Flow concentrated on 1-2 strikes = targeted bet (informed)
- Flow spread across many strikes = market maker activity (noise)
- OTM calls = cheap leverage, lottery tickets
- ATM/ITM calls = serious directional bet

POTENTIAL VALUE:
- We already have unique_options count in buckets
- Low unique_options + high volume = concentrated bet
- Could calculate "strike entropy" to measure clustering

PROJECTION:
- Top 20% most concentrated signals might have 54%+ win rate
- OTM-heavy flow before big moves could have 3x more big winners
- Estimated lift: +2-4% win rate, +0.3-0.6% avg return

DATA NEEDED:
- Parse strike price from option symbol (we have this!)
- Get underlying price to calculate moneyness
- Calculate concentration metrics
""")

print("\n" + "-"*80)
print("3. PRICE TA (Support/Resistance, Trend)")
print("-"*80)
print("""
HYPOTHESIS:
- Volume spike + price at support = bounce setup
- Volume spike + price at resistance = breakout setup
- Volume spike + downtrend = catching falling knife (bad)
- Volume spike + uptrend = momentum continuation (good)

POTENTIAL VALUE:
- Filter out signals where price already extended
- Identify signals near key levels

PROJECTION:
- Signals with price <5% from 20-day low might have 55%+ win rate
- Signals in uptrend (price > 20 SMA) might have +0.3% better avg
- Estimated lift: +3-5% win rate, +0.2-0.4% avg return

DATA NEEDED:
- Stock minute bars (we have this!)
- Calculate moving averages, support/resistance levels
- Measure distance from recent high/low
""")

print("\n" + "-"*80)
print("4. TRADE CLASSIFICATION (Sweeps vs Blocks)")
print("-"*80)
print("""
HYPOTHESIS:
- Sweeps (hitting multiple exchanges rapidly) = urgency = informed
- Blocks (single large trade) = institutional, could be hedging
- Many small prints = retail accumulation
- Few large prints = institutional positioning

POTENTIAL VALUE:
- Sweep detection = urgency indicator
- Block trades with bullish flow = institutional conviction

PROJECTION:
- Sweep-heavy signals might have 55%+ win rate
- Block trades in illiquid names might predict big moves
- Estimated lift: +2-4% win rate, +0.2-0.5% avg return

DATA NEEDED:
- Trade conditions codes (we have this in flat files!)
- Condition 209 = intermarket sweep
- Parse and classify trade types
""")

print("\n" + "="*80)
print("COMBINED PROJECTION: MULTI-FACTOR SCORING")
print("="*80)
print("""
If we combine factors into a scoring system:

EXAMPLE SCORING:
  +2 points: Early detection (4-7 AM)
  +2 points: Bullish flow (>80% calls)
  +2 points: High ratio (>10x)
  +1 point: Concentrated strikes (low unique_options)
  +1 point: OTM-heavy flow
  +1 point: Sweep trades present
  +1 point: Price near support / in uptrend
  +1 point: Low IV rank
  -2 points: Bearish flow (<30% calls)
  -1 point: Price already extended
  -1 point: High IV rank (event priced in)

PROJECTED OUTCOMES BY SCORE:

  Score 0-2 (noise):     ~50% win rate, +0.0% avg (filter out)
  Score 3-4 (weak):      ~52% win rate, +0.1% avg  
  Score 5-6 (moderate):  ~55% win rate, +0.3% avg
  Score 7-8 (strong):    ~58% win rate, +0.6% avg
  Score 9+  (very high): ~62% win rate, +1.0% avg (rare, <1% of signals)

With score >= 5 filter:
  - Signal count: ~50K (from 400K) = 400/day
  - Win rate: ~55%
  - Avg return: ~0.3%
  - Big winners: ~4% (2x current rate)
  - Expectancy: 0.55 * 0.3 - 0.45 * 0.2 = +0.07% per trade
  
With score >= 7 filter:
  - Signal count: ~10K = 80/day
  - Win rate: ~58%
  - Avg return: ~0.6%
  - Big winners: ~6% (3x current rate)
  - Expectancy: 0.58 * 0.6 - 0.42 * 0.3 = +0.22% per trade
""")

print("\n" + "="*80)
print("IMPLEMENTATION PRIORITY (Effort vs Value)")
print("="*80)
print("""
| Feature              | Effort | Data Available? | Projected Value | Priority |
|----------------------|--------|-----------------|-----------------|----------|
| Strike clustering    | Low    | YES (parse OCC) | +2-4% WR        | HIGH     |
| OTM concentration    | Low    | YES (parse OCC) | +3% WR          | HIGH     |
| Trade conditions     | Low    | YES (flat file) | +2-4% WR        | HIGH     |
| Price TA (basic)     | Medium | YES (stock bars)| +3-5% WR        | MEDIUM   |
| Greeks (calculated)  | High   | Need prices     | +2-5% WR        | MEDIUM   |
| IV Rank              | High   | Need ORATS/calc | +2-3% WR        | LOW      |

RECOMMENDED ORDER:
1. Strike clustering + OTM concentration (easy wins, data ready)
2. Trade condition classification (sweeps)
3. Basic price TA (distance from support, trend)
4. Greeks later (more complex, may not add much)
""")

print("\n" + "="*80)
print("QUICK WIN: ANALYZE STRIKE CLUSTERING WITH CURRENT DATA")
print("="*80)
print("""
We already track 'unique_options' per bucket (number of distinct contracts).

A bucket with:
  - High notional + FEW unique options = concentrated bet
  - High notional + MANY unique options = spread activity

Let's see if we can proxy this from existing data...
""")

# Check if we have prints (trade count) data
sample = valid[0]
print(f"\nSample signal fields: {list(sample.keys())}")
print(f"  prints: {sample.get('prints')}")
print(f"  contracts: {sample.get('contracts')}")

# We have prints (trade count) and contracts - can calculate avg trade size
# High contracts / low prints = block trades
# Low contracts / high prints = retail accumulation

print("\n" + "-"*80)
print("PROXY ANALYSIS: Trade Size (contracts / prints)")
print("-"*80)

for s in valid:
    if s.get('prints', 0) > 0:
        s['avg_trade_size'] = s['contracts'] / s['prints']
    else:
        s['avg_trade_size'] = 0

with_size = [s for s in valid if s['avg_trade_size'] > 0]
sizes = [s['avg_trade_size'] for s in with_size]
median_size = sorted(sizes)[len(sizes)//2]
print(f"Median avg trade size: {median_size:.1f} contracts/print")

# Split by trade size
large_trades = [s for s in with_size if s['avg_trade_size'] >= median_size * 2]  # 2x median
small_trades = [s for s in with_size if s['avg_trade_size'] <= median_size / 2]  # 0.5x median

print(f"\nLarge avg trade size (>{median_size*2:.0f} contracts/print): {len(large_trades):,}")
if large_trades:
    lt_closes = [s['pct_to_close'] for s in large_trades]
    lt_avg = sum(lt_closes)/len(lt_closes)
    lt_wr = len([c for c in lt_closes if c > 0])/len(lt_closes)*100
    lt_big_win = len([c for c in lt_closes if c > 5])/len(lt_closes)*100
    print(f"  Win rate: {lt_wr:.1f}%")
    print(f"  Avg return: {lt_avg:+.2f}%")
    print(f"  Big winners: {lt_big_win:.2f}%")

print(f"\nSmall avg trade size (<{median_size/2:.0f} contracts/print): {len(small_trades):,}")
if small_trades:
    st_closes = [s['pct_to_close'] for s in small_trades]
    st_avg = sum(st_closes)/len(st_closes)
    st_wr = len([c for c in st_closes if c > 0])/len(st_closes)*100
    st_big_win = len([c for c in st_closes if c > 5])/len(st_closes)*100
    print(f"  Win rate: {st_wr:.1f}%")
    print(f"  Avg return: {st_avg:+.2f}%")
    print(f"  Big winners: {st_big_win:.2f}%")

# Combine with other filters
print("\n" + "-"*80)
print("COMBINED: Large trades + Bullish + Early + High ratio")
print("-"*80)

for min_ratio in [5, 10, 15]:
    subset = [s for s in large_trades 
              if s['ratio'] >= min_ratio
              and s.get('call_pct', 0) > 0.8
              and s.get('is_premarket')
              and int(s['detection_time'][11:13]) < 7]
    
    if len(subset) >= 20:
        closes = [s['pct_to_close'] for s in subset]
        avg = sum(closes)/len(closes)
        wr = len([c for c in closes if c > 0])/len(closes)*100
        big_win = len([c for c in closes if c > 5])/len(closes)*100
        print(f"  Large + Bullish + Early + >={min_ratio}x: n={len(subset):4}, WR={wr:.1f}%, avg={avg:+.2f}%, big_win={big_win:.1f}%")
