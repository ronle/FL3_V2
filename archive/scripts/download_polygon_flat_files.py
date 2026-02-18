"""
Polygon/Massive Flat Files - Download Options Trades

Download and explore options trades flat file for backtesting.
"""

import boto3
from botocore.config import Config
import gzip
import csv
from datetime import datetime
from collections import defaultdict
import os

# S3 Credentials
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

OUTPUT_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"

def get_s3_client():
    """Create S3 client for Massive/Polygon."""
    session = boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    return session.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        config=Config(signature_version='s3v4'),
    )

def list_available_files(s3, prefix='us_options_opra/trades_v1/2026/01/'):
    """List available flat files."""
    print(f"\n{'='*70}")
    print(f"LISTING FILES: {prefix}")
    print('='*70)
    
    try:
        response = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=prefix,
            MaxKeys=50
        )
        
        if 'Contents' in response:
            print(f"Found {len(response['Contents'])} files:\n")
            for obj in response['Contents']:
                size_mb = obj['Size'] / (1024*1024)
                print(f"  {obj['Key']:60} {size_mb:>8.1f} MB")
            return response['Contents']
        else:
            print("No files found")
            return []
    except Exception as e:
        print(f"Error: {e}")
        return []

def download_file(s3, key, local_path):
    """Download a flat file."""
    print(f"\nDownloading: {key}")
    print(f"To: {local_path}")
    
    try:
        # Get file size first
        response = s3.head_object(Bucket=BUCKET, Key=key)
        size_mb = response['ContentLength'] / (1024*1024)
        print(f"File size: {size_mb:.1f} MB")
        
        # Download
        s3.download_file(BUCKET, key, local_path)
        
        local_size = os.path.getsize(local_path) / (1024*1024)
        print(f"Downloaded: {local_size:.1f} MB")
        return True
    except Exception as e:
        print(f"Download error: {e}")
        return False

def explore_trades_file(filepath):
    """Explore the contents of an options trades flat file."""
    print(f"\n{'='*70}")
    print(f"EXPLORING: {filepath}")
    print('='*70)
    
    # Aggregation structures
    underlying_call_vol = defaultdict(int)
    underlying_put_vol = defaultdict(int)
    underlying_trades = defaultdict(int)
    hourly_volume = defaultdict(int)
    
    rows_read = 0
    sample_rows = []
    
    with gzip.open(filepath, 'rt') as f:
        reader = csv.DictReader(f)
        
        print(f"\nColumns: {reader.fieldnames}")
        
        for row in reader:
            rows_read += 1
            
            # Save sample
            if rows_read <= 5:
                sample_rows.append(row)
            
            # Parse ticker: O:AAOI260130C00040000
            ticker = row.get('ticker', '')
            if not ticker.startswith('O:'):
                continue
                
            # Extract underlying (everything between O: and first digit)
            underlying = ''
            for i, c in enumerate(ticker[2:]):
                if c.isdigit():
                    underlying = ticker[2:2+i]
                    break
            
            if not underlying:
                continue
            
            # Determine call/put (C or P before strike)
            is_call = 'C' in ticker[2+len(underlying):2+len(underlying)+7]
            
            # Get size
            size = int(row.get('size', 0))
            
            # Aggregate
            if is_call:
                underlying_call_vol[underlying] += size
            else:
                underlying_put_vol[underlying] += size
            underlying_trades[underlying] += 1
            
            # Time aggregation
            ts = row.get('sip_timestamp', '')
            if ts:
                try:
                    ts_sec = int(ts) / 1e9
                    dt = datetime.fromtimestamp(ts_sec)
                    hour = dt.strftime('%H:00')
                    hourly_volume[hour] += size
                except:
                    pass
            
            # Progress
            if rows_read % 500000 == 0:
                print(f"  Processed {rows_read:,} rows...")
    
    print(f"\nTotal rows: {rows_read:,}")
    
    # Sample rows
    print(f"\n{'='*70}")
    print("SAMPLE ROWS")
    print('='*70)
    for i, row in enumerate(sample_rows):
        print(f"\nRow {i+1}:")
        for k, v in row.items():
            print(f"  {k}: {v}")
    
    # Hourly distribution
    print(f"\n{'='*70}")
    print("VOLUME BY HOUR")
    print('='*70)
    for hour in sorted(hourly_volume.keys()):
        vol = hourly_volume[hour]
        bar = '#' * min(50, vol // 50000)
        print(f"{hour} | {vol:>12,} | {bar}")
    
    # Top underlyings by call volume
    print(f"\n{'='*70}")
    print("TOP 30 UNDERLYINGS BY CALL VOLUME")
    print('='*70)
    print(f"{'Ticker':<8} {'Calls':>12} {'Puts':>12} {'Total':>12} {'P/C Ratio':>10} {'Trades':>10}")
    print('-'*70)
    
    sorted_by_calls = sorted(underlying_call_vol.items(), key=lambda x: x[1], reverse=True)
    
    for ticker, call_vol in sorted_by_calls[:30]:
        put_vol = underlying_put_vol.get(ticker, 0)
        total = call_vol + put_vol
        pc_ratio = put_vol / call_vol if call_vol > 0 else 0
        trades = underlying_trades[ticker]
        
        print(f"{ticker:<8} {call_vol:>12,} {put_vol:>12,} {total:>12,} {pc_ratio:>10.2f} {trades:>10,}")
    
    return {
        'total_rows': rows_read,
        'call_volume': dict(underlying_call_vol),
        'put_volume': dict(underlying_put_vol),
        'hourly': dict(hourly_volume)
    }

def main():
    print("="*70)
    print("POLYGON/MASSIVE FLAT FILES - OPTIONS TRADES EXPLORER")
    print("="*70)
    print(f"Time: {datetime.now()}")
    print(f"Endpoint: {S3_ENDPOINT}")
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Connect to S3
    print("\nConnecting to S3...")
    s3 = get_s3_client()
    
    # List available files
    files = list_available_files(s3, 'us_options_opra/trades_v1/2026/01/')
    
    if not files:
        print("No files found, trying root listing...")
        list_available_files(s3, 'us_options_opra/')
        return
    
    # Download Jan 27 or most recent file
    target_file = None
    for f in files:
        if '2026-01-27' in f['Key'] or '2026-01-28' in f['Key']:
            target_file = f['Key']
            break
    
    if not target_file and files:
        target_file = files[-1]['Key']  # Most recent
    
    if target_file:
        filename = os.path.basename(target_file)
        local_path = os.path.join(OUTPUT_DIR, filename)
        
        if os.path.exists(local_path):
            print(f"\nFile already exists: {local_path}")
        else:
            download_file(s3, target_file, local_path)
        
        if os.path.exists(local_path):
            explore_trades_file(local_path)
    
    print(f"\n{'='*70}")
    print("DONE!")
    print('='*70)

if __name__ == "__main__":
    main()
