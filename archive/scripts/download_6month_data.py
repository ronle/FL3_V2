"""
Download 6 months of flat files for E2E backtest.

Options trades: us_options_opra/trades_v1
Stock minute bars: us_stocks_sip/minute_aggs_v1

Period: July 2025 (warm-up) through Jan 28, 2026
"""

import boto3
from botocore.config import Config
import os
from datetime import date, timedelta

# S3 Config
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"
OPTIONS_DIR = os.path.join(BASE_DIR, "options")
STOCKS_DIR = os.path.join(BASE_DIR, "stocks")


def get_s3_client():
    return boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    ).client('s3', endpoint_url=S3_ENDPOINT, config=Config(signature_version='s3v4'))


def get_trading_days(start_date, end_date):
    """Generate list of trading days (Mon-Fri, excluding major holidays)."""
    # Major US market holidays 2025-2026 (approximate)
    holidays = {
        date(2025, 1, 1),   # New Year
        date(2025, 1, 20),  # MLK
        date(2025, 2, 17),  # Presidents
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 26),  # Memorial
        date(2025, 6, 19),  # Juneteenth
        date(2025, 7, 4),   # Independence
        date(2025, 9, 1),   # Labor
        date(2025, 11, 27), # Thanksgiving
        date(2025, 12, 25), # Christmas
        date(2026, 1, 1),   # New Year
        date(2026, 1, 19),  # MLK
    }
    
    days = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5 and current not in holidays:
            days.append(current)
        current += timedelta(days=1)
    return days


def download_file(s3, prefix, date_str, local_dir, file_type):
    """Download a single file."""
    year = date_str[:4]
    month = date_str[5:7]
    
    key = f"{prefix}/{year}/{month}/{date_str}.csv.gz"
    local_path = os.path.join(local_dir, f"{date_str}.csv.gz")
    
    if os.path.exists(local_path):
        size_mb = os.path.getsize(local_path) / (1024*1024)
        print(f"  [SKIP] {date_str} ({file_type}) - exists ({size_mb:.1f} MB)")
        return True, size_mb
    
    try:
        print(f"  [DOWN] {date_str} ({file_type})...", end=" ", flush=True)
        s3.download_file(BUCKET, key, local_path)
        size_mb = os.path.getsize(local_path) / (1024*1024)
        print(f"{size_mb:.1f} MB")
        return True, size_mb
    except Exception as e:
        print(f"FAILED: {e}")
        return False, 0


def main():
    # Date range
    start_date = date(2025, 7, 1)   # Start of warm-up
    end_date = date(2026, 1, 28)    # End of detection period
    
    trading_days = get_trading_days(start_date, end_date)
    
    print("="*70)
    print("FLAT FILE DOWNLOAD - 6 MONTH BACKTEST")
    print("="*70)
    print(f"Period: {start_date} to {end_date}")
    print(f"Trading days: {len(trading_days)}")
    print(f"Estimated options size: ~{len(trading_days) * 55 / 1024:.1f} GB")
    print(f"Estimated stocks size: ~{len(trading_days) * 22 / 1024:.1f} GB")
    print()
    
    # Create directories
    os.makedirs(OPTIONS_DIR, exist_ok=True)
    os.makedirs(STOCKS_DIR, exist_ok=True)
    
    s3 = get_s3_client()
    
    # Download options
    print("--- OPTIONS TRADES ---")
    options_total = 0
    options_success = 0
    for d in trading_days:
        date_str = d.strftime("%Y-%m-%d")
        success, size = download_file(
            s3, "us_options_opra/trades_v1", date_str, OPTIONS_DIR, "options"
        )
        if success:
            options_success += 1
            options_total += size
    
    print(f"\nOptions: {options_success}/{len(trading_days)} files, {options_total/1024:.2f} GB")
    
    # Download stocks
    print("\n--- STOCK MINUTE BARS ---")
    stocks_total = 0
    stocks_success = 0
    for d in trading_days:
        date_str = d.strftime("%Y-%m-%d")
        success, size = download_file(
            s3, "us_stocks_sip/minute_aggs_v1", date_str, STOCKS_DIR, "stocks"
        )
        if success:
            stocks_success += 1
            stocks_total += size
    
    print(f"\nStocks: {stocks_success}/{len(trading_days)} files, {stocks_total/1024:.2f} GB")
    
    print("\n" + "="*70)
    print("DOWNLOAD COMPLETE")
    print("="*70)
    print(f"Total: {(options_total + stocks_total)/1024:.2f} GB")
    print(f"Options dir: {OPTIONS_DIR}")
    print(f"Stocks dir: {STOCKS_DIR}")


if __name__ == "__main__":
    main()
