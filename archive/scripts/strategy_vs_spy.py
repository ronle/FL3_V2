"""
Compare strategy performance vs SPY buy-and-hold
"""
import json
import gzip
import os
from datetime import date, timedelta
from collections import defaultdict

results_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results"
stocks_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\stocks"

# Load scored signals
with open(os.path.join(results_dir, "e2e_backtest_v2_strikes_sweeps_price_scored.json")) as f:
    data = json.load(f)

with open(os.path.join(results_dir, "e2e_backtest_with_outcomes.json")) as f:
    outcomes = json.load(f)

# Merge outcomes
outcomes_lookup = {}
for s in outcomes['signals']:
    key = (s['symbol'], s['detection_time'][:16])
    outcomes_lookup[key] = s

signals = data['signals']
for s in signals:
    key = (s['symbol'], s['detection_time'][:16])
    if key in outcomes_lookup:
        s.update(outcomes_lookup[key])

# Filter to our best strategy: Uptrend + Score >= 10
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('trend') == 1
         and s.get('score', 0) >= 10]

print("="*70)
print("STRATEGY VS SPY COMPARISON")
print("="*70)
print(f"\nStrategy: Uptrend + Score >= 10")
print(f"Total signals: {len(valid):,}")

# Get date range
dates = sorted(set(s['detection_time'][:10] for s in valid))
start_date = dates[0]
end_date = dates[-1]
print(f"Period: {start_date} to {end_date}")

# =============================================================================
# Load SPY daily prices
# =============================================================================
spy_prices = {}

# Scan all stock files for SPY
current = date.fromisoformat(start_date) - timedelta(days=5)  # Get a few days before
end = date.fromisoformat(end_date)

while current <= end:
    filepath = os.path.join(stocks_dir, f"{current.isoformat()}.csv.gz")
    if os.path.exists(filepath):
        with gzip.open(filepath, "rt") as f:
            header = f.readline().strip().split(",")
            idx = {col: i for i, col in enumerate(header)}
            
            for line in f:
                parts = line.strip().split(",")
                if parts[idx["ticker"]] == "SPY":
                    # Get close price (last bar of day)
                    spy_prices[current] = float(parts[idx["close"]])
    current += timedelta(days=1)

print(f"SPY price data: {len(spy_prices)} days")

# Get first and last SPY prices
spy_dates = sorted(spy_prices.keys())
if spy_dates:
    spy_start_price = spy_prices[spy_dates[0]]
    spy_end_price = spy_prices[spy_dates[-1]]
    spy_return = (spy_end_price - spy_start_price) / spy_start_price * 100
    print(f"\nSPY: ${spy_start_price:.2f} -> ${spy_end_price:.2f}")
    print(f"SPY Buy & Hold Return: {spy_return:+.2f}%")

# =============================================================================
# Calculate strategy returns
# =============================================================================
print("\n" + "="*70)
print("STRATEGY PERFORMANCE")
print("="*70)

# Group signals by date
by_date = defaultdict(list)
for s in valid:
    by_date[s['detection_time'][:10]].append(s)

# Calculate daily returns (equal weight all signals that day)
daily_returns = []
for d in sorted(by_date.keys()):
    day_signals = by_date[d]
    day_returns = [s['pct_to_close'] for s in day_signals]
    avg_return = sum(day_returns) / len(day_returns)
    daily_returns.append({
        'date': d,
        'signals': len(day_signals),
        'return': avg_return
    })

# Summary stats
all_returns = [s['pct_to_close'] for s in valid]
total_return_simple = sum(all_returns)  # Sum of all individual returns (not compounded)
avg_per_trade = sum(all_returns) / len(all_returns)
win_rate = len([r for r in all_returns if r > 0]) / len(all_returns) * 100
trading_days = len(daily_returns)

print(f"\nTrading days with signals: {trading_days}")
print(f"Total trades: {len(valid)}")
print(f"Avg trades/day: {len(valid)/trading_days:.1f}")
print(f"\nPer-trade stats:")
print(f"  Win rate: {win_rate:.1f}%")
print(f"  Avg return: {avg_per_trade:+.2f}%")
print(f"  Sum of returns: {total_return_simple:+.1f}%")

# Compounded return (assume equal allocation per signal per day)
# Start with $10,000
initial_capital = 10000
capital = initial_capital

for day in daily_returns:
    # Equal weight all signals that day
    day_return_pct = day['return'] / 100
    capital *= (1 + day_return_pct)

strategy_compounded_return = (capital - initial_capital) / initial_capital * 100

print(f"\nCompounded return (reinvesting):")
print(f"  ${initial_capital:,} -> ${capital:,.0f}")
print(f"  Return: {strategy_compounded_return:+.2f}%")

# =============================================================================
# Side by side comparison
# =============================================================================
print("\n" + "="*70)
print("HEAD-TO-HEAD COMPARISON")
print("="*70)

print(f"\n{'Metric':<30} {'Strategy':<15} {'SPY B&H':<15}")
print("-"*60)
print(f"{'Period':<30} {start_date} to {end_date}")
print(f"{'Total Return (compounded)':<30} {strategy_compounded_return:+.2f}%{'':<8} {spy_return:+.2f}%")
print(f"{'Avg Daily Return':<30} {sum(d['return'] for d in daily_returns)/len(daily_returns):+.3f}%")

# Annualized
days_in_period = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
annualized_strategy = strategy_compounded_return * (365 / days_in_period)
annualized_spy = spy_return * (365 / days_in_period)
print(f"{'Annualized Return':<30} {annualized_strategy:+.1f}%{'':<9} {annualized_spy:+.1f}%")

# Max drawdown (simple approximation)
peak = initial_capital
max_dd = 0
capital = initial_capital
for day in daily_returns:
    capital *= (1 + day['return']/100)
    if capital > peak:
        peak = capital
    dd = (peak - capital) / peak * 100
    if dd > max_dd:
        max_dd = dd

print(f"{'Max Drawdown':<30} {max_dd:.1f}%")

# =============================================================================
# Monthly breakdown
# =============================================================================
print("\n" + "="*70)
print("MONTHLY PERFORMANCE")
print("="*70)

by_month = defaultdict(list)
for day in daily_returns:
    month = day['date'][:7]
    by_month[month].append(day['return'])

print(f"\n{'Month':<12} {'Trades':<10} {'Avg Ret':<12} {'Win Days':<12}")
print("-"*50)
for month in sorted(by_month.keys()):
    rets = by_month[month]
    avg = sum(rets)/len(rets)
    wins = len([r for r in rets if r > 0])
    print(f"{month:<12} {len(rets):<10} {avg:+.2f}%{'':<6} {wins}/{len(rets)}")

# =============================================================================
# Risk-adjusted metrics
# =============================================================================
print("\n" + "="*70)
print("RISK METRICS")
print("="*70)

import math

returns = [d['return'] for d in daily_returns]
avg_return = sum(returns) / len(returns)
variance = sum((r - avg_return)**2 for r in returns) / len(returns)
std_dev = math.sqrt(variance)

# Sharpe ratio (assuming 0% risk-free rate for simplicity)
sharpe = (avg_return / std_dev) * math.sqrt(252) if std_dev > 0 else 0

print(f"\nDaily return std dev: {std_dev:.2f}%")
print(f"Sharpe Ratio (annualized): {sharpe:.2f}")

# Win/Loss ratio
winners = [r for r in all_returns if r > 0]
losers = [r for r in all_returns if r < 0]
avg_win = sum(winners)/len(winners) if winners else 0
avg_loss = abs(sum(losers)/len(losers)) if losers else 1
profit_factor = (sum(winners) / abs(sum(losers))) if losers else float('inf')

print(f"Avg winner: +{avg_win:.2f}%")
print(f"Avg loser: -{avg_loss:.2f}%")
print(f"Win/Loss ratio: {avg_win/avg_loss:.2f}")
print(f"Profit factor: {profit_factor:.2f}")
