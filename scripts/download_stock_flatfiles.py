"""
Download Polygon Flat Files — Stock Minute Bars (3 years)

Downloads daily csv.gz files from Polygon's S3-compatible flat file storage.
Skips files that already exist locally. Handles pagination and retries.

Usage:
    python -m scripts.download_stock_flatfiles                  # Download all missing
    python -m scripts.download_stock_flatfiles --discover       # List available prefixes
    python -m scripts.download_stock_flatfiles --dry-run        # Show what would download
    python -m scripts.download_stock_flatfiles --year 2023      # Only download 2023
"""

import boto3
from botocore.config import Config
import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta

# ── S3 config ────────────────────────────────────────────────────────
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

# ── Local paths ──────────────────────────────────────────────────────
OUTPUT_DIR = r"C:\Users\levir\Documents\FL3_V2\polygon_data\stocks"

# ── S3 prefix — will be confirmed via discovery ──────────────────────
# Minute aggs based on existing file schema (OHLCV + transactions)
STOCKS_PREFIX = "us_stocks_sip/minute_aggs_v1"

# ── Date range ───────────────────────────────────────────────────────
START_DATE = date(2023, 1, 1)
END_DATE = date.today()


def get_s3_client():
    session = boto3.Session(
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
    )
    return session.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        config=Config(signature_version="s3v4"),
    )


def discover_prefixes(s3):
    """List top-level prefixes in the bucket to find stock data paths."""
    print("Discovering available data sets...\n")

    # List root prefixes
    resp = s3.list_objects_v2(Bucket=BUCKET, Delimiter="/", MaxKeys=100)
    top_prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    print(f"Top-level prefixes ({len(top_prefixes)}):")
    for p in sorted(top_prefixes):
        print(f"  {p}")

    # Drill into us_stocks_sip
    stock_prefixes = [p for p in top_prefixes if "stocks" in p.lower()]
    for sp in stock_prefixes:
        print(f"\nSub-prefixes under {sp}:")
        resp2 = s3.list_objects_v2(Bucket=BUCKET, Prefix=sp, Delimiter="/", MaxKeys=100)
        for p in resp2.get("CommonPrefixes", []):
            print(f"  {p['Prefix']}")

            # Show year dirs for the first sub-prefix
            resp3 = s3.list_objects_v2(
                Bucket=BUCKET, Prefix=p["Prefix"], Delimiter="/", MaxKeys=20
            )
            for y in resp3.get("CommonPrefixes", []):
                print(f"    {y['Prefix']}")


def list_remote_files(s3, year: int, month: int) -> list[dict]:
    """List all files for a given year/month."""
    prefix = f"{STOCKS_PREFIX}/{year}/{month:02d}/"
    files = []
    continuation = None

    while True:
        kwargs = dict(Bucket=BUCKET, Prefix=prefix, MaxKeys=1000)
        if continuation:
            kwargs["ContinuationToken"] = continuation
        resp = s3.list_objects_v2(**kwargs)

        for obj in resp.get("Contents", []):
            files.append({"key": obj["Key"], "size": obj["Size"]})

        if resp.get("IsTruncated"):
            continuation = resp["NextContinuationToken"]
        else:
            break

    return files


def get_existing_files() -> set[str]:
    """Get set of filenames already downloaded."""
    if not os.path.isdir(OUTPUT_DIR):
        return set()
    return {f for f in os.listdir(OUTPUT_DIR) if f.endswith(".csv.gz")}


def download_file(s3, key: str, local_path: str, retries: int = 3) -> bool:
    """Download a single file with retries."""
    for attempt in range(1, retries + 1):
        try:
            s3.download_file(BUCKET, key, local_path)
            return True
        except Exception as e:
            if attempt < retries:
                wait = 2 ** attempt
                print(f"    Retry {attempt}/{retries} in {wait}s: {e}")
                time.sleep(wait)
            else:
                print(f"    FAILED after {retries} attempts: {e}")
                return False
    return False


def run_download(s3, *, dry_run: bool = False, year_filter: int | None = None):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    existing = get_existing_files()
    print(f"Existing local files: {len(existing)}")
    print(f"Output dir: {OUTPUT_DIR}")
    print(f"S3 prefix: {STOCKS_PREFIX}")
    print(f"Date range: {START_DATE} -> {END_DATE}")
    if year_filter:
        print(f"Year filter: {year_filter}")
    print()

    # Build list of year/month combos to scan
    months_to_scan = []
    d = START_DATE.replace(day=1)
    end_month = END_DATE.replace(day=1)
    while d <= end_month:
        if year_filter is None or d.year == year_filter:
            months_to_scan.append((d.year, d.month))
        d = (d + timedelta(days=32)).replace(day=1)

    total_to_download = 0
    total_downloaded = 0
    total_skipped = 0
    total_bytes = 0
    failed = []

    for i, (year, month) in enumerate(months_to_scan):
        label = f"[{i+1}/{len(months_to_scan)}] {year}-{month:02d}"
        print(f"{label}: listing remote files...", end=" ", flush=True)

        remote_files = list_remote_files(s3, year, month)
        print(f"{len(remote_files)} files")

        for rf in remote_files:
            filename = os.path.basename(rf["key"])
            size_mb = rf["size"] / (1024 * 1024)

            if filename in existing:
                total_skipped += 1
                continue

            total_to_download += 1

            if dry_run:
                print(f"  [DRY RUN] Would download: {filename} ({size_mb:.1f} MB)")
                continue

            local_path = os.path.join(OUTPUT_DIR, filename)
            print(f"  Downloading {filename} ({size_mb:.1f} MB)...", end=" ", flush=True)

            t0 = time.time()
            ok = download_file(s3, rf["key"], local_path)
            elapsed = time.time() - t0

            if ok:
                actual_size = os.path.getsize(local_path)
                speed = actual_size / elapsed / (1024 * 1024) if elapsed > 0 else 0
                total_downloaded += 1
                total_bytes += actual_size
                print(f"OK ({elapsed:.1f}s, {speed:.1f} MB/s)")
            else:
                failed.append(filename)
                # Clean up partial file
                if os.path.exists(local_path):
                    os.remove(local_path)

    # Summary
    print(f"\n{'='*60}")
    print("DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"Scanned months:  {len(months_to_scan)}")
    print(f"Already existed:  {total_skipped}")
    print(f"Downloaded:       {total_downloaded}")
    print(f"Total size:       {total_bytes / (1024**3):.2f} GB")
    if failed:
        print(f"Failed ({len(failed)}):")
        for f in failed:
            print(f"  {f}")
    if dry_run and total_to_download:
        print(f"\n[DRY RUN] Would download {total_to_download} files")
    print()


def main():
    parser = argparse.ArgumentParser(description="Download Polygon stock minute bars")
    parser.add_argument("--discover", action="store_true", help="List available S3 prefixes")
    parser.add_argument("--dry-run", action="store_true", help="Show what would download")
    parser.add_argument("--year", type=int, help="Only download a specific year")
    args = parser.parse_args()

    print("=" * 60)
    print("POLYGON FLAT FILES — STOCK MINUTE BARS DOWNLOADER")
    print("=" * 60)
    print(f"Time: {datetime.now()}")
    print()

    s3 = get_s3_client()

    if args.discover:
        discover_prefixes(s3)
    else:
        run_download(s3, dry_run=args.dry_run, year_filter=args.year)


if __name__ == "__main__":
    main()
