"""
AAOI Deep Dive - Jan 28, 2026 Options Flow Analysis

Download Jan 28 flat file and trace AAOI trades with timestamps to 
see exactly when the unusual activity started.
"""

import boto3
from botocore.config import Config
import gzip
import os
from datetime import datetime
from collections import defaultdict

# S3 Credentials
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

OUTPUT_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"

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

def download_if_needed(s3, key, local_path):
    """Download file if not already present."""
    if os.path.exists(local_path):
        print(f"File exists: {local_path}")
        return True
    
    print(f"Downloading: {key}...")
    try:
        s3.download_file(BUCKET, key, local_path)
        print(f"Downloaded: {os.path.getsize(local_path) / (1024*1024):.1f} MB")
        return True
    except Exception as e:
        print(f"Download error: {e}")
        return False

def analyze_aaoi_flow(filepath):
    """Analyze AAOI options flow with timestamps."""
    print(f"\n{'='*70}")
    print(f"AAOI OPTIONS FLOW ANALYSIS - JAN 28, 2026")
    print('='*70)
    
    aaoi_trades = []
    other_calls = defaultdict(int)  # For comparison
    
    rows_read = 0
    
    with gzip.open(filepath, 'rt') as f:
        header = f.readline().strip().split(',')
        print(f"Columns: {header}")
        
        # ticker,conditions,correction,exchange,price,sip_timestamp,size
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            rows_read += 1
            parts = line.strip().split(',')
            
            ticker = parts[idx['ticker']]
            if not ticker.startswith('O:'):
                continue
            
            # Get underlying
            underlying = ''
            for i, c in enumerate(ticker[2:]):
                if c.isdigit():
                    underlying = ticker[2:2+i]
                    break
            
            # Get call/put
            rest = ticker[2+len(underlying):]
            is_call = 'C' in rest[:7] if len(rest) >= 7 else False
            
            size = int(parts[idx['size']])
            
            # Track all call volume for comparison
            if is_call:
                other_calls[underlying] += size
            
            # Capture all AAOI trades
            if underlying == 'AAOI':
                ts_ns = int(parts[idx['sip_timestamp']])
                ts_sec = ts_ns / 1e9
                dt = datetime.fromtimestamp(ts_sec)
                
                aaoi_trades.append({
                    'time': dt,
                    'ticker': ticker,
                    'price': float(parts[idx['price']]),
                    'size': size,
                    'is_call': is_call,
                    'exchange': parts[idx['exchange']],
                })
            
            if rows_read % 1000000 == 0:
                print(f"  Processed {rows_read:,} rows...")
    
    print(f"\nTotal rows: {rows_read:,}")
    print(f"AAOI trades captured: {len(aaoi_trades):,}")
    
    # Sort AAOI trades by time
    aaoi_trades.sort(key=lambda x: x['time'])
    
    # Early trades (pre-market and first hour)
    print(f"\n{'='*70}")
    print("AAOI EARLY TRADES (First 50)")
    print('='*70)
    print(f"{'Time':19} {'Type':4} {'Contract':30} {'Price':>8} {'Size':>6}")
    print('-'*70)
    
    for trade in aaoi_trades[:50]:
        t_type = 'CALL' if trade['is_call'] else 'PUT'
        # Extract strike from ticker
        contract = trade['ticker'][2:]  # Remove O:
        print(f"{trade['time'].strftime('%H:%M:%S.%f')[:12]} {t_type:4} {contract:30} ${trade['price']:>7.2f} {trade['size']:>6}")
    
    # Aggregate by 5-minute intervals
    print(f"\n{'='*70}")
    print("AAOI CALL VOLUME BY 5-MINUTE INTERVALS")
    print('='*70)
    
    interval_volume = defaultdict(int)
    interval_trades = defaultdict(int)
    
    for trade in aaoi_trades:
        if trade['is_call']:
            # Round to 5-min interval
            minute = (trade['time'].minute // 5) * 5
            interval = trade['time'].replace(minute=minute, second=0, microsecond=0)
            interval_str = interval.strftime('%H:%M')
            interval_volume[interval_str] += trade['size']
            interval_trades[interval_str] += 1
    
    print(f"{'Time':6} {'Volume':>10} {'Trades':>8} {'Bar'}")
    print('-'*60)
    
    max_vol = max(interval_volume.values()) if interval_volume else 1
    
    for time_str in sorted(interval_volume.keys()):
        vol = interval_volume[time_str]
        trades = interval_trades[time_str]
        bar_len = int(50 * vol / max_vol)
        bar = '#' * bar_len
        
        # Highlight unusual activity
        marker = " ***" if vol > 200 else ""
        print(f"{time_str:6} {vol:>10,} {trades:>8} {bar}{marker}")
    
    # Sum up call and put volumes
    total_call_vol = sum(t['size'] for t in aaoi_trades if t['is_call'])
    total_put_vol = sum(t['size'] for t in aaoi_trades if not t['is_call'])
    
    print(f"\n{'='*70}")
    print("AAOI SUMMARY")
    print('='*70)
    print(f"Total AAOI call volume: {total_call_vol:,}")
    print(f"Total AAOI put volume:  {total_put_vol:,}")
    print(f"Call/Put ratio: {total_call_vol/total_put_vol:.2f}" if total_put_vol > 0 else "")
    
    # Pre-market vs regular hours
    premarket_calls = sum(t['size'] for t in aaoi_trades if t['is_call'] and t['time'].hour < 9)
    regular_calls = sum(t['size'] for t in aaoi_trades if t['is_call'] and t['time'].hour >= 9)
    
    print(f"\nPre-market call volume (before 9am): {premarket_calls:,}")
    print(f"Regular hours call volume (9am+):    {regular_calls:,}")
    
    # First trade timestamps
    first_call = next((t for t in aaoi_trades if t['is_call']), None)
    if first_call:
        print(f"\nFirst AAOI call trade: {first_call['time'].strftime('%H:%M:%S')}")
    
    # Compare to market averages
    print(f"\n{'='*70}")
    print("AAOI VS OTHER STOCKS - CALL VOLUME RANKING")
    print('='*70)
    
    sorted_calls = sorted(other_calls.items(), key=lambda x: x[1], reverse=True)
    aaoi_rank = next((i for i, (t, v) in enumerate(sorted_calls) if t == 'AAOI'), -1) + 1
    
    print(f"AAOI rank by call volume: #{aaoi_rank} out of {len(other_calls)} underlyings")
    
    # Show context around AAOI's rank
    if aaoi_rank > 0:
        start = max(0, aaoi_rank - 5)
        end = min(len(sorted_calls), aaoi_rank + 5)
        print(f"\nNearby stocks:")
        for i in range(start, end):
            ticker, vol = sorted_calls[i]
            marker = " <-- AAOI" if ticker == 'AAOI' else ""
            print(f"  #{i+1:4} {ticker:8} {vol:>12,}{marker}")
    
    return {
        'trades': aaoi_trades,
        'total_calls': total_call_vol,
        'total_puts': total_put_vol,
        'premarket_calls': premarket_calls,
    }

def main():
    print("="*70)
    print("AAOI FLAT FILE ANALYSIS - JAN 28, 2026")
    print("="*70)
    print(f"Time: {datetime.now()}")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    s3 = get_s3_client()
    
    # Download Jan 28
    key = 'us_options_opra/trades_v1/2026/01/2026-01-28.csv.gz'
    local_path = os.path.join(OUTPUT_DIR, '2026-01-28.csv.gz')
    
    if download_if_needed(s3, key, local_path):
        analyze_aaoi_flow(local_path)

if __name__ == "__main__":
    main()
