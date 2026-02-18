"""
Generate Signals for Extended Period (Jan-Jun 2025)

For TEST-4b: 12-month adversarial validation.

This runs the e2e_backtest_v2 logic on the extended period
and saves signals that can be combined with existing data.

Usage:
    python scripts/generate_extended_signals.py
"""

import gzip
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date, time as dt_time, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

BASE_DIR = Path("C:/Users/levir/Documents/FL3_V2/polygon_data")
OPTIONS_DIR = BASE_DIR / "options"
STOCKS_DIR = BASE_DIR / "stocks"
OUTPUT_DIR = BASE_DIR / "backtest_results"

# Extended period
START_DATE = date(2025, 1, 2)
END_DATE = date(2025, 6, 30)

# Detection parameters
BUCKET_MINUTES = 30
BASELINE_LOOKBACK_DAYS = 20
DETECTION_THRESHOLD = 3.0
MIN_NOTIONAL = 10000
COOLDOWN_MINUTES = 60

# OCC regex
OCC_REGEX = re.compile(r"O:([A-Z]+)(\d{6})([CP])(\d{8})")

TIME_MULTIPLIERS = {
    "09:30": 3.0, "10:00": 1.8, "10:30": 1.4,
    "11:00": 1.1, "11:30": 0.8, "12:00": 0.6,
    "12:30": 0.5, "13:00": 0.6, "13:30": 0.8,
    "14:00": 1.0, "14:30": 1.1, "15:00": 1.3,
    "15:30": 2.0,
}


def parse_occ(symbol: str) -> Optional[dict]:
    """Parse OCC option symbol."""
    match = OCC_REGEX.match(symbol)
    if not match:
        return None

    return {
        "underlying": match.group(1),
        "expiry": f"20{match.group(2)[:2]}-{match.group(2)[2:4]}-{match.group(2)[4:6]}",
        "right": "call" if match.group(3) == "C" else "put",
        "strike": int(match.group(4)) / 1000,
    }


def get_bucket_key(ts: datetime) -> str:
    """Get 30-min bucket key for timestamp."""
    minute = (ts.minute // 30) * 30
    return ts.replace(minute=minute, second=0, microsecond=0).strftime("%H:%M")


def load_options_day(trade_date: date) -> List[dict]:
    """Load options trades for a day."""
    file_path = OPTIONS_DIR / f"{trade_date.isoformat()}.csv.gz"
    if not file_path.exists():
        return []

    trades = []
    try:
        import pandas as pd
        df = pd.read_csv(file_path, compression='gzip')

        for _, row in df.iterrows():
            parsed = parse_occ(row.get('symbol', ''))
            if not parsed:
                continue

            trades.append({
                "symbol": row.get('symbol'),
                "underlying": parsed["underlying"],
                "right": parsed["right"],
                "strike": parsed["strike"],
                "expiry": parsed["expiry"],
                "price": float(row.get('price', 0)),
                "size": int(row.get('size', 0)),
                "timestamp": row.get('sip_timestamp', 0),
                "conditions": row.get('conditions', ''),
            })

    except Exception as e:
        print(f"Error loading {file_path}: {e}")

    return trades


def load_stock_bars(trade_date: date) -> Dict[str, dict]:
    """Load stock minute bars for a day, aggregate to daily."""
    file_path = STOCKS_DIR / f"{trade_date.isoformat()}.csv.gz"
    if not file_path.exists():
        return {}

    try:
        import pandas as pd
        df = pd.read_csv(file_path, compression='gzip')

        daily = {}
        for ticker, group in df.groupby('ticker'):
            daily[ticker] = {
                'o': float(group['open'].iloc[0]),
                'h': float(group['high'].max()),
                'l': float(group['low'].min()),
                'c': float(group['close'].iloc[-1]),
                'v': int(group['volume'].sum()),
            }

        return daily

    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return {}


def calculate_baselines(symbol: str, trade_date: date, history: Dict[date, Dict[str, dict]]) -> Dict[str, float]:
    """Calculate baseline notional per bucket for symbol."""
    baselines = defaultdict(list)

    # Look back up to 20 trading days
    d = trade_date - timedelta(days=1)
    days_counted = 0

    for _ in range(40):  # Max 40 calendar days
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue

        if d in history and symbol in history[d]:
            stats = history[d][symbol]
            for bucket, notional in stats.items():
                baselines[bucket].append(notional)
            days_counted += 1

            if days_counted >= BASELINE_LOOKBACK_DAYS:
                break

        d -= timedelta(days=1)

    # Average
    return {bucket: sum(vals)/len(vals) if vals else 0 for bucket, vals in baselines.items()}


def process_day(trade_date: date, baseline_history: Dict) -> List[dict]:
    """Process a single day and detect signals."""
    trades = load_options_day(trade_date)
    if not trades:
        return []

    # Group by underlying and bucket
    by_symbol_bucket = defaultdict(lambda: defaultdict(list))

    for trade in trades:
        ts = datetime.fromtimestamp(trade['timestamp'] / 1e9)
        bucket = get_bucket_key(ts)
        by_symbol_bucket[trade['underlying']][bucket].append(trade)

    signals = []
    cooldowns = {}

    # Process each symbol
    for symbol, buckets in by_symbol_bucket.items():
        baselines = calculate_baselines(symbol, trade_date, baseline_history)

        for bucket_key, bucket_trades in sorted(buckets.items()):
            # Skip if in cooldown
            if symbol in cooldowns:
                last_trigger = cooldowns[symbol]
                bucket_time = datetime.combine(trade_date, datetime.strptime(bucket_key, "%H:%M").time())
                if (bucket_time - last_trigger).total_seconds() < COOLDOWN_MINUTES * 60:
                    continue

            # Calculate bucket stats
            notional = sum(t['price'] * t['size'] * 100 for t in bucket_trades)
            contracts = sum(t['size'] for t in bucket_trades)
            call_notional = sum(t['price'] * t['size'] * 100 for t in bucket_trades if t['right'] == 'call')

            # Get baseline
            time_mult = TIME_MULTIPLIERS.get(bucket_key, 1.0)
            baseline = baselines.get(bucket_key, 50000) * time_mult
            if baseline < MIN_NOTIONAL:
                baseline = MIN_NOTIONAL

            # Check threshold
            ratio = notional / baseline if baseline > 0 else 0
            if ratio < DETECTION_THRESHOLD:
                continue

            if notional < MIN_NOTIONAL:
                continue

            # This is a trigger!
            detection_time = datetime.combine(trade_date, datetime.strptime(bucket_key, "%H:%M").time())
            detection_time = detection_time + timedelta(minutes=15)  # Mid-bucket

            # Calculate score (simplified)
            score = 0
            if ratio >= 5: score += 5
            elif ratio >= 4: score += 4
            elif ratio >= 3: score += 3

            call_pct = call_notional / notional if notional > 0 else 0.5
            if call_pct >= 0.8: score += 3
            elif call_pct >= 0.7: score += 2

            # Sweep bonus (check conditions for '209')
            sweep_count = sum(1 for t in bucket_trades if '209' in str(t.get('conditions', '')))
            sweep_pct = sweep_count / len(bucket_trades) if bucket_trades else 0
            if sweep_pct >= 0.3: score += 2

            signals.append({
                'symbol': symbol,
                'detection_time': detection_time.isoformat(),
                'score': score,
                'notional': notional,
                'contracts': contracts,
                'call_pct': round(call_pct, 3),
                'volume_ratio': round(ratio, 2),
                'baseline': baseline,
            })

            cooldowns[symbol] = detection_time

    return signals


def main():
    print("=" * 60)
    print("Extended Signal Generation (Jan-Jun 2025)")
    print("=" * 60)

    # Get list of dates
    dates = []
    d = START_DATE
    while d <= END_DATE:
        if d.weekday() < 5:  # Weekday
            file_path = OPTIONS_DIR / f"{d.isoformat()}.csv.gz"
            if file_path.exists():
                dates.append(d)
        d += timedelta(days=1)

    print(f"Found {len(dates)} trading days with data")

    if len(dates) == 0:
        print("No data files found for extended period")
        print("Make sure options data is downloaded for Jan-Jun 2025")
        return

    # Build baseline history as we go
    baseline_history: Dict[date, Dict[str, Dict[str, float]]] = {}
    all_signals = []

    for i, trade_date in enumerate(dates):
        signals = process_day(trade_date, baseline_history)

        # Update baseline history with today's data
        trades = load_options_day(trade_date)
        day_stats = defaultdict(lambda: defaultdict(float))
        for trade in trades:
            bucket = get_bucket_key(datetime.fromtimestamp(trade['timestamp'] / 1e9))
            notional = trade['price'] * trade['size'] * 100
            day_stats[trade['underlying']][bucket] += notional
        baseline_history[trade_date] = dict(day_stats)

        all_signals.extend(signals)

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(dates)} days... ({len(all_signals):,} signals)")

    print(f"\nTotal signals: {len(all_signals):,}")

    # Save
    output_file = OUTPUT_DIR / "extended_signals_jan_jun_2025.json"
    output_data = {
        "period": f"{START_DATE.isoformat()} to {END_DATE.isoformat()}",
        "trading_days": len(dates),
        "total_signals": len(all_signals),
        "signals": all_signals,
    }

    with open(output_file, 'w') as f:
        json.dump(output_data, f)

    print(f"Saved to: {output_file}")

    # Summary stats
    if all_signals:
        avg_score = sum(s['score'] for s in all_signals) / len(all_signals)
        high_score = len([s for s in all_signals if s['score'] >= 10])
        print(f"\nStats:")
        print(f"  Avg score: {avg_score:.1f}")
        print(f"  Score >= 10: {high_score:,} ({high_score/len(all_signals)*100:.1f}%)")


if __name__ == "__main__":
    main()
