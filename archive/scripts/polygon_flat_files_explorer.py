"""
Polygon/Massive Flat Files - Download and Explore Options Trades

Now with proper S3 credentials!
"""

import boto3
from botocore.config import Config
import os
import gzip
from datetime import datetime
from collections import defaultdict

# S3 Credentials
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

def get_s3_client():
    """Create S3 client with Polygon/Massive credentials."""
    session = boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    return session.client(
        's3',
        endpoint_url=S3_ENDPOINT,
        config=Config(signature_version='s3v4'),
    )


def list_options_trades_files(s3, prefix='us_options_opra/trades_v1/2026/01/'):
    """List available options trades files."""
    print(f"\n--- Listing files in {prefix} ---")
    
    response = s3.list_objects_v2(
        Bucket=BUCKET,
        Prefix=prefix,
        MaxKeys=50
    )
    
    files = []
    if 'Contents' in response:
        for obj in response['Contents']:
            size_mb = obj['Size'] / (1024*1024)
            print(f"  {obj['Key']} - {size_mb:.1f} MB")
            files.append(obj)
    else:
        print("  No files found")
    
    return files


def download_file(s3, key, local_path):
    """Download a flat file."""
    print(f"\n--- Downloading {key} ---")
    
    # Get file info
    response = s3.head_object(Bucket=BUCKET, Key=key)
    size_mb = response['ContentLength'] / (1024*1024)
    print(f"File size: {size_mb:.1f} MB")
    
    # Download
    print(f"Downloading to {local_path}...")
    s3.download_file(BUCKET, key, local_path)
    
    local_size = os.path.getsize(local_path) / (1024*1024)
    print(f"Downloaded: {local_size:.1f} MB")
    
    return local_path


def explore_trades_file(filepath):
    """Explore the structure and contents of an options trades file."""
    print(f"\n{'='*70}")
    print(f"EXPLORING: {filepath}")
    print('='*70)
    
    # Track stats
    underlying_call_volume = defaultdict(int)
    underlying_put_volume = defaultdict(int)
    underlying_trades = defaultdict(int)
    total_trades = 0
    sample_rows = []
    
    with gzip.open(filepath, 'rt') as f:
        # Read header
        header = f.readline().strip().split(',')
        print(f"\nColumns ({len(header)}): {header}")
        
        # Find column indices
        ticker_idx = header.index('ticker') if 'ticker' in header else 0
        size_idx = header.index('size') if 'size' in header else -1
        price_idx = header.index('price') if 'price' in header else -1
        ts_idx = header.index('sip_timestamp') if 'sip_timestamp' in header else -1
        
        print(f"\nKey columns: ticker={ticker_idx}, size={size_idx}, price={price_idx}, timestamp={ts_idx}")
        
        # Process rows
        for line in f:
            total_trades += 1
            
            if total_trades <= 10:
                sample_rows.append(line.strip())
            
            try:
                parts = line.strip().split(',')
                ticker = parts[ticker_idx]
                size = int(parts[size_idx]) if size_idx >= 0 else 1
                
                # Parse option ticker: O:AAOI260130C00040000
                # Format: O:UNDERLYING + YYMMDD + C/P + STRIKE
                if ticker.startswith('O:'):
                    # Find where the date starts (6 digits after underlying)
                    ticker_body = ticker[2:]  # Remove O:
                    
                    # Find first digit
                    underlying = ''
                    for i, c in enumerate(ticker_body):
                        if c.isdigit():
                            underlying = ticker_body[:i]
                            # Check if call or put (C or P after the date)
                            rest = ticker_body[i:]
                            if len(rest) >= 7:  # YYMMDD + C/P
                                option_type = rest[6]  # C or P
                                
                                if option_type == 'C':
                                    underlying_call_volume[underlying] += size
                                elif option_type == 'P':
                                    underlying_put_volume[underlying] += size
                                    
                                underlying_trades[underlying] += 1
                            break
                            
            except Exception as e:
                if total_trades < 100:
                    print(f"Parse error on row {total_trades}: {e}")
            
            # Progress update
            if total_trades % 1000000 == 0:
                print(f"  Processed {total_trades:,} trades...")
    
    print(f"\n--- RESULTS ---")
    print(f"Total trades in file: {total_trades:,}")
    print(f"Unique underlyings: {len(underlying_trades):,}")
    
    print(f"\nSample rows:")
    for i, row in enumerate(sample_rows[:5]):
        print(f"  {i+1}: {row[:120]}...")
    
    # Top underlyings by call volume
    print(f"\n--- TOP 20 UNDERLYINGS BY CALL VOLUME ---")
    sorted_calls = sorted(underlying_call_volume.items(), key=lambda x: x[1], reverse=True)
    for ticker, vol in sorted_calls[:20]:
        put_vol = underlying_put_volume.get(ticker, 0)
        pc_ratio = put_vol / vol if vol > 0 else 0
        trades = underlying_trades.get(ticker, 0)
        print(f"  {ticker:6} | Calls: {vol:>10,} | Puts: {put_vol:>10,} | P/C: {pc_ratio:.2f} | Trades: {trades:,}")
    
    # Check for AAOI specifically
    if 'AAOI' in underlying_call_volume:
        print(f"\n--- AAOI SPECIFICALLY ---")
        print(f"  Call volume: {underlying_call_volume['AAOI']:,}")
        print(f"  Put volume: {underlying_put_volume['AAOI']:,}")
        print(f"  Trades: {underlying_trades['AAOI']:,}")
    
    return {
        'total_trades': total_trades,
        'call_volume': dict(underlying_call_volume),
        'put_volume': dict(underlying_put_volume),
    }


def main():
    print("="*70)
    print("POLYGON FLAT FILES - OPTIONS TRADES EXPLORER")
    print("="*70)
    print(f"Time: {datetime.now()}")
    print(f"Endpoint: {S3_ENDPOINT}")
    print(f"Bucket: {BUCKET}")
    
    # Create S3 client
    s3 = get_s3_client()
    
    # Test connection by listing root
    print("\n--- Testing connection ---")
    try:
        response = s3.list_objects_v2(Bucket=BUCKET, Prefix='us_options_opra/', Delimiter='/', MaxKeys=10)
        print("Connection successful!")
        
        if 'CommonPrefixes' in response:
            print("Available prefixes:")
            for p in response['CommonPrefixes']:
                print(f"  {p['Prefix']}")
    except Exception as e:
        print(f"Connection failed: {e}")
        return
    
    # List January 2026 files
    files = list_options_trades_files(s3, 'us_options_opra/trades_v1/2026/01/')
    
    if files:
        # Download the Jan 27 file (the day before the big AAOI move)
        # Or Jan 28 if available
        target_file = None
        for f in files:
            if '2026-01-27' in f['Key'] or '2026-01-28' in f['Key']:
                target_file = f['Key']
                break
        
        if not target_file and files:
            target_file = files[-1]['Key']  # Latest available
        
        if target_file:
            local_path = f"C:\\Users\\levir\\Documents\\FL3_V2\\{os.path.basename(target_file)}"
            
            # Download if not exists
            if not os.path.exists(local_path):
                download_file(s3, target_file, local_path)
            else:
                print(f"\nFile already exists: {local_path}")
            
            # Explore the file
            explore_trades_file(local_path)


if __name__ == "__main__":
    main()
