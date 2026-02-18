"""
Quick TA Validation: Sample 1,000 signals, calculate TA, check correlation with outcomes.

QUESTION: Do RSI/MACD patterns at signal time predict outcomes?
"""

import json
import gzip
import os
from datetime import datetime, timedelta, date
from collections import defaultdict
import random

# Paths
STOCKS_DIR = r"C:\Users\levir\Documents\FL3_V2\polygon_data\stocks"
RESULTS_FILE = r"C:\Users\levir\Documents\FL3_V2\polygon_data\backtest_results\e2e_backtest_with_outcomes.json"

# =============================================================================
# TA CALCULATIONS
# =============================================================================

def calc_rsi(closes, period=14):
    """Calculate RSI from list of closes."""
    if len(closes) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    if len(gains) < period:
        return None
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss == 0:
        return 100
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_macd(closes, fast=12, slow=26, signal=9):
    """Calculate MACD, Signal, Histogram."""
    if len(closes) < slow + signal:
        return None, None, None
    
    def ema(data, period):
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for price in data[period:]:
            ema_val = (price - ema_val) * multiplier + ema_val
        return ema_val
    
    # Need enough data for slow EMA + signal smoothing
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    
    if ema_fast is None or ema_slow is None:
        return None, None, None
    
    macd_line = ema_fast - ema_slow
    
    # For signal line, we'd need MACD history - simplify for now
    # Just return MACD line and whether it's positive
    return macd_line, None, macd_line  # histogram = macd for simplicity


def calc_vwap(bars):
    """Calculate VWAP from list of (close, volume) tuples."""
    if not bars:
        return None
    
    total_pv = sum(price * vol for price, vol in bars)
    total_vol = sum(vol for _, vol in bars)
    
    return total_pv / total_vol if total_vol > 0 else None


def calc_sma(closes, period=20):
    """Simple moving average."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


# =============================================================================
# LOAD STOCK BARS FOR A DAY
# =============================================================================

def load_stock_bars(trade_date, symbol):
    """Load minute bars for a symbol on a specific date."""
    filepath = os.path.join(STOCKS_DIR, f"{trade_date}.csv.gz")
    if not os.path.exists(filepath):
        return []
    
    bars = []
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            if parts[idx["ticker"]] != symbol:
                continue
            
            # Parse timestamp
            ts_ns = int(parts[idx["window_start"]])
            ts = datetime.fromtimestamp(ts_ns / 1e9)
            
            bars.append({
                "ts": ts,
                "o": float(parts[idx["open"]]),
                "h": float(parts[idx["high"]]),
                "l": float(parts[idx["low"]]),
                "c": float(parts[idx["close"]]),
                "v": int(parts[idx["volume"]]),
            })
    
    return sorted(bars, key=lambda x: x["ts"])


def get_ta_at_time(bars, signal_time):
    """Calculate TA indicators at a specific time."""
    # Filter bars up to signal time
    relevant = [b for b in bars if b["ts"] <= signal_time]
    
    if len(relevant) < 30:  # Need enough history
        return None
    
    closes = [b["c"] for b in relevant]
    
    # Calculate indicators
    rsi = calc_rsi(closes[-50:], 14)  # Use last 50 bars for RSI-14
    macd, _, histogram = calc_macd(closes[-50:])
    sma_20 = calc_sma(closes, 20)
    
    # VWAP (from market open)
    market_open = signal_time.replace(hour=9, minute=30, second=0)
    intraday = [(b["c"], b["v"]) for b in relevant if b["ts"] >= market_open]
    vwap = calc_vwap(intraday) if intraday else None
    
    current_price = closes[-1]
    
    # Price vs levels
    price_vs_sma = (current_price / sma_20 - 1) * 100 if sma_20 else None
    price_vs_vwap = (current_price / vwap - 1) * 100 if vwap else None
    
    # 20-bar high/low
    recent_high = max(closes[-20:])
    recent_low = min(closes[-20:])
    dist_from_high = (current_price / recent_high - 1) * 100
    dist_from_low = (current_price / recent_low - 1) * 100
    
    return {
        "rsi": rsi,
        "macd": macd,
        "macd_positive": macd > 0 if macd else None,
        "sma_20": sma_20,
        "vwap": vwap,
        "price": current_price,
        "price_vs_sma": price_vs_sma,
        "price_vs_vwap": price_vs_vwap,
        "dist_from_high": dist_from_high,
        "dist_from_low": dist_from_low,
    }


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

print("="*80)
print("TA VALIDATION: 1,000 Signal Sample")
print("="*80)

# Load signals
with open(RESULTS_FILE) as f:
    data = json.load(f)

signals = data['signals']

# Filter to valid signals with outcomes
valid = [s for s in signals 
         if s.get('pct_to_close') is not None 
         and not s.get('filtered_out')
         and s.get('baseline_source') == 'history'
         and not s.get('is_premarket')]  # Regular hours only (have VWAP)

print(f"Valid signals (regular hours): {len(valid):,}")

# Sample 1,000 random signals
random.seed(42)
sample = random.sample(valid, min(1000, len(valid)))
print(f"Sample size: {len(sample)}")

# Calculate TA for each
print("\nCalculating TA indicators...")
enriched = []
bars_cache = {}  # Cache bars by (date, symbol)

for i, sig in enumerate(sample):
    if i % 100 == 0:
        print(f"  Processing {i}/{len(sample)}...")
    
    sig_time = datetime.fromisoformat(sig['detection_time'])
    trade_date = sig_time.date().isoformat()
    symbol = sig['symbol']
    
    # Load bars (with caching)
    cache_key = (trade_date, symbol)
    if cache_key not in bars_cache:
        bars_cache[cache_key] = load_stock_bars(trade_date, symbol)
    
    bars = bars_cache[cache_key]
    
    if not bars:
        continue
    
    ta = get_ta_at_time(bars, sig_time)
    if ta is None:
        continue
    
    enriched.append({
        **sig,
        **ta
    })

print(f"\nEnriched signals: {len(enriched)}")

# =============================================================================
# ANALYSIS
# =============================================================================

print("\n" + "="*80)
print("ANALYSIS: RSI AT SIGNAL TIME")
print("="*80)

def analyze_bucket(subset, name):
    if len(subset) < 20:
        print(f"\n{name}: Too few signals ({len(subset)})")
        return
    
    closes = [s['pct_to_close'] for s in subset]
    avg = sum(closes) / len(closes)
    win_rate = len([c for c in closes if c > 0]) / len(closes) * 100
    big_win = len([c for c in closes if c > 5]) / len(closes) * 100
    
    print(f"\n{name}:")
    print(f"  Count: {len(subset)}")
    print(f"  Win rate: {win_rate:.1f}%")
    print(f"  Avg return: {avg:+.2f}%")
    print(f"  Big winners: {big_win:.1f}%")

# RSI buckets
rsi_oversold = [s for s in enriched if s.get('rsi') and s['rsi'] < 30]
rsi_neutral_low = [s for s in enriched if s.get('rsi') and 30 <= s['rsi'] < 50]
rsi_neutral_high = [s for s in enriched if s.get('rsi') and 50 <= s['rsi'] < 70]
rsi_overbought = [s for s in enriched if s.get('rsi') and s['rsi'] >= 70]

analyze_bucket(rsi_oversold, "RSI < 30 (oversold)")
analyze_bucket(rsi_neutral_low, "RSI 30-50 (neutral low)")
analyze_bucket(rsi_neutral_high, "RSI 50-70 (neutral high)")
analyze_bucket(rsi_overbought, "RSI >= 70 (overbought)")

print("\n" + "="*80)
print("ANALYSIS: MACD AT SIGNAL TIME")
print("="*80)

macd_positive = [s for s in enriched if s.get('macd_positive') == True]
macd_negative = [s for s in enriched if s.get('macd_positive') == False]

analyze_bucket(macd_positive, "MACD > 0 (bullish momentum)")
analyze_bucket(macd_negative, "MACD < 0 (bearish momentum)")

print("\n" + "="*80)
print("ANALYSIS: PRICE VS VWAP")
print("="*80)

above_vwap = [s for s in enriched if s.get('price_vs_vwap') and s['price_vs_vwap'] > 0.5]
at_vwap = [s for s in enriched if s.get('price_vs_vwap') and -0.5 <= s['price_vs_vwap'] <= 0.5]
below_vwap = [s for s in enriched if s.get('price_vs_vwap') and s['price_vs_vwap'] < -0.5]

analyze_bucket(above_vwap, "Above VWAP (>0.5%)")
analyze_bucket(at_vwap, "At VWAP (+/- 0.5%)")
analyze_bucket(below_vwap, "Below VWAP (<-0.5%)")

print("\n" + "="*80)
print("ANALYSIS: PRICE VS 20 SMA (TREND)")
print("="*80)

uptrend = [s for s in enriched if s.get('price_vs_sma') and s['price_vs_sma'] > 1]
neutral_trend = [s for s in enriched if s.get('price_vs_sma') and -1 <= s['price_vs_sma'] <= 1]
downtrend = [s for s in enriched if s.get('price_vs_sma') and s['price_vs_sma'] < -1]

analyze_bucket(uptrend, "Uptrend (>1% above SMA)")
analyze_bucket(neutral_trend, "Neutral (+/- 1% of SMA)")
analyze_bucket(downtrend, "Downtrend (>1% below SMA)")

print("\n" + "="*80)
print("ANALYSIS: DISTANCE FROM RECENT LOW (SUPPORT)")
print("="*80)

near_low = [s for s in enriched if s.get('dist_from_low') is not None and s['dist_from_low'] < 2]
mid_range = [s for s in enriched if s.get('dist_from_low') is not None and 2 <= s['dist_from_low'] < 5]
extended = [s for s in enriched if s.get('dist_from_low') is not None and s['dist_from_low'] >= 5]

analyze_bucket(near_low, "Near 20-bar low (<2%)")
analyze_bucket(mid_range, "Mid-range (2-5% from low)")
analyze_bucket(extended, "Extended (>5% from low)")

print("\n" + "="*80)
print("COMBINED: BEST TA CONDITIONS")
print("="*80)

# Theory: RSI neutral/low + MACD positive + price near support = good entry
best_ta = [s for s in enriched 
           if s.get('rsi') and 30 <= s['rsi'] < 60
           and s.get('macd_positive') == True
           and s.get('dist_from_low') is not None and s['dist_from_low'] < 5]

analyze_bucket(best_ta, "RSI 30-60 + MACD positive + near support")

# Contrarian: RSI oversold + MACD negative (bounce setup)
bounce_setup = [s for s in enriched
                if s.get('rsi') and s['rsi'] < 40
                and s.get('price_vs_vwap') and s['price_vs_vwap'] < 0]

analyze_bucket(bounce_setup, "RSI < 40 + Below VWAP (bounce setup)")

# Momentum: RSI high + MACD positive + above VWAP
momentum_setup = [s for s in enriched
                  if s.get('rsi') and s['rsi'] > 50
                  and s.get('macd_positive') == True
                  and s.get('price_vs_vwap') and s['price_vs_vwap'] > 0]

analyze_bucket(momentum_setup, "RSI > 50 + MACD positive + Above VWAP (momentum)")

# Add flow direction
print("\n" + "="*80)
print("COMBINED: TA + BULLISH FLOW")
print("="*80)

bullish_momentum = [s for s in momentum_setup if s.get('call_pct', 0) > 0.7]
analyze_bucket(bullish_momentum, "Momentum setup + Bullish flow (>70% calls)")

bullish_bounce = [s for s in bounce_setup if s.get('call_pct', 0) > 0.7]
analyze_bucket(bullish_bounce, "Bounce setup + Bullish flow (>70% calls)")

print("\n" + "="*80)
print("VERDICT")
print("="*80)
