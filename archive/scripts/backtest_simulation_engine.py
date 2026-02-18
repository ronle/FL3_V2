"""
Backtest Simulation Engine - Proof of Concept

Replay historical flat file data chronologically to simulate 
real-time detection of unusual options activity.

This emulates the production firehose pipeline but uses 
historical tick data for backtesting.
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

OUTPUT_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"


class BacktestSimulator:
    """
    Simulates the production firehose pipeline using historical flat file data.
    
    Key principle: Process trades in chronological order, exactly as they
    would arrive in a real-time stream.
    """
    
    def __init__(self, baseline_volumes=None):
        """
        Args:
            baseline_volumes: Dict of {underlying: avg_daily_call_volume}
                             This would come from ORATS 20-day averages in production.
        """
        # Running aggregates (simulating real-time state)
        self.call_volume = defaultdict(int)
        self.put_volume = defaultdict(int)
        self.trade_count = defaultdict(int)
        
        # Baseline volumes (from ORATS - backward-looking, no bias)
        self.baseline = baseline_volumes or {}
        
        # Detection parameters
        self.detection_threshold = 2.0  # 2x average = unusual
        self.min_volume_threshold = 500  # Minimum volume to consider
        
        # Signals generated
        self.signals = []
        
        # Current simulation time
        self.current_time = None
        self.last_check_time = None
        
    def process_trade(self, trade):
        """
        Process a single trade as it arrives in the stream.
        
        Args:
            trade: Dict with keys: time, underlying, is_call, size
        """
        underlying = trade['underlying']
        size = trade['size']
        
        # Update running totals
        if trade['is_call']:
            self.call_volume[underlying] += size
        else:
            self.put_volume[underlying] += size
        self.trade_count[underlying] += 1
        
        # Update current time
        self.current_time = trade['time']
        
    def check_signals(self, interval_minutes=1):
        """
        Check for unusual activity signals.
        Called periodically (e.g., every minute).
        """
        if self.last_check_time and (self.current_time - self.last_check_time).seconds < interval_minutes * 60:
            return []
        
        self.last_check_time = self.current_time
        new_signals = []
        
        for underlying, call_vol in self.call_volume.items():
            # Get baseline (from ORATS)
            baseline_avg = self.baseline.get(underlying, 0)
            
            if baseline_avg <= 0:
                # No baseline - use absolute threshold
                if call_vol >= self.min_volume_threshold:
                    # This is a discovery - no baseline but high volume
                    ratio = None
                    signal = {
                        'time': self.current_time,
                        'underlying': underlying,
                        'call_volume': call_vol,
                        'baseline': None,
                        'ratio': None,
                        'signal_type': 'HIGH_ABSOLUTE_VOLUME',
                    }
                    new_signals.append(signal)
            else:
                # Compare to baseline
                ratio = call_vol / baseline_avg
                
                if ratio >= self.detection_threshold and call_vol >= self.min_volume_threshold:
                    signal = {
                        'time': self.current_time,
                        'underlying': underlying,
                        'call_volume': call_vol,
                        'baseline': baseline_avg,
                        'ratio': ratio,
                        'signal_type': 'UNUSUAL_RATIO',
                    }
                    
                    # Only signal once per underlying per day
                    already_signaled = any(s['underlying'] == underlying for s in self.signals)
                    if not already_signaled:
                        new_signals.append(signal)
                        self.signals.append(signal)
        
        return new_signals


def parse_trade_from_row(parts, idx):
    """Parse a single trade from CSV row."""
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
        'price': float(parts[idx['price']]),
        'ticker': ticker,
    }


def load_baseline_from_previous_day(prev_day_path):
    """
    Load baseline volumes from previous day's flat file.
    
    In production, this would come from ORATS 20-day averages.
    For simplicity, we use previous day as baseline.
    """
    print(f"\nLoading baseline from: {prev_day_path}")
    
    call_volumes = defaultdict(int)
    
    with gzip.open(prev_day_path, 'rt') as f:
        header = f.readline().strip().split(',')
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(',')
            trade = parse_trade_from_row(parts, idx)
            
            if trade and trade['is_call']:
                call_volumes[trade['underlying']] += trade['size']
    
    print(f"Loaded baseline for {len(call_volumes)} underlyings")
    
    # Return average (since this is just 1 day, it's the total)
    # In reality, you'd divide by 20 for a 20-day average
    return dict(call_volumes)


def run_simulation(target_day_path, baseline_volumes):
    """
    Run the backtest simulation on a target day.
    
    Args:
        target_day_path: Path to the target day's flat file
        baseline_volumes: Dict of baseline call volumes by underlying
    """
    print(f"\n{'='*70}")
    print("RUNNING BACKTEST SIMULATION")
    print('='*70)
    print(f"Target file: {target_day_path}")
    print(f"Baselines loaded: {len(baseline_volumes)}")
    
    # Initialize simulator
    sim = BacktestSimulator(baseline_volumes)
    
    trades_processed = 0
    signals_found = []
    
    # Open and process chronologically
    print("\nProcessing trades chronologically...")
    
    with gzip.open(target_day_path, 'rt') as f:
        header = f.readline().strip().split(',')
        idx = {col: i for i, col in enumerate(header)}
        
        # Collect trades and sort by time
        trades = []
        for line in f:
            parts = line.strip().split(',')
            trade = parse_trade_from_row(parts, idx)
            if trade:
                trades.append(trade)
        
        print(f"Loaded {len(trades):,} trades, sorting by timestamp...")
        trades.sort(key=lambda x: x['time'])
        
        # Process in order
        last_progress = None
        
        for trade in trades:
            trades_processed += 1
            
            # Process the trade
            sim.process_trade(trade)
            
            # Check for signals every minute
            new_signals = sim.check_signals(interval_minutes=1)
            
            for signal in new_signals:
                signals_found.append(signal)
                print(f"\n*** SIGNAL at {signal['time'].strftime('%H:%M:%S')}: "
                      f"{signal['underlying']} - {signal['signal_type']}")
                print(f"   Call volume: {signal['call_volume']:,}")
                if signal['ratio']:
                    print(f"   Ratio vs baseline: {signal['ratio']:.1f}x")
            
            # Progress update every 500k trades
            if trades_processed % 500000 == 0:
                print(f"  Processed {trades_processed:,} trades, time: {trade['time'].strftime('%H:%M')}")
    
    print(f"\n{'='*70}")
    print("SIMULATION COMPLETE")
    print('='*70)
    print(f"Trades processed: {trades_processed:,}")
    print(f"Signals generated: {len(signals_found)}")
    
    # Show all signals sorted by time
    print(f"\n{'='*70}")
    print("ALL SIGNALS (sorted by time)")
    print('='*70)
    print(f"{'Time':12} {'Underlying':8} {'Call Vol':>12} {'Baseline':>12} {'Ratio':>8}")
    print('-'*60)
    
    signals_found.sort(key=lambda x: x['time'])
    
    for signal in signals_found[:50]:  # First 50
        ratio_str = f"{signal['ratio']:.1f}x" if signal['ratio'] else "N/A"
        baseline_str = f"{signal['baseline']:,}" if signal['baseline'] else "N/A"
        print(f"{signal['time'].strftime('%H:%M:%S'):12} {signal['underlying']:8} "
              f"{signal['call_volume']:>12,} {baseline_str:>12} {ratio_str:>8}")
    
    # Check specifically for AAOI
    aaoi_signal = next((s for s in signals_found if s['underlying'] == 'AAOI'), None)
    
    print(f"\n{'='*70}")
    print("AAOI DETECTION ANALYSIS")
    print('='*70)
    
    if aaoi_signal:
        print(f"YES - AAOI DETECTED!")
        print(f"   Detection time: {aaoi_signal['time'].strftime('%H:%M:%S')}")
        print(f"   Call volume at detection: {aaoi_signal['call_volume']:,}")
        if aaoi_signal['ratio']:
            print(f"   Ratio vs baseline: {aaoi_signal['ratio']:.1f}x")
        
        # How early was the detection?
        market_open = aaoi_signal['time'].replace(hour=9, minute=30, second=0)
        time_before_open = market_open - aaoi_signal['time']
        if time_before_open.total_seconds() > 0:
            print(f"   >> Detected {time_before_open.seconds // 60} minutes BEFORE market open!")
    else:
        print("❌ AAOI not detected with current thresholds")
        print(f"   Final AAOI call volume: {sim.call_volume.get('AAOI', 0):,}")
        print(f"   AAOI baseline: {baseline_volumes.get('AAOI', 0):,}")
    
    return {
        'signals': signals_found,
        'aaoi_signal': aaoi_signal,
        'final_volumes': dict(sim.call_volume),
    }


def main():
    print("="*70)
    print("BACKTEST SIMULATION ENGINE - PROOF OF CONCEPT")
    print("="*70)
    print(f"Time: {datetime.now()}")
    
    # File paths - check both locations
    base_dir = "C:\\Users\\levir\\Documents\\FL3_V2"
    polygon_dir = os.path.join(base_dir, "polygon_data")
    
    # Jan 27 is in root, Jan 28 is in polygon_data
    prev_day_path = os.path.join(base_dir, '2026-01-27.csv.gz')
    target_day_path = os.path.join(polygon_dir, '2026-01-28.csv.gz')
    
    # Check files exist
    if not os.path.exists(prev_day_path):
        print(f"Missing baseline file: {prev_day_path}")
        print("Run the download script first!")
        return
    
    if not os.path.exists(target_day_path):
        print(f"Missing target file: {target_day_path}")
        print("Run the download script first!")
        return
    
    # Load baseline (previous day's volumes)
    baseline = load_baseline_from_previous_day(prev_day_path)
    
    # Run simulation
    results = run_simulation(target_day_path, baseline)
    
    # Save results
    output_file = os.path.join(OUTPUT_DIR, 'backtest_results.json')
    with open(output_file, 'w') as f:
        # Convert to serializable format
        output = {
            'signals': [
                {
                    **s,
                    'time': s['time'].isoformat()
                }
                for s in results['signals']
            ],
            'aaoi_detected': results['aaoi_signal'] is not None,
            'aaoi_detection_time': results['aaoi_signal']['time'].isoformat() if results['aaoi_signal'] else None,
        }
        json.dump(output, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
