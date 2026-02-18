"""
Download extended data for 12-month backtest.

New period: January 2025 - June 2025 (6 additional months)
Combined with existing July 2025 - January 2026 = 12 months total

Options trades: us_options_opra/trades_v1
Stock minute bars: us_stocks_sip/minute_aggs_v1
"""

import boto3
from botocore.config import Config
import os
import argparse
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
    holidays = {
        # 2025 holidays
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
    parser = argparse.ArgumentParser(description='Download extended backtest data')
    parser.add_argument('--start', default='2025-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', default='2025-06-30', help='End date (YYYY-MM-DD)')
    parser.add_argument('--options-only', action='store_true', help='Only download options')
    parser.add_argument('--stocks-only', action='store_true', help='Only download stocks')
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)

    trading_days = get_trading_days(start_date, end_date)

    print("="*70)
    print("EXTENDED DATA DOWNLOAD")
    print("="*70)
    print(f"Period: {start_date} to {end_date}")
    print(f"Trading days: {len(trading_days)}")
    print(f"Estimated options size: ~{len(trading_days) * 55 / 1024:.1f} GB")
    print(f"Estimated stocks size: ~{len(trading_days) * 22 / 1024:.1f} GB")
    print()

    os.makedirs(OPTIONS_DIR, exist_ok=True)
    os.makedirs(STOCKS_DIR, exist_ok=True)

    s3 = get_s3_client()

    # Download options
    if not args.stocks_only:
        print("\n--- OPTIONS TRADES ---")
        options_total = 0
        options_count = 0
        for d in trading_days:
            date_str = d.isoformat()
            success, size = download_file(
                s3,
                "us_options_opra/trades_v1",
                date_str,
                OPTIONS_DIR,
                "options"
            )
            if success:
                options_total += size
                options_count += 1

        print(f"\nOptions: {options_count}/{len(trading_days)} files, {options_total/1024:.2f} GB")

    # Download stocks
    if not args.options_only:
        print("\n--- STOCK MINUTE BARS ---")
        stocks_total = 0
        stocks_count = 0
        for d in trading_days:
            date_str = d.isoformat()
            success, size = download_file(
                s3,
                "us_stocks_sip/minute_aggs_v1",
                date_str,
                STOCKS_DIR,
                "stocks"
            )
            if success:
                stocks_total += size
                stocks_count += 1

        print(f"\nStocks: {stocks_count}/{len(trading_days)} files, {stocks_total/1024:.2f} GB")

    print("\n" + "="*70)
    print("DOWNLOAD COMPLETE")
    print("="*70)


if __name__ == "__main__":
    main()
