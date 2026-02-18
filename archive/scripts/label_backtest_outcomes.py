"""
Outcome Labeling for Backtest Signals - Using Polygon Stocks API

Fetch minute-level price data to measure:
1. Price at detection time
2. Price at close
3. Max gain/loss after signal
"""

import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
import requests
import time

# Polygon API
POLYGON_API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"

DATA_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"


def get_minute_bars(ticker, date):
    """Fetch minute bars for a specific ticker and date."""
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}"
    params = {
        'adjusted': 'true',
        'sort': 'asc',
        'limit': 50000,
        'apiKey': POLYGON_API_KEY
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get('status') == 'OK' and 'results' in data:
            return data['results']
        elif data.get('status') == 'ERROR':
            print(f"  API Error for {ticker}: {data.get('error', 'Unknown')}")
    except Exception as e:
        print(f"  Request error for {ticker}: {e}")
    
    return []


def get_daily_bar(ticker, date):
    """Fetch daily bar for a specific ticker and date."""
    url = f"https://api.polygon.io/v1/open-close/{ticker}/{date}"
    params = {
        'adjusted': 'true',
        'apiKey': POLYGON_API_KEY
    }
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get('status') == 'OK':
            return data
    except Exception as e:
        pass
    
    return None


def find_price_at_time(minute_bars, target_time):
    """Find the price closest to the target time."""
    if not minute_bars:
        return None
    
    target_ts = target_time.timestamp() * 1000  # Convert to milliseconds
    
    # Find closest bar at or before target time
    closest_bar = None
    for bar in minute_bars:
        if bar['t'] <= target_ts:
            closest_bar = bar
        else:
            break
    
    if closest_bar:
        return closest_bar['c']  # Return close of that minute
    
    # If no bar before target, return first bar's open
    return minute_bars[0]['o'] if minute_bars else None


def calculate_outcomes(signals):
    """
    For each signal, calculate price outcomes using minute bars.
    """
    print("\n" + "="*70)
    print("CALCULATING PRICE OUTCOMES (Polygon Stocks API)")
    print("="*70)
    
    # Group signals by (underlying, date) to batch API calls
    by_ticker_date = defaultdict(list)
    for s in signals:
        key = (s['underlying'], s['date'])
        by_ticker_date[key].append(s)
    
    print(f"Unique (ticker, date) combinations: {len(by_ticker_date)}")
    
    enriched = []
    success = 0
    errors = 0
    
    for i, ((ticker, date), ticker_signals) in enumerate(by_ticker_date.items()):
        if i % 100 == 0:
            print(f"  Processing {i}/{len(by_ticker_date)} ({success} success, {errors} errors)...")
        
        # Fetch minute bars for this ticker/date
        minute_bars = get_minute_bars(ticker, date)
        
        if not minute_bars:
            errors += 1
            continue
        
        success += 1
        
        # Calculate OHLC from minute bars
        opens = [b['o'] for b in minute_bars]
        highs = [b['h'] for b in minute_bars]
        lows = [b['l'] for b in minute_bars]
        closes = [b['c'] for b in minute_bars]
        
        day_open = opens[0] if opens else None
        day_high = max(highs) if highs else None
        day_low = min(lows) if lows else None
        day_close = closes[-1] if closes else None
        
        # Find first regular session bar (9:30 AM)
        market_open_price = None
        for bar in minute_bars:
            bar_time = datetime.fromtimestamp(bar['t'] / 1000)
            if bar_time.hour == 9 and bar_time.minute >= 30:
                market_open_price = bar['o']
                break
            elif bar_time.hour > 9:
                market_open_price = bar['o']
                break
        
        for sig in ticker_signals:
            # Parse detection time
            det_time_str = sig['detection_time']
            det_time = datetime.fromisoformat(det_time_str)
            
            # Find price at detection
            price_at_detection = find_price_at_time(minute_bars, det_time)
            
            # Find max price AFTER detection
            max_after = None
            min_after = None
            close_after = None
            
            det_ts = det_time.timestamp() * 1000
            bars_after = [b for b in minute_bars if b['t'] >= det_ts]
            
            if bars_after:
                max_after = max(b['h'] for b in bars_after)
                min_after = min(b['l'] for b in bars_after)
                close_after = bars_after[-1]['c']
            
            # Calculate returns
            if price_at_detection and price_at_detection > 0:
                pct_to_close = ((day_close - price_at_detection) / price_at_detection * 100) if day_close else None
                pct_max_gain = ((max_after - price_at_detection) / price_at_detection * 100) if max_after else None
                pct_max_loss = ((min_after - price_at_detection) / price_at_detection * 100) if min_after else None
            else:
                pct_to_close = None
                pct_max_gain = None
                pct_max_loss = None
            
            # Check if detection was pre-market
            is_premarket = det_time.hour < 9 or (det_time.hour == 9 and det_time.minute < 30)
            
            # Calculate gap (if pre-market signal)
            gap_pct = None
            if is_premarket and price_at_detection and market_open_price:
                gap_pct = ((market_open_price - price_at_detection) / price_at_detection * 100)
            
            enriched.append({
                **sig,
                'price_at_detection': round(price_at_detection, 2) if price_at_detection else None,
                'market_open_price': round(market_open_price, 2) if market_open_price else None,
                'day_close': round(day_close, 2) if day_close else None,
                'day_high': round(day_high, 2) if day_high else None,
                'day_low': round(day_low, 2) if day_low else None,
                'pct_to_close': round(pct_to_close, 2) if pct_to_close else None,
                'pct_max_gain': round(pct_max_gain, 2) if pct_max_gain else None,
                'pct_max_loss': round(pct_max_loss, 2) if pct_max_loss else None,
                'gap_pct': round(gap_pct, 2) if gap_pct else None,
                'is_premarket': is_premarket,
            })
        
        # Small delay to be nice to API (unlimited calls but still good practice)
        time.sleep(0.05)
    
    print(f"\nCompleted: {success} success, {errors} errors")
    print(f"Enriched signals: {len(enriched)}")
    
    return enriched


def analyze_outcomes(signals):
    """Analyze price outcomes of signals."""
    print("\n" + "="*70)
    print("OUTCOME ANALYSIS")
    print("="*70)
    
    # Filter to signals with outcomes
    with_outcomes = [s for s in signals if s.get('pct_to_close') is not None]
    print(f"Signals with price data: {len(with_outcomes)}")
    
    if not with_outcomes:
        print("No outcome data available!")
        return {}
    
    # Overall stats
    closes = [s['pct_to_close'] for s in with_outcomes]
    gains = [s['pct_max_gain'] for s in with_outcomes if s.get('pct_max_gain')]
    
    print(f"\n--- Overall Performance (from detection price) ---")
    print(f"Average % to close: {sum(closes)/len(closes):.2f}%")
    print(f"Median % to close: {sorted(closes)[len(closes)//2]:.2f}%")
    if gains:
        print(f"Average max gain after signal: {sum(gains)/len(gains):.2f}%")
    
    # Win rate
    winners = len([s for s in with_outcomes if s['pct_to_close'] > 0])
    print(f"Win rate (close > detection): {winners/len(with_outcomes)*100:.1f}%")
    
    big_winners = len([s for s in with_outcomes if s['pct_to_close'] > 5])
    print(f"Big winners (>5%): {big_winners} ({big_winners/len(with_outcomes)*100:.1f}%)")
    
    big_losers = len([s for s in with_outcomes if s['pct_to_close'] < -5])
    print(f"Big losers (<-5%): {big_losers} ({big_losers/len(with_outcomes)*100:.1f}%)")
    
    # Pre-market signals
    print(f"\n--- Pre-Market Signals ---")
    premarket = [s for s in with_outcomes if s.get('is_premarket')]
    print(f"Count: {len(premarket)}")
    
    if premarket:
        pm_closes = [s['pct_to_close'] for s in premarket]
        print(f"Avg % to close: {sum(pm_closes)/len(pm_closes):.2f}%")
        print(f"Win rate: {len([s for s in premarket if s['pct_to_close'] > 0])/len(premarket)*100:.1f}%")
        
        # Gap analysis
        with_gap = [s for s in premarket if s.get('gap_pct') is not None]
        if with_gap:
            gaps = [s['gap_pct'] for s in with_gap]
            print(f"Avg gap at open: {sum(gaps)/len(gaps):.2f}%")
    
    # By ratio buckets
    print(f"\n--- Performance by Ratio Bucket ---")
    buckets = [
        ('2-5x', 2, 5),
        ('5-10x', 5, 10),
        ('10-50x', 10, 50),
        ('50-100x', 50, 100),
        ('100x+', 100, float('inf')),
    ]
    
    for name, low, high in buckets:
        bucket = [s for s in with_outcomes if low <= s['ratio'] < high]
        if bucket:
            avg_close = sum(s['pct_to_close'] for s in bucket) / len(bucket)
            win_rate = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
            print(f"  {name:10}: n={len(bucket):4}, avg={avg_close:+6.2f}%, win={win_rate:.1f}%")
    
    # Top performers
    print(f"\n--- Top 25 Performers (by % to close) ---")
    print(f"{'Date':12} {'Ticker':8} {'Ratio':>8} {'Det':>8} {'DetPx':>8} {'%Close':>8} {'%MaxGain':>10}")
    print("-"*75)
    
    sorted_by_close = sorted(with_outcomes, key=lambda x: x['pct_to_close'], reverse=True)
    for s in sorted_by_close[:25]:
        det_time = s['detection_time'][11:16] if len(s['detection_time']) > 11 else 'N/A'
        det_px = f"${s['price_at_detection']:.2f}" if s.get('price_at_detection') else 'N/A'
        max_gain = f"{s['pct_max_gain']:.1f}%" if s.get('pct_max_gain') else 'N/A'
        print(f"{s['date']:12} {s['underlying']:8} {s['ratio']:>7.1f}x {det_time:>8} {det_px:>8} "
              f"{s['pct_to_close']:>+7.1f}% {max_gain:>10}")
    
    # Worst performers
    print(f"\n--- Bottom 25 Performers ---")
    print(f"{'Date':12} {'Ticker':8} {'Ratio':>8} {'Det':>8} {'DetPx':>8} {'%Close':>8} {'%MaxLoss':>10}")
    print("-"*75)
    
    for s in sorted_by_close[-25:]:
        det_time = s['detection_time'][11:16] if len(s['detection_time']) > 11 else 'N/A'
        det_px = f"${s['price_at_detection']:.2f}" if s.get('price_at_detection') else 'N/A'
        max_loss = f"{s['pct_max_loss']:.1f}%" if s.get('pct_max_loss') else 'N/A'
        print(f"{s['date']:12} {s['underlying']:8} {s['ratio']:>7.1f}x {det_time:>8} {det_px:>8} "
              f"{s['pct_to_close']:>+7.1f}% {max_loss:>10}")
    
    # AAOI specifically
    print(f"\n--- AAOI Signal (if present) ---")
    aaoi = [s for s in with_outcomes if s['underlying'] == 'AAOI']
    for s in aaoi:
        print(f"  Date: {s['date']}")
        print(f"  Detection: {s['detection_time'][11:16]} @ ${s.get('price_at_detection', 'N/A')}")
        print(f"  Ratio: {s['ratio']:.1f}x")
        print(f"  % to close: {s['pct_to_close']:+.2f}%")
        print(f"  Max gain: {s.get('pct_max_gain', 'N/A')}")
        print(f"  Max loss: {s.get('pct_max_loss', 'N/A')}")
    
    return {
        'total_with_outcomes': len(with_outcomes),
        'avg_pct_to_close': sum(closes)/len(closes),
        'win_rate': winners/len(with_outcomes),
        'big_winner_rate': big_winners/len(with_outcomes),
    }


def main():
    # Load signals from multi-day backtest
    input_file = os.path.join(DATA_DIR, 'multi_day_backtest.json')
    
    print("Loading backtest results...")
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    signals = data['signals']
    print(f"Loaded {len(signals)} signals")
    
    # Calculate outcomes
    enriched = calculate_outcomes(signals)
    
    # Analyze
    summary = analyze_outcomes(enriched)
    
    # Save enriched results
    output_file = os.path.join(DATA_DIR, 'backtest_with_outcomes.json')
    with open(output_file, 'w') as f:
        json.dump({
            'signals': enriched,
            'summary': summary,
        }, f, indent=2)
    
    print(f"\nSaved to: {output_file}")


if __name__ == "__main__":
    main()
