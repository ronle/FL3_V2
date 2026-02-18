"""
Polygon Flat Files Explorer - Options Trades

Download and explore the structure of Polygon's options trades flat files.
These contain ALL options trades across ALL tickers for a given day.
"""

import requests
import gzip
import os
from datetime import datetime
from collections import defaultdict
import csv
from io import StringIO

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"

# Flat files are accessed via S3 or direct download
# Let's first check what's available via the file browser API

def list_available_files():
    """List available flat files."""
    print("="*70)
    print("EXPLORING POLYGON FLAT FILES")
    print("="*70)
    
    # Check the flat files endpoint
    url = "https://api.polygon.io/v1/reference/flat-files"
    params = {"apiKey": API_KEY}
    
    response = requests.get(url, params=params)
    print(f"Flat files endpoint status: {response.status_code}")
    
    if response.status_code == 200:
        print(f"Response: {response.json()}")
    else:
        print(f"Error: {response.text[:500]}")


def download_options_trades_sample():
    """
    Try to download options trades flat file.
    
    According to docs, flat files are at:
    s3://flatfiles/us_options_opra/trades_v1/YYYY/MM/YYYY-MM-DD.csv.gz
    
    Web access might be at:
    https://api.polygon.io/v1/flat-files/us_options_opra/trades_v1/2026/01/2026-01-27.csv.gz
    """
    print("\n" + "="*70)
    print("ATTEMPTING TO DOWNLOAD OPTIONS TRADES FLAT FILE")
    print("="*70)
    
    # Try different URL patterns
    date = "2026-01-27"
    year, month, day = date.split("-")
    
    urls_to_try = [
        f"https://api.polygon.io/v1/flat-files/us_options_opra/trades_v1/{year}/{month}/{date}.csv.gz",
        f"https://api.polygon.io/flat-files/us_options_opra/trades_v1/{year}/{month}/{date}.csv.gz",
        f"https://files.polygon.io/flatfiles/us_options_opra/trades_v1/{year}/{month}/{date}.csv.gz",
    ]
    
    for url in urls_to_try:
        print(f"\nTrying: {url}")
        try:
            response = requests.get(url, params={"apiKey": API_KEY}, stream=True, timeout=30)
            print(f"  Status: {response.status_code}")
            print(f"  Headers: {dict(response.headers)[:200] if response.headers else 'None'}")
            
            if response.status_code == 200:
                # Check content type
                content_type = response.headers.get('content-type', '')
                print(f"  Content-Type: {content_type}")
                
                if 'gzip' in content_type or url.endswith('.gz'):
                    # Save a sample
                    sample_path = f"C:\\Users\\levir\\Documents\\FL3_V2\\polygon_options_trades_sample.csv.gz"
                    
                    # Download first 10MB as sample
                    downloaded = 0
                    max_bytes = 10 * 1024 * 1024  # 10MB
                    
                    with open(sample_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                if downloaded >= max_bytes:
                                    break
                    
                    print(f"  Downloaded {downloaded:,} bytes to {sample_path}")
                    return sample_path
                else:
                    # Maybe it's JSON or HTML error
                    print(f"  Response preview: {response.text[:500]}")
            elif response.status_code == 403:
                print(f"  Access denied - may need different auth method")
            elif response.status_code == 404:
                print(f"  File not found")
        except Exception as e:
            print(f"  Error: {e}")
    
    return None


def explore_via_s3_api():
    """
    Try using the S3-compatible API that Polygon provides.
    """
    print("\n" + "="*70)
    print("EXPLORING VIA POLYGON'S FILE LISTING API")
    print("="*70)
    
    # Try to list available files
    base_url = "https://api.polygon.io"
    
    # Try the reference/files endpoint
    endpoints = [
        "/v1/reference/options/contracts",  # Sanity check - should work
        "/vX/reference/flat-files",
        "/v1/flat-files",
    ]
    
    for endpoint in endpoints:
        url = f"{base_url}{endpoint}"
        print(f"\nTrying: {url}")
        response = requests.get(url, params={"apiKey": API_KEY, "limit": 5})
        print(f"  Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"  Keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
            if isinstance(data, dict) and 'results' in data:
                print(f"  Results count: {len(data.get('results', []))}")


def check_file_browser():
    """
    The web file browser is at polygon.io/flat-files
    Let's see if there's an API behind it.
    """
    print("\n" + "="*70)
    print("CHECKING FILE BROWSER API")
    print("="*70)
    
    # The docs mention S3 access - let's check the structure
    # According to docs: s3polygon/flatfiles/us_options_opra/trades_v1/
    
    # Try the documented S3 endpoint structure
    url = "https://files.polygon.io/flat-files/us_options_opra/trades_v1/2026/01/"
    print(f"\nTrying directory listing: {url}")
    
    response = requests.get(url, params={"apiKey": API_KEY})
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print(f"Response: {response.text[:1000]}")
    else:
        print(f"Response: {response.text[:500]}")


def try_direct_s3():
    """
    Try accessing via boto3 S3 client as documented.
    """
    print("\n" + "="*70)
    print("S3 ACCESS INFO")
    print("="*70)
    print("""
According to Polygon docs, flat files can be accessed via S3:

Endpoint: https://files.polygon.io
Bucket: flatfiles
Path: us_options_opra/trades_v1/YYYY/MM/YYYY-MM-DD.csv.gz

To access via boto3/mc:
    
    import boto3
    
    s3 = boto3.client('s3',
        endpoint_url='https://files.polygon.io',
        aws_access_key_id=API_KEY,
        aws_secret_access_key=API_KEY
    )
    
    # List files
    response = s3.list_objects_v2(
        Bucket='flatfiles',
        Prefix='us_options_opra/trades_v1/2026/01/'
    )
    
    # Download file
    s3.download_file(
        'flatfiles',
        'us_options_opra/trades_v1/2026/01/2026-01-27.csv.gz',
        'local_file.csv.gz'
    )

Let's try this...
""")
    
    try:
        import boto3
        from botocore.config import Config
        
        s3 = boto3.client('s3',
            endpoint_url='https://files.polygon.io',
            aws_access_key_id=API_KEY,
            aws_secret_access_key=API_KEY,
            config=Config(signature_version='s3v4')
        )
        
        print("Listing files in us_options_opra/trades_v1/2026/01/...")
        
        response = s3.list_objects_v2(
            Bucket='flatfiles',
            Prefix='us_options_opra/trades_v1/2026/01/',
            MaxKeys=10
        )
        
        if 'Contents' in response:
            print(f"\nFound {len(response['Contents'])} files:")
            for obj in response['Contents'][:10]:
                print(f"  {obj['Key']} - {obj['Size']:,} bytes")
        else:
            print("No files found or access denied")
            print(f"Response: {response}")
            
    except ImportError:
        print("boto3 not installed. Install with: pip install boto3")
    except Exception as e:
        print(f"Error: {e}")


def main():
    print(f"\nStarting at {datetime.now()}")
    print(f"API Key: {API_KEY[:8]}...")
    
    # Try different approaches
    list_available_files()
    explore_via_s3_api()
    check_file_browser()
    try_direct_s3()
    
    # Try direct download
    result = download_options_trades_sample()
    
    if result:
        print(f"\n{'='*70}")
        print("SUCCESS! Downloaded sample file.")
        print(f"{'='*70}")
    else:
        print(f"\n{'='*70}")
        print("Could not download via HTTP. S3 access may be required.")
        print(f"{'='*70}")
        print("""
NEXT STEPS:

1. Install boto3: pip install boto3

2. Use S3 client to access flat files:
   
   import boto3
   s3 = boto3.client('s3',
       endpoint_url='https://files.polygon.io',
       aws_access_key_id='YOUR_API_KEY',
       aws_secret_access_key='YOUR_API_KEY'
   )
   
3. Or use the web file browser at:
   https://polygon.io/flat-files/us_options_opra/trades_v1
""")


if __name__ == "__main__":
    main()
