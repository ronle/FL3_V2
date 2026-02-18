"""
Polygon Flat Files via S3 - Options Trades

Access Polygon's flat files using S3 protocol.
"""

import boto3
from botocore.config import Config
from botocore import UNSIGNED
import os
from datetime import datetime

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"

def explore_flat_files():
    print("="*70)
    print("POLYGON FLAT FILES VIA S3")
    print("="*70)
    print(f"API Key: {API_KEY[:8]}...")
    print(f"Time: {datetime.now()}")
    
    # Create S3 client for Polygon
    s3 = boto3.client('s3',
        endpoint_url='https://files.polygon.io',
        aws_access_key_id=API_KEY,
        aws_secret_access_key=API_KEY,
        config=Config(signature_version='s3v4')
    )
    
    print("\n--- Listing buckets ---")
    try:
        response = s3.list_buckets()
        print(f"Buckets: {[b['Name'] for b in response.get('Buckets', [])]}")
    except Exception as e:
        print(f"Cannot list buckets (expected): {e}")
    
    print("\n--- Listing options trades files ---")
    try:
        # Try different prefixes
        prefixes = [
            'us_options_opra/trades_v1/2026/01/',
            'us_options_opra/trades/2026/01/',
            'options/trades/2026/01/',
        ]
        
        for prefix in prefixes:
            print(f"\nTrying prefix: {prefix}")
            try:
                response = s3.list_objects_v2(
                    Bucket='flatfiles',
                    Prefix=prefix,
                    MaxKeys=10
                )
                
                if 'Contents' in response and response['Contents']:
                    print(f"  Found {len(response['Contents'])} files!")
                    for obj in response['Contents'][:5]:
                        size_mb = obj['Size'] / (1024*1024)
                        print(f"    {obj['Key']} - {size_mb:.1f} MB")
                    return prefix, response['Contents']
                else:
                    print(f"  No files found")
            except Exception as e:
                print(f"  Error: {e}")
                
    except Exception as e:
        print(f"Error: {e}")
    
    return None, None


def download_sample_file(s3, key, local_path, max_mb=50):
    """Download a flat file (or part of it)."""
    print(f"\n--- Downloading {key} ---")
    
    try:
        # Get file info first
        response = s3.head_object(Bucket='flatfiles', Key=key)
        size_mb = response['ContentLength'] / (1024*1024)
        print(f"File size: {size_mb:.1f} MB")
        
        if size_mb > max_mb:
            print(f"File too large, downloading first {max_mb} MB...")
            # Download partial
            range_header = f"bytes=0-{max_mb * 1024 * 1024}"
            response = s3.get_object(
                Bucket='flatfiles',
                Key=key,
                Range=range_header
            )
        else:
            response = s3.get_object(Bucket='flatfiles', Key=key)
        
        # Save to local file
        with open(local_path, 'wb') as f:
            for chunk in response['Body'].iter_chunks(chunk_size=8192):
                f.write(chunk)
        
        print(f"Downloaded to: {local_path}")
        print(f"Local size: {os.path.getsize(local_path) / (1024*1024):.1f} MB")
        return True
        
    except Exception as e:
        print(f"Download error: {e}")
        return False


def explore_file_contents(filepath):
    """Explore the contents of a downloaded flat file."""
    import gzip
    import csv
    from collections import defaultdict
    
    print(f"\n--- Exploring {filepath} ---")
    
    try:
        with gzip.open(filepath, 'rt') as f:
            reader = csv.reader(f)
            
            # Read header
            header = next(reader)
            print(f"\nColumns: {header}")
            
            # Read sample rows and aggregate
            underlying_volume = defaultdict(int)
            rows_read = 0
            sample_rows = []
            
            for row in reader:
                rows_read += 1
                
                if rows_read <= 10:
                    sample_rows.append(row)
                
                # Extract underlying from option symbol
                # O:AAOI260130C00040000 -> AAOI
                if len(row) > 0:
                    ticker = row[0]
                    if ticker.startswith('O:'):
                        # Find where numbers start (after underlying)
                        underlying = ''
                        for i, c in enumerate(ticker[2:]):
                            if c.isdigit():
                                underlying = ticker[2:2+i]
                                break
                        
                        # Get size (usually last or near-last column)
                        try:
                            size_idx = header.index('size') if 'size' in header else -1
                            size = int(row[size_idx]) if size_idx >= 0 else 1
                            underlying_volume[underlying] += size
                        except:
                            underlying_volume[underlying] += 1
                
                if rows_read >= 100000:  # Sample first 100K rows
                    break
            
            print(f"\nSample rows:")
            for i, row in enumerate(sample_rows):
                print(f"  {i+1}: {row[:6]}...")  # First 6 columns
            
            print(f"\nRows read: {rows_read:,}")
            print(f"\nTop 20 underlyings by volume (from sample):")
            sorted_vol = sorted(underlying_volume.items(), key=lambda x: x[1], reverse=True)
            for ticker, vol in sorted_vol[:20]:
                print(f"  {ticker:6} : {vol:>10,}")
                
    except Exception as e:
        print(f"Error exploring file: {e}")
        import traceback
        traceback.print_exc()


def main():
    # Create S3 client
    s3 = boto3.client('s3',
        endpoint_url='https://files.polygon.io',
        aws_access_key_id=API_KEY,
        aws_secret_access_key=API_KEY,
        config=Config(signature_version='s3v4')
    )
    
    # Explore available files
    prefix, files = explore_flat_files()
    
    if files:
        # Download the most recent file
        latest_file = files[-1]['Key']  # Assuming sorted by date
        local_path = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_options_trades.csv.gz"
        
        if download_sample_file(s3, latest_file, local_path, max_mb=100):
            explore_file_contents(local_path)
    else:
        print("\nNo files found. Let's try listing the root...")
        
        # Try listing root of flatfiles bucket
        try:
            response = s3.list_objects_v2(
                Bucket='flatfiles',
                Delimiter='/',
                MaxKeys=100
            )
            
            print("\nRoot prefixes:")
            for prefix in response.get('CommonPrefixes', []):
                print(f"  {prefix['Prefix']}")
                
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    main()
