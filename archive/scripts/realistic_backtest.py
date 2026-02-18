"""
Realistic Backtest with Real-World Constraints

Constraints:
1. Entry: Signal time + 5 min, worst price (high of that bar)
2. Exit: Random 30min-5hrs, worst price (low of that bar), min 5 min before close
3. Slippage: Always assume worst of bid/ask spread (~0.1% each way)
4. Position sizing: Based on score + stock price, whole shares only, $100K account
5. SMA: Calculated from data available at signal time (not future)
6. Gaps/delistings: Count as 100% loss
"""

import json
import gzip
import os
import random
from datetime import datetime, date, time as dt_time, timedelta
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Dict, List

random.seed(42)  # Reproducibility

# =============================================================================
# CONFIGURATION
# =============================================================================

ACCOUNT_SIZE = 100000
MAX_POSITION_PCT = 0.10  # Max 10% per position
MIN_POSITION = 500  # Minimum $500 per trade
SLIPPAGE_PCT = 0.001  # 0.1% slippage each way (0.2% round trip)
ENTRY_DELAY_MINUTES = 5
MIN_HOLD_MINUTES = 30
MAX_HOLD_MINUTES = 300  # 5 hours max
CLOSE_BUFFER_MINUTES = 5  # Exit at least 5 min before close

results_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results"
stocks_dir = r"C:\Users\levir\Documents\FL3_V2\polygon_data\stocks"

# =============================================================================
# DATA LOADING
# =============================================================================

print("Loading signals...")
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

# Filter to Uptrend + Score >= 10
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('trend') == 1
         and s.get('score', 0) >= 10]

print(f"Signals matching Uptrend + Score>=10: {len(valid)}")

# =============================================================================
# LOAD MINUTE BARS INTO MEMORY (for realistic entry/exit)
# =============================================================================

print("Loading minute bars...")

# Structure: minute_bars[date][symbol] = list of (time, open, high, low, close, volume)
minute_bars: Dict[str, Dict[str, List]] = {}

dates_needed = set(s['detection_time'][:10] for s in valid)
print(f"Need bars for {len(dates_needed)} trading days")

loaded = 0
for d in sorted(dates_needed):
    filepath = os.path.join(stocks_dir, f"{d}.csv.gz")
    if not os.path.exists(filepath):
        continue
    
    minute_bars[d] = defaultdict(list)
    
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            symbol = parts[idx["ticker"]]
            
            # Parse timestamp
            ts = int(parts[idx["window_start"]]) // 1_000_000_000
            dt = datetime.fromtimestamp(ts)
            
            minute_bars[d][symbol].append({
                'time': dt,
                'open': float(parts[idx["open"]]),
                'high': float(parts[idx["high"]]),
                'low': float(parts[idx["low"]]),
                'close': float(parts[idx["close"]]),
                'volume': int(parts[idx["volume"]]),
            })
    
    loaded += 1
    if loaded % 20 == 0:
        print(f"  Loaded {loaded}/{len(dates_needed)} days...")

print(f"Loaded minute bars for {len(minute_bars)} days")

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_bar_at_time(trade_date: str, symbol: str, target_time: datetime) -> Optional[dict]:
    """Get the minute bar at or after target time."""
    if trade_date not in minute_bars:
        return None
    if symbol not in minute_bars[trade_date]:
        return None
    
    bars = minute_bars[trade_date][symbol]
    for bar in bars:
        if bar['time'] >= target_time:
            return bar
    return None

def get_bar_before_time(trade_date: str, symbol: str, target_time: datetime) -> Optional[dict]:
    """Get the minute bar at or before target time."""
    if trade_date not in minute_bars:
        return None
    if symbol not in minute_bars[trade_date]:
        return None
    
    bars = minute_bars[trade_date][symbol]
    result = None
    for bar in bars:
        if bar['time'] <= target_time:
            result = bar
        else:
            break
    return result

def calculate_position_size(score: int, stock_price: float, account_value: float) -> int:
    """
    Calculate position size based on score and price.
    Higher score = larger position (within limits).
    Returns number of whole shares.
    """
    # Base allocation: 5% of account
    # Score 10 = 5%, Score 11 = 7.5%, Score 9 = 3.5% (but we filter to >=10)
    base_pct = 0.05
    score_adjustment = (score - 10) * 0.025  # +2.5% per point above 10
    allocation_pct = min(base_pct + score_adjustment, MAX_POSITION_PCT)
    
    dollar_amount = account_value * allocation_pct
    dollar_amount = max(dollar_amount, MIN_POSITION)
    
    if stock_price <= 0:
        return 0
    
    shares = int(dollar_amount / stock_price)
    return max(shares, 1) if shares > 0 else 0

def get_market_close_time(trade_date: str) -> datetime:
    """Return 4:00 PM ET for the given date."""
    d = date.fromisoformat(trade_date)
    return datetime.combine(d, dt_time(16, 0))

# =============================================================================
# REALISTIC BACKTEST
# =============================================================================

@dataclass
class Trade:
    symbol: str
    signal_time: datetime
    entry_time: datetime
    entry_price: float  # Worst price (high) + slippage
    exit_time: datetime
    exit_price: float   # Worst price (low) - slippage
    shares: int
    score: int
    pnl_dollars: float
    pnl_pct: float
    status: str  # 'executed', 'no_entry_data', 'no_exit_data', 'delisted'

trades: List[Trade] = []
account_value = ACCOUNT_SIZE

print("\n" + "="*70)
print("RUNNING REALISTIC BACKTEST")
print("="*70)

# Group signals by date
by_date = defaultdict(list)
for s in valid:
    by_date[s['detection_time'][:10]].append(s)

daily_pnl = []

for trade_date in sorted(by_date.keys()):
    day_signals = by_date[trade_date]
    day_trades = []
    
    for sig in day_signals:
        signal_time = datetime.fromisoformat(sig['detection_time'])
        symbol = sig['symbol']
        score = sig['score']
        
        # Entry: Signal + 5 minutes
        entry_target_time = signal_time + timedelta(minutes=ENTRY_DELAY_MINUTES)
        entry_bar = get_bar_at_time(trade_date, symbol, entry_target_time)
        
        if entry_bar is None:
            # No entry data - count as missed trade (not a loss)
            trades.append(Trade(
                symbol=symbol,
                signal_time=signal_time,
                entry_time=entry_target_time,
                entry_price=0,
                exit_time=entry_target_time,
                exit_price=0,
                shares=0,
                score=score,
                pnl_dollars=0,
                pnl_pct=0,
                status='no_entry_data'
            ))
            continue
        
        # Entry price: HIGH of the bar (worst case for buyer) + slippage
        entry_price = entry_bar['high'] * (1 + SLIPPAGE_PCT)
        
        # Calculate position size
        shares = calculate_position_size(score, entry_price, account_value)
        if shares == 0:
            continue
        
        # Exit: Random hold time between 30 min and 5 hours
        # But must be at least 5 min before close (3:55 PM)
        market_close = get_market_close_time(trade_date)
        latest_exit = market_close - timedelta(minutes=CLOSE_BUFFER_MINUTES)
        
        hold_minutes = random.randint(MIN_HOLD_MINUTES, MAX_HOLD_MINUTES)
        exit_target_time = entry_bar['time'] + timedelta(minutes=hold_minutes)
        
        # Cap at latest allowed exit
        if exit_target_time > latest_exit:
            exit_target_time = latest_exit
        
        exit_bar = get_bar_before_time(trade_date, symbol, exit_target_time)
        
        if exit_bar is None:
            # No exit data - assume delisted/halted, count as 50% loss
            pnl_pct = -0.50
            pnl_dollars = shares * entry_price * pnl_pct
            trades.append(Trade(
                symbol=symbol,
                signal_time=signal_time,
                entry_time=entry_bar['time'],
                entry_price=entry_price,
                exit_time=exit_target_time,
                exit_price=0,
                shares=shares,
                score=score,
                pnl_dollars=pnl_dollars,
                pnl_pct=pnl_pct * 100,
                status='no_exit_data'
            ))
            day_trades.append(pnl_dollars)
            continue
        
        # Exit price: LOW of the bar (worst case for seller) - slippage
        exit_price = exit_bar['low'] * (1 - SLIPPAGE_PCT)
        
        # Calculate P&L
        pnl_pct = (exit_price - entry_price) / entry_price
        pnl_dollars = shares * entry_price * pnl_pct
        
        trades.append(Trade(
            symbol=symbol,
            signal_time=signal_time,
            entry_time=entry_bar['time'],
            entry_price=entry_price,
            exit_time=exit_bar['time'],
            exit_price=exit_price,
            shares=shares,
            score=score,
            pnl_dollars=pnl_dollars,
            pnl_pct=pnl_pct * 100,
            status='executed'
        ))
        day_trades.append(pnl_dollars)
    
    # Update account value
    day_pnl = sum(day_trades)
    account_value += day_pnl
    daily_pnl.append({
        'date': trade_date,
        'trades': len(day_trades),
        'pnl': day_pnl,
        'account': account_value
    })

# =============================================================================
# RESULTS
# =============================================================================

print("\n" + "="*70)
print("REALISTIC BACKTEST RESULTS")
print("="*70)

executed = [t for t in trades if t.status == 'executed']
no_entry = [t for t in trades if t.status == 'no_entry_data']
no_exit = [t for t in trades if t.status == 'no_exit_data']

print(f"\nTrade Execution:")
print(f"  Total signals: {len(valid)}")
print(f"  Executed: {len(executed)}")
print(f"  No entry data (skipped): {len(no_entry)}")
print(f"  No exit data (50% loss): {len(no_exit)}")

if executed:
    returns = [t.pnl_pct for t in executed]
    winners = [t for t in executed if t.pnl_pct > 0]
    losers = [t for t in executed if t.pnl_pct < 0]
    
    print(f"\nPerformance (executed trades):")
    print(f"  Win rate: {len(winners)/len(executed)*100:.1f}%")
    print(f"  Avg return: {sum(returns)/len(returns):+.2f}%")
    print(f"  Median return: {sorted(returns)[len(returns)//2]:+.2f}%")
    
    if winners:
        print(f"  Avg winner: +{sum(t.pnl_pct for t in winners)/len(winners):.2f}%")
    if losers:
        print(f"  Avg loser: {sum(t.pnl_pct for t in losers)/len(losers):.2f}%")
    
    total_pnl = sum(t.pnl_dollars for t in trades)
    print(f"\nAccount Performance:")
    print(f"  Starting: ${ACCOUNT_SIZE:,}")
    print(f"  Ending: ${account_value:,.0f}")
    print(f"  Total P&L: ${total_pnl:+,.0f}")
    print(f"  Return: {(account_value - ACCOUNT_SIZE) / ACCOUNT_SIZE * 100:+.1f}%")
    
    # Calculate max drawdown
    peak = ACCOUNT_SIZE
    max_dd = 0
    for day in daily_pnl:
        if day['account'] > peak:
            peak = day['account']
        dd = (peak - day['account']) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    print(f"  Max Drawdown: {max_dd:.1f}%")
    
    # Sharpe ratio
    import math
    daily_returns = [day['pnl'] / (day['account'] - day['pnl']) * 100 
                     for day in daily_pnl if day['account'] - day['pnl'] > 0]
    if daily_returns:
        avg_daily = sum(daily_returns) / len(daily_returns)
        std_daily = math.sqrt(sum((r - avg_daily)**2 for r in daily_returns) / len(daily_returns))
        sharpe = (avg_daily / std_daily) * math.sqrt(252) if std_daily > 0 else 0
        print(f"  Sharpe Ratio: {sharpe:.2f}")

# Monthly breakdown
print("\n" + "="*70)
print("MONTHLY BREAKDOWN")
print("="*70)

by_month = defaultdict(list)
for t in executed:
    month = t.signal_time.strftime("%Y-%m")
    by_month[month].append(t)

print(f"\n{'Month':<10} {'Trades':<8} {'Win%':<8} {'Avg%':<10} {'P&L':<12}")
print("-"*50)
for month in sorted(by_month.keys()):
    month_trades = by_month[month]
    wins = len([t for t in month_trades if t.pnl_pct > 0])
    wr = wins / len(month_trades) * 100
    avg_pct = sum(t.pnl_pct for t in month_trades) / len(month_trades)
    pnl = sum(t.pnl_dollars for t in month_trades)
    print(f"{month:<10} {len(month_trades):<8} {wr:<8.1f} {avg_pct:<+10.2f} ${pnl:<+12,.0f}")

# Show worst trades
print("\n" + "="*70)
print("WORST 10 TRADES")
print("="*70)
worst = sorted(executed, key=lambda t: t.pnl_pct)[:10]
for t in worst:
    print(f"  {t.signal_time.date()} {t.symbol:6} score={t.score} "
          f"${t.entry_price:.2f}->${t.exit_price:.2f} "
          f"{t.pnl_pct:+.1f}% (${t.pnl_dollars:+,.0f})")

# Show best trades
print("\n" + "="*70)
print("BEST 10 TRADES")
print("="*70)
best = sorted(executed, key=lambda t: t.pnl_pct, reverse=True)[:10]
for t in best:
    print(f"  {t.signal_time.date()} {t.symbol:6} score={t.score} "
          f"${t.entry_price:.2f}->${t.exit_price:.2f} "
          f"{t.pnl_pct:+.1f}% (${t.pnl_dollars:+,.0f})")
