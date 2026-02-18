"""
Multi-Day Firehose Backtest

Run detection across multiple days to understand:
1. How many signals fire daily
2. Which underlyings trigger repeatedly  
3. What the outcomes look like

Uses rolling baseline (previous day) for each day.
"""

import boto3
from botocore.config import Config
import gzip
import os
from datetime import datetime, timedelta
from collections import defaultdict
import json

# S3 Credentials
S3_ACCESS_KEY = "51df643a-56b5-4a2b-8427-09b81f1f0759"
S3_SECRET_KEY = "jm1TKQihT3V6rvIYWXsJ4hdOYAD1LMop"
S3_ENDPOINT = "https://files.massive.com"
BUCKET = "flatfiles"

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2"
DATA_DIR = os.path.join(BASE_DIR, "polygon_data")


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


def download_file(s3, date_str):
    """Download flat file for a specific date if not exists."""
    key = f'us_options_opra/trades_v1/2026/01/{date_str}.csv.gz'
    local_path = os.path.join(DATA_DIR, f'{date_str}.csv.gz')
    
    if os.path.exists(local_path):
        return local_path
    
    print(f"  Downloading {date_str}...")
    try:
        s3.download_file(BUCKET, key, local_path)
        size_mb = os.path.getsize(local_path) / (1024*1024)
        print(f"  Downloaded: {size_mb:.1f} MB")
        return local_path
    except Exception as e:
        print(f"  Download failed: {e}")
        return None


def parse_trade(parts, idx):
    """Parse a trade row into structured dict."""
    ticker = parts[idx['ticker']]
    if not ticker.startswith('O:'):
        return None
    
    # Extract underlying
    underlying = ''
    for i, c in enumerate(ticker[2:]):
        if c.isdigit():
            underlying = ticker[2:2+i]
            break
    
    if not underlying:
        return None
    
    # Determine call/put
    rest = ticker[2+len(underlying):]
    is_call = 'C' in rest[:7] if len(rest) >= 7 else False
    
    # Parse timestamp
    ts_ns = int(parts[idx['sip_timestamp']])
    ts_sec = ts_ns / 1e9
    dt = datetime.fromtimestamp(ts_sec)
    
    return {
        'time': dt,
        'underlying': underlying,
        'is_call': is_call,
        'size': int(parts[idx['size']]),
    }


def load_daily_volumes(filepath):
    """Load call volumes by underlying for a single day."""
    volumes = defaultdict(int)
    
    with gzip.open(filepath, 'rt') as f:
        header = f.readline().strip().split(',')
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(',')
            trade = parse_trade(parts, idx)
            if trade and trade['is_call']:
                volumes[trade['underlying']] += trade['size']
    
    return dict(volumes)


def run_single_day_detection(filepath, baseline, detection_threshold=2.0, min_volume=500):
    """
    Run detection on a single day, returning all signals with timing.
    
    Returns list of signals with:
    - underlying
    - detection_time
    - call_volume at detection
    - final_call_volume (end of day)
    - baseline
    - ratio
    """
    signals = {}  # underlying -> first signal
    running_volume = defaultdict(int)
    
    with gzip.open(filepath, 'rt') as f:
        header = f.readline().strip().split(',')
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(',')
            trade = parse_trade(parts, idx)
            
            if not trade or not trade['is_call']:
                continue
            
            underlying = trade['underlying']
            running_volume[underlying] += trade['size']
            
            # Check for signal (first time only per underlying)
            if underlying not in signals:
                vol = running_volume[underlying]
                base = baseline.get(underlying, 0)
                
                if base > 0:
                    ratio = vol / base
                    if ratio >= detection_threshold and vol >= min_volume:
                        signals[underlying] = {
                            'underlying': underlying,
                            'detection_time': trade['time'],
                            'volume_at_detection': vol,
                            'baseline': base,
                            'ratio': ratio,
                        }
    
    # Add final volumes
    for sig in signals.values():
        sig['final_volume'] = running_volume[sig['underlying']]
        sig['final_ratio'] = sig['final_volume'] / sig['baseline']
    
    return list(signals.values()), dict(running_volume)


def run_multi_day_backtest(dates):
    """
    Run backtest across multiple consecutive days.
    
    Args:
        dates: List of date strings ['2026-01-20', '2026-01-21', ...]
    """
    print("="*70)
    print("MULTI-DAY FIREHOSE BACKTEST")
    print("="*70)
    print(f"Dates: {dates[0]} to {dates[-1]}")
    print(f"Total days: {len(dates)}")
    
    os.makedirs(DATA_DIR, exist_ok=True)
    s3 = get_s3_client()
    
    # Download all needed files
    print("\n--- Downloading Files ---")
    files = {}
    for date in dates:
        path = download_file(s3, date)
        if path:
            files[date] = path
    
    print(f"\nFiles available: {len(files)}")
    
    # Run detection day by day
    all_signals = []
    daily_stats = []
    
    prev_volumes = None
    
    for date in sorted(files.keys()):
        print(f"\n--- Processing {date} ---")
        filepath = files[date]
        
        if prev_volumes is None:
            # First day - just load volumes for baseline
            print("  Loading as baseline (no detection)...")
            prev_volumes = load_daily_volumes(filepath)
            print(f"  Loaded {len(prev_volumes)} underlyings")
            continue
        
        # Run detection
        signals, day_volumes = run_single_day_detection(
            filepath, 
            prev_volumes,
            detection_threshold=2.0,
            min_volume=500
        )
        
        print(f"  Signals: {len(signals)}")
        
        # Categorize signals by time
        premarket = [s for s in signals if s['detection_time'].hour < 9 or 
                     (s['detection_time'].hour == 9 and s['detection_time'].minute < 30)]
        first_hour = [s for s in signals if 9 <= s['detection_time'].hour < 10 or
                      (s['detection_time'].hour == 10 and s['detection_time'].minute < 30)]
        later = [s for s in signals if s not in premarket and s not in first_hour]
        
        print(f"    Pre-market (<9:30): {len(premarket)}")
        print(f"    First hour (9:30-10:30): {len(first_hour)}")
        print(f"    Later (>10:30): {len(later)}")
        
        # Add date to signals
        for s in signals:
            s['date'] = date
        
        all_signals.extend(signals)
        
        daily_stats.append({
            'date': date,
            'total_signals': len(signals),
            'premarket': len(premarket),
            'first_hour': len(first_hour),
            'later': len(later),
            'unique_underlyings': len(day_volumes),
        })
        
        # Update baseline for next day
        prev_volumes = day_volumes
    
    return all_signals, daily_stats


def analyze_results(signals, daily_stats):
    """Analyze backtest results."""
    print("\n" + "="*70)
    print("BACKTEST ANALYSIS")
    print("="*70)
    
    print(f"\nTotal signals across all days: {len(signals)}")
    
    # Daily summary
    print("\n--- Daily Signal Counts ---")
    print(f"{'Date':12} {'Total':>8} {'PreMkt':>8} {'1stHr':>8} {'Later':>8}")
    print("-"*50)
    for day in daily_stats:
        print(f"{day['date']:12} {day['total_signals']:>8} {day['premarket']:>8} "
              f"{day['first_hour']:>8} {day['later']:>8}")
    
    avg_signals = sum(d['total_signals'] for d in daily_stats) / len(daily_stats)
    print(f"\nAverage signals per day: {avg_signals:.1f}")
    
    # Top repeated underlyings
    underlying_counts = defaultdict(int)
    for s in signals:
        underlying_counts[s['underlying']] += 1
    
    print("\n--- Most Frequent Signal Underlyings ---")
    print(f"{'Underlying':10} {'Days':>6} {'Avg Ratio':>10}")
    print("-"*30)
    
    sorted_underlyings = sorted(underlying_counts.items(), key=lambda x: x[1], reverse=True)
    for underlying, count in sorted_underlyings[:20]:
        # Get average ratio for this underlying
        ratios = [s['ratio'] for s in signals if s['underlying'] == underlying]
        avg_ratio = sum(ratios) / len(ratios)
        print(f"{underlying:10} {count:>6} {avg_ratio:>10.1f}x")
    
    # Extreme movers (highest ratios)
    print("\n--- Highest Ratio Signals (potential big movers) ---")
    print(f"{'Date':12} {'Underlying':10} {'Ratio':>8} {'Detection':>12} {'Final Vol':>12}")
    print("-"*60)
    
    sorted_by_ratio = sorted(signals, key=lambda x: x['ratio'], reverse=True)
    for s in sorted_by_ratio[:30]:
        det_time = s['detection_time'].strftime('%H:%M')
        print(f"{s['date']:12} {s['underlying']:10} {s['ratio']:>7.1f}x {det_time:>12} {s['final_volume']:>12,}")
    
    # Pre-market signals (most valuable)
    print("\n--- Pre-Market Signals (highest value) ---")
    premarket = [s for s in signals if s['detection_time'].hour < 9 or 
                 (s['detection_time'].hour == 9 and s['detection_time'].minute < 30)]
    
    print(f"Total pre-market signals: {len(premarket)}")
    print(f"\n{'Date':12} {'Underlying':10} {'Ratio':>8} {'Detection':>12} {'Final Vol':>12}")
    print("-"*60)
    
    sorted_premarket = sorted(premarket, key=lambda x: x['ratio'], reverse=True)
    for s in sorted_premarket[:30]:
        det_time = s['detection_time'].strftime('%H:%M')
        print(f"{s['date']:12} {s['underlying']:10} {s['ratio']:>7.1f}x {det_time:>12} {s['final_volume']:>12,}")
    
    return {
        'total_signals': len(signals),
        'avg_per_day': avg_signals,
        'premarket_count': len(premarket),
        'top_underlyings': dict(sorted_underlyings[:20]),
    }


def main():
    # Test dates - last two weeks of January 2026
    dates = [
        '2026-01-13', '2026-01-14', '2026-01-15', '2026-01-16',
        '2026-01-20', '2026-01-21', '2026-01-22', '2026-01-23',
        '2026-01-26', '2026-01-27', '2026-01-28'
    ]
    
    signals, daily_stats = run_multi_day_backtest(dates)
    summary = analyze_results(signals, daily_stats)
    
    # Save results
    output_file = os.path.join(DATA_DIR, 'multi_day_backtest.json')
    with open(output_file, 'w') as f:
        output = {
            'signals': [
                {**s, 'detection_time': s['detection_time'].isoformat()}
                for s in signals
            ],
            'daily_stats': daily_stats,
            'summary': summary,
        }
        json.dump(output, f, indent=2)
    
    print(f"\n\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
