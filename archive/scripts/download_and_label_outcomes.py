"""
Download stock minute bars and label backtest outcomes using flat files.

Run from CLI:
  python download_and_label_outcomes.py
"""
import boto3
from botocore.config import Config
import gzip
import os
from datetime import datetime
from collections import defaultdict
import json

# S3 Config
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2"
OPTIONS_DIR = os.path.join(BASE_DIR, "polygon_data")
STOCKS_DIR = os.path.join(BASE_DIR, "polygon_data", "stocks")


def get_s3_client():
    session = boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    return session.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        config=Config(signature_version='s3v4'),
    )


def download_stock_files(dates):
    """Download stock minute agg files for specified dates."""
    os.makedirs(STOCKS_DIR, exist_ok=True)
    s3 = get_s3_client()
    
    print("="*70)
    print("DOWNLOADING STOCK MINUTE BARS")
    print("="*70)
    
    for date in dates:
        local_path = os.path.join(STOCKS_DIR, f'{date}.csv.gz')
        
        if os.path.exists(local_path):
            print(f"  {date} - already exists")
            continue
        
        key = f'us_stocks_sip/minute_aggs_v1/2026/01/{date}.csv.gz'
        print(f"  Downloading {date}...", end=' ', flush=True)
        
        try:
            s3.download_file(BUCKET, key, local_path)
            size_mb = os.path.getsize(local_path) / (1024*1024)
            print(f"{size_mb:.1f} MB")
        except Exception as e:
            print(f"FAILED: {e}")


def load_stock_minute_bars(date):
    """
    Load all stock minute bars for a date into a dict.
    Returns: {ticker: [(timestamp_ms, open, high, low, close, volume), ...]}
    """
    filepath = os.path.join(STOCKS_DIR, f'{date}.csv.gz')
    if not os.path.exists(filepath):
        return {}
    
    bars_by_ticker = defaultdict(list)
    
    with gzip.open(filepath, 'rt') as f:
        header = f.readline().strip().split(',')
        # Actual: ticker,volume,open,close,high,low,window_start,transactions
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(',')
            ticker = parts[idx['ticker']]
            
            bars_by_ticker[ticker].append({
                't': int(parts[idx['window_start']]) // 1000000,  # Convert ns to ms
                'o': float(parts[idx['open']]),
                'h': float(parts[idx['high']]),
                'l': float(parts[idx['low']]),
                'c': float(parts[idx['close']]),
                'v': int(parts[idx['volume']]),
            })
    
    return dict(bars_by_ticker)


def find_price_at_time(bars, target_time):
    """Find the price closest to target time."""
    if not bars:
        return None
    
    target_ts = target_time.timestamp() * 1000
    
    # Find closest bar at or before target time
    closest = None
    for bar in bars:
        if bar['t'] <= target_ts:
            closest = bar
        else:
            break
    
    return closest['c'] if closest else bars[0]['o']


def calculate_outcomes(signals):
    """Calculate price outcomes using local flat files."""
    print("\n" + "="*70)
    print("CALCULATING PRICE OUTCOMES (from flat files)")
    print("="*70)
    
    # Group signals by date
    by_date = defaultdict(list)
    for s in signals:
        by_date[s['date']].append(s)
    
    print(f"Dates to process: {sorted(by_date.keys())}")
    
    enriched = []
    
    for date in sorted(by_date.keys()):
        print(f"\n  Processing {date}...")
        
        # Load all stock bars for this date
        stock_bars = load_stock_minute_bars(date)
        print(f"    Loaded {len(stock_bars)} tickers")
        
        date_signals = by_date[date]
        matched = 0
        
        for sig in date_signals:
            ticker = sig['underlying']
            bars = stock_bars.get(ticker, [])
            
            if not bars:
                enriched.append({**sig, 'price_at_detection': None})
                continue
            
            matched += 1
            
            # Sort bars by time
            bars = sorted(bars, key=lambda x: x['t'])
            
            # Parse detection time
            det_time = datetime.fromisoformat(sig['detection_time'])
            det_ts = det_time.timestamp() * 1000
            
            # Find price at detection
            price_at_detection = find_price_at_time(bars, det_time)
            
            # Day stats
            day_open = bars[0]['o']
            day_high = max(b['h'] for b in bars)
            day_low = min(b['l'] for b in bars)
            day_close = bars[-1]['c']
            
            # Find market open price (9:30 AM)
            market_open_price = None
            for bar in bars:
                bar_time = datetime.fromtimestamp(bar['t'] / 1000)
                if bar_time.hour == 9 and bar_time.minute >= 30:
                    market_open_price = bar['o']
                    break
                elif bar_time.hour > 9:
                    market_open_price = bar['o']
                    break
            
            # Bars after detection
            bars_after = [b for b in bars if b['t'] >= det_ts]
            max_after = max(b['h'] for b in bars_after) if bars_after else None
            min_after = min(b['l'] for b in bars_after) if bars_after else None
            
            # Calculate returns
            if price_at_detection and price_at_detection > 0:
                pct_to_close = (day_close - price_at_detection) / price_at_detection * 100
                pct_max_gain = (max_after - price_at_detection) / price_at_detection * 100 if max_after else None
                pct_max_loss = (min_after - price_at_detection) / price_at_detection * 100 if min_after else None
            else:
                pct_to_close = pct_max_gain = pct_max_loss = None
            
            is_premarket = det_time.hour < 9 or (det_time.hour == 9 and det_time.minute < 30)
            
            gap_pct = None
            if is_premarket and price_at_detection and market_open_price:
                gap_pct = (market_open_price - price_at_detection) / price_at_detection * 100
            
            enriched.append({
                **sig,
                'price_at_detection': round(price_at_detection, 4) if price_at_detection else None,
                'market_open_price': round(market_open_price, 4) if market_open_price else None,
                'day_open': round(day_open, 4),
                'day_high': round(day_high, 4),
                'day_low': round(day_low, 4),
                'day_close': round(day_close, 4),
                'pct_to_close': round(pct_to_close, 2) if pct_to_close is not None else None,
                'pct_max_gain': round(pct_max_gain, 2) if pct_max_gain is not None else None,
                'pct_max_loss': round(pct_max_loss, 2) if pct_max_loss is not None else None,
                'gap_pct': round(gap_pct, 2) if gap_pct is not None else None,
                'is_premarket': is_premarket,
            })
        
        print(f"    Matched {matched}/{len(date_signals)} signals")
    
    return enriched


def analyze_outcomes(signals):
    """Analyze price outcomes."""
    print("\n" + "="*70)
    print("OUTCOME ANALYSIS")
    print("="*70)
    
    with_outcomes = [s for s in signals if s.get('pct_to_close') is not None]
    print(f"Signals with price data: {len(with_outcomes)}")
    
    if not with_outcomes:
        print("No outcome data!")
        return {}
    
    closes = [s['pct_to_close'] for s in with_outcomes]
    
    print(f"\n--- Overall Performance ---")
    print(f"Count: {len(with_outcomes)}")
    print(f"Avg % to close: {sum(closes)/len(closes):.2f}%")
    print(f"Median: {sorted(closes)[len(closes)//2]:.2f}%")
    
    winners = len([s for s in with_outcomes if s['pct_to_close'] > 0])
    print(f"Win rate: {winners/len(with_outcomes)*100:.1f}%")
    
    big_winners = len([s for s in with_outcomes if s['pct_to_close'] > 5])
    big_losers = len([s for s in with_outcomes if s['pct_to_close'] < -5])
    print(f"Big winners (>5%): {big_winners} ({big_winners/len(with_outcomes)*100:.1f}%)")
    print(f"Big losers (<-5%): {big_losers} ({big_losers/len(with_outcomes)*100:.1f}%)")
    
    # Pre-market
    print(f"\n--- Pre-Market Signals ---")
    premarket = [s for s in with_outcomes if s.get('is_premarket')]
    if premarket:
        pm_closes = [s['pct_to_close'] for s in premarket]
        pm_winners = len([s for s in premarket if s['pct_to_close'] > 0])
        print(f"Count: {len(premarket)}")
        print(f"Avg: {sum(pm_closes)/len(pm_closes):.2f}%")
        print(f"Win rate: {pm_winners/len(premarket)*100:.1f}%")
    
    # By ratio
    print(f"\n--- By Ratio Bucket ---")
    buckets = [('2-5x', 2, 5), ('5-10x', 5, 10), ('10-50x', 10, 50), 
               ('50-100x', 50, 100), ('100x+', 100, float('inf'))]
    
    for name, lo, hi in buckets:
        bucket = [s for s in with_outcomes if lo <= s['ratio'] < hi]
        if bucket:
            avg = sum(s['pct_to_close'] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s['pct_to_close'] > 0]) / len(bucket) * 100
            print(f"  {name:10}: n={len(bucket):4}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # Top 25
    print(f"\n--- Top 25 Performers ---")
    print(f"{'Date':12} {'Ticker':8} {'Ratio':>7} {'Det':>6} {'Price':>8} {'%Close':>8} {'%Max':>8}")
    print("-"*65)
    
    sorted_signals = sorted(with_outcomes, key=lambda x: x['pct_to_close'], reverse=True)
    for s in sorted_signals[:25]:
        t = s['detection_time'][11:16]
        p = f"${s['price_at_detection']:.2f}" if s.get('price_at_detection') else 'N/A'
        mg = f"{s['pct_max_gain']:.1f}" if s.get('pct_max_gain') else 'N/A'
        print(f"{s['date']:12} {s['underlying']:8} {s['ratio']:>6.1f}x {t:>6} {p:>8} {s['pct_to_close']:>+7.1f}% {mg:>7}%")
    
    # Bottom 25
    print(f"\n--- Bottom 25 Performers ---")
    for s in sorted_signals[-25:]:
        t = s['detection_time'][11:16]
        p = f"${s['price_at_detection']:.2f}" if s.get('price_at_detection') else 'N/A'
        ml = f"{s['pct_max_loss']:.1f}" if s.get('pct_max_loss') else 'N/A'
        print(f"{s['date']:12} {s['underlying']:8} {s['ratio']:>6.1f}x {t:>6} {p:>8} {s['pct_to_close']:>+7.1f}% {ml:>7}%")
    
    # AAOI
    print(f"\n--- AAOI ---")
    aaoi = [s for s in with_outcomes if s['underlying'] == 'AAOI']
    for s in aaoi:
        print(f"  {s['date']} @ {s['detection_time'][11:16]}: ratio={s['ratio']:.1f}x, "
              f"price=${s.get('price_at_detection','?')}, close={s['pct_to_close']:+.2f}%")
    
    return {
        'total': len(with_outcomes),
        'avg': sum(closes)/len(closes),
        'win_rate': winners/len(with_outcomes),
    }


def main():
    # Dates we have options data for
    dates = [
        '2026-01-13', '2026-01-14', '2026-01-15', '2026-01-16',
        '2026-01-20', '2026-01-21', '2026-01-22', '2026-01-23',
        '2026-01-26', '2026-01-27', '2026-01-28'
    ]
    
    # Step 1: Download stock minute bars
    download_stock_files(dates)
    
    # Step 2: Load backtest signals
    signals_file = os.path.join(OPTIONS_DIR, 'multi_day_backtest.json')
    print(f"\nLoading signals from {signals_file}...")
    with open(signals_file, 'r') as f:
        data = json.load(f)
    signals = data['signals']
    print(f"Loaded {len(signals)} signals")
    
    # Step 3: Calculate outcomes
    enriched = calculate_outcomes(signals)
    
    # Step 4: Analyze
    summary = analyze_outcomes(enriched)
    
    # Step 5: Save
    output_file = os.path.join(OPTIONS_DIR, 'backtest_with_outcomes.json')
    with open(output_file, 'w') as f:
        json.dump({'signals': enriched, 'summary': summary}, f, indent=2)
    print(f"\nSaved to: {output_file}")


if __name__ == "__main__":
    main()
