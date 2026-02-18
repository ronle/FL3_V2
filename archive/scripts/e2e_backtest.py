"""
E2E Backtest Engine - Production Logic Replica

Replicates the full production detection pipeline:
1. BucketAggregator - 30-min buckets (prints, notional, contracts)
2. BaselineManager - 20-day rolling bucket averages + time multipliers
3. UOADetector - 3x threshold detection with cooldown

Phases:
1. Warm-up: First 20 trading days fill buckets (no signals)
2. Detection: Remaining days generate signals with proper baselines

Usage:
    python e2e_backtest.py
"""

import gzip
import os
from datetime import datetime, date, time as dt_time, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import json

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"
OPTIONS_DIR = os.path.join(BASE_DIR, "options")
STOCKS_DIR = os.path.join(BASE_DIR, "stocks")
OUTPUT_DIR = os.path.join(BASE_DIR, "backtest_results")

# Detection parameters (match production)
BUCKET_MINUTES = 30
BASELINE_LOOKBACK_DAYS = 20
DETECTION_THRESHOLD = 3.0  # 3x baseline
MIN_NOTIONAL = 10000       # $10K minimum to trigger
COOLDOWN_MINUTES = 60      # 1 hour cooldown per symbol

# Liquidity filters (from config/filters.json)
MIN_STOCK_PRICE = 5.00     # Filter penny stocks
ENABLE_FILTERS = True      # Set False to disable filters for comparison

# Time-of-day multipliers (U-shaped intraday pattern)
TIME_MULTIPLIERS = {
    "04:00": 0.3, "04:30": 0.3,
    "05:00": 0.4, "05:30": 0.4,
    "06:00": 0.5, "06:30": 0.6,
    "07:00": 0.8, "07:30": 1.0,
    "08:00": 1.5, "08:30": 2.0,
    "09:00": 2.5, "09:30": 3.0,
    "10:00": 1.8, "10:30": 1.4,
    "11:00": 1.1, "11:30": 0.8,
    "12:00": 0.6, "12:30": 0.5,
    "13:00": 0.6, "13:30": 0.8,
    "14:00": 1.0, "14:30": 1.1,
    "15:00": 1.3, "15:30": 2.0,
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class BucketStats:
    """Stats for a single 30-minute bucket."""
    symbol: str
    trade_date: date
    bucket_start: dt_time
    prints: int = 0
    notional: float = 0.0
    contracts: int = 0
    call_contracts: int = 0
    put_contracts: int = 0
    unique_options: set = field(default_factory=set)


@dataclass 
class Signal:
    """UOA detection signal."""
    symbol: str
    detection_time: datetime
    bucket_start: dt_time
    notional: float
    baseline_notional: float
    ratio: float
    contracts: int
    prints: int
    call_pct: float
    confidence: float
    baseline_source: str
    
    # Filtering metadata (populated during outcome labeling)
    stock_price: Optional[float] = None
    filtered_out: bool = False
    filter_reason: Optional[str] = None


@dataclass
class Trade:
    """Parsed options trade."""
    timestamp: datetime
    underlying: str
    option_symbol: str
    is_call: bool
    price: float
    size: int
    
    @property
    def notional(self) -> float:
        return self.price * self.size * 100


# =============================================================================
# BUCKET AGGREGATOR
# =============================================================================

class BucketAggregator:
    """
    Aggregates trades into 30-minute buckets.
    Stores historical buckets for baseline calculation.
    """
    
    def __init__(self):
        # Current bucket being filled: symbol -> BucketStats
        self.current_buckets: Dict[str, BucketStats] = {}
        self.current_bucket_start: Optional[dt_time] = None
        self.current_date: Optional[date] = None
        
        # Historical buckets: symbol -> bucket_key -> list of BucketStats
        # bucket_key = "HH:MM"
        self.history: Dict[str, Dict[str, List[BucketStats]]] = defaultdict(lambda: defaultdict(list))
        
        # Metrics
        self.total_trades = 0
        self.total_buckets_stored = 0
    
    def _get_bucket_start(self, dt: datetime) -> dt_time:
        """Get bucket start time for a datetime."""
        minute = (dt.minute // BUCKET_MINUTES) * BUCKET_MINUTES
        return dt_time(dt.hour, minute)
    
    def _bucket_key(self, bucket_start: dt_time) -> str:
        """Convert bucket time to string key."""
        return bucket_start.strftime("%H:%M")
    
    def add_trade(self, trade: Trade) -> Optional[Dict[str, BucketStats]]:
        """
        Add a trade to current bucket.
        Returns completed buckets if we crossed a boundary.
        """
        self.total_trades += 1
        
        bucket_start = self._get_bucket_start(trade.timestamp)
        trade_date = trade.timestamp.date()
        
        # Check for bucket boundary
        completed_buckets = None
        if (self.current_bucket_start is not None and 
            (bucket_start != self.current_bucket_start or trade_date != self.current_date)):
            # Save completed buckets to history
            completed_buckets = self._flush_buckets()
        
        # Update tracking
        self.current_bucket_start = bucket_start
        self.current_date = trade_date
        
        # Initialize bucket if needed
        if trade.underlying not in self.current_buckets:
            self.current_buckets[trade.underlying] = BucketStats(
                symbol=trade.underlying,
                trade_date=trade_date,
                bucket_start=bucket_start,
            )
        
        # Accumulate stats
        bucket = self.current_buckets[trade.underlying]
        bucket.prints += 1
        bucket.notional += trade.notional
        bucket.contracts += trade.size
        if trade.is_call:
            bucket.call_contracts += trade.size
        else:
            bucket.put_contracts += trade.size
        bucket.unique_options.add(trade.option_symbol)
        
        return completed_buckets
    
    def _flush_buckets(self) -> Dict[str, BucketStats]:
        """Flush current buckets to history and return them."""
        if not self.current_buckets:
            return {}
        
        completed = dict(self.current_buckets)
        bucket_key = self._bucket_key(self.current_bucket_start)
        
        # Store in history (keep last BASELINE_LOOKBACK_DAYS worth)
        for symbol, stats in completed.items():
            history_list = self.history[symbol][bucket_key]
            history_list.append(stats)
            
            # Trim to lookback period
            if len(history_list) > BASELINE_LOOKBACK_DAYS:
                history_list.pop(0)
            
            self.total_buckets_stored += 1
        
        self.current_buckets = {}
        return completed
    
    def get_baseline(self, symbol: str, bucket_start: dt_time) -> tuple[float, float, str]:
        """
        Get baseline notional for symbol at bucket time.
        
        Returns: (baseline_notional, confidence, source)
        """
        bucket_key = self._bucket_key(bucket_start)
        history_list = self.history.get(symbol, {}).get(bucket_key, [])
        
        if len(history_list) >= 5:
            # Use historical average
            avg_notional = sum(b.notional for b in history_list) / len(history_list)
            confidence = min(len(history_list) / BASELINE_LOOKBACK_DAYS, 1.0)
            return avg_notional, confidence, "history"
        
        # Fallback: use time multiplier with default
        multiplier = TIME_MULTIPLIERS.get(bucket_key, 1.0)
        default_baseline = 10000 * multiplier  # $10K base × multiplier
        return default_baseline, 0.1, "default"
    
    def flush_end_of_day(self) -> Dict[str, BucketStats]:
        """Flush any remaining buckets at end of day."""
        return self._flush_buckets()
    
    def get_metrics(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "total_buckets_stored": self.total_buckets_stored,
            "symbols_with_history": len(self.history),
            "current_bucket_symbols": len(self.current_buckets),
        }


# =============================================================================
# UOA DETECTOR
# =============================================================================

class UOADetector:
    """
    Detects unusual options activity.
    Compares current bucket notional to baseline.
    """
    
    def __init__(self, aggregator: BucketAggregator):
        self.aggregator = aggregator
        self.cooldowns: Dict[str, datetime] = {}  # symbol -> last trigger time
        self.signals: List[Signal] = []
        self.checks = 0
        self.triggers = 0
    
    def check_bucket(self, bucket: BucketStats) -> Optional[Signal]:
        """
        Check if a completed bucket triggers UOA.
        """
        self.checks += 1
        
        # Skip if below minimum notional
        if bucket.notional < MIN_NOTIONAL:
            return None
        
        # Check cooldown
        if bucket.symbol in self.cooldowns:
            last_trigger = self.cooldowns[bucket.symbol]
            elapsed = datetime.combine(bucket.trade_date, bucket.bucket_start) - last_trigger
            if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
                return None
        
        # Get baseline
        baseline, confidence, source = self.aggregator.get_baseline(
            bucket.symbol, bucket.bucket_start
        )
        
        if baseline <= 0:
            return None
        
        # Calculate ratio
        ratio = bucket.notional / baseline
        
        # Check threshold
        if ratio >= DETECTION_THRESHOLD:
            trigger_time = datetime.combine(bucket.trade_date, bucket.bucket_start)
            
            # Calculate call percentage
            total_contracts = bucket.call_contracts + bucket.put_contracts
            call_pct = bucket.call_contracts / total_contracts if total_contracts > 0 else 0.5
            
            signal = Signal(
                symbol=bucket.symbol,
                detection_time=trigger_time,
                bucket_start=bucket.bucket_start,
                notional=bucket.notional,
                baseline_notional=baseline,
                ratio=ratio,
                contracts=bucket.contracts,
                prints=bucket.prints,
                call_pct=call_pct,
                confidence=confidence,
                baseline_source=source,
            )
            
            # Record cooldown
            self.cooldowns[bucket.symbol] = trigger_time
            self.signals.append(signal)
            self.triggers += 1
            
            return signal
        
        return None
    
    def get_metrics(self) -> dict:
        return {
            "checks": self.checks,
            "triggers": self.triggers,
            "trigger_rate": self.triggers / self.checks if self.checks > 0 else 0,
            "symbols_in_cooldown": len(self.cooldowns),
        }


# =============================================================================
# TRADE PARSER
# =============================================================================

def parse_option_symbol(ticker: str) -> Optional[tuple[str, bool]]:
    """
    Parse OCC option symbol to extract underlying and call/put.
    
    Format: O:AAPL260220C00150000
            O:{underlying}{YYMMDD}{C/P}{strike}
    
    Returns: (underlying, is_call) or None
    """
    if not ticker.startswith("O:"):
        return None
    
    # Extract underlying (letters before first digit after O:)
    rest = ticker[2:]
    underlying = ""
    for i, c in enumerate(rest):
        if c.isdigit():
            underlying = rest[:i]
            break
    
    if not underlying or len(underlying) > 5:
        return None
    
    # Find C or P after the date (6 digits)
    date_start = len(underlying)
    if len(rest) < date_start + 7:
        return None
    
    cp_char = rest[date_start + 6]
    is_call = cp_char == "C"
    
    return underlying, is_call


def parse_trades_file(filepath: str):
    """
    Generator that yields Trade objects from a gzipped flat file.
    """
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            
            ticker = parts[idx["ticker"]]
            parsed = parse_option_symbol(ticker)
            if not parsed:
                continue
            
            underlying, is_call = parsed
            
            # Parse timestamp (nanoseconds)
            ts_ns = int(parts[idx["sip_timestamp"]])
            ts_sec = ts_ns / 1e9
            dt = datetime.fromtimestamp(ts_sec)
            
            yield Trade(
                timestamp=dt,
                underlying=underlying,
                option_symbol=ticker,
                is_call=is_call,
                price=float(parts[idx["price"]]),
                size=int(parts[idx["size"]]),
            )


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

class BacktestEngine:
    """
    E2E backtest engine that replays historical data through production logic.
    """
    
    def __init__(self, warmup_days: int = BASELINE_LOOKBACK_DAYS):
        self.warmup_days = warmup_days
        self.aggregator = BucketAggregator()
        self.detector = UOADetector(self.aggregator)
        
        self.days_processed = 0
        self.warmup_complete = False
        self.daily_stats = []
    
    def process_day(self, filepath: str, trade_date: date) -> List[Signal]:
        """
        Process a single day's trades.
        
        Returns signals (empty during warmup).
        """
        print(f"  Processing {trade_date}...", end=" ", flush=True)
        
        day_signals = []
        trade_count = 0
        
        for trade in parse_trades_file(filepath):
            trade_count += 1
            
            # Add trade to aggregator
            completed_buckets = self.aggregator.add_trade(trade)
            
            # Check completed buckets for signals (if warmup complete)
            if completed_buckets and self.warmup_complete:
                for symbol, bucket in completed_buckets.items():
                    signal = self.detector.check_bucket(bucket)
                    if signal:
                        day_signals.append(signal)
        
        # Flush end of day
        final_buckets = self.aggregator.flush_end_of_day()
        if final_buckets and self.warmup_complete:
            for symbol, bucket in final_buckets.items():
                signal = self.detector.check_bucket(bucket)
                if signal:
                    day_signals.append(signal)
        
        self.days_processed += 1
        
        # Check if warmup complete
        if not self.warmup_complete and self.days_processed >= self.warmup_days:
            self.warmup_complete = True
            print(f"{trade_count:,} trades [WARMUP COMPLETE]")
        else:
            status = "WARMUP" if not self.warmup_complete else f"{len(day_signals)} signals"
            print(f"{trade_count:,} trades [{status}]")
        
        self.daily_stats.append({
            "date": trade_date.isoformat(),
            "trades": trade_count,
            "signals": len(day_signals),
            "warmup": not self.warmup_complete,
        })
        
        return day_signals
    
    def run(self, start_date: date, end_date: date) -> List[Signal]:
        """
        Run backtest over date range.
        """
        print("="*70)
        print("E2E BACKTEST - PRODUCTION LOGIC REPLICA")
        print("="*70)
        print(f"Period: {start_date} to {end_date}")
        print(f"Warmup: {self.warmup_days} days")
        print(f"Detection threshold: {DETECTION_THRESHOLD}x")
        print(f"Bucket size: {BUCKET_MINUTES} minutes")
        print()
        
        all_signals = []
        current = start_date
        
        while current <= end_date:
            # Skip weekends
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            
            # Check if file exists
            filepath = os.path.join(OPTIONS_DIR, f"{current.isoformat()}.csv.gz")
            if not os.path.exists(filepath):
                print(f"  {current} - FILE NOT FOUND, skipping")
                current += timedelta(days=1)
                continue
            
            day_signals = self.process_day(filepath, current)
            all_signals.extend(day_signals)
            
            current += timedelta(days=1)
        
        print()
        print("="*70)
        print("BACKTEST COMPLETE")
        print("="*70)
        print(f"Days processed: {self.days_processed}")
        print(f"Warmup days: {self.warmup_days}")
        print(f"Detection days: {self.days_processed - self.warmup_days}")
        print(f"Total signals: {len(all_signals)}")
        print()
        print(f"Aggregator: {self.aggregator.get_metrics()}")
        print(f"Detector: {self.detector.get_metrics()}")
        
        return all_signals


# =============================================================================
# MAIN
# =============================================================================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Date range
    start_date = date(2025, 7, 1)   # Include warmup period
    end_date = date(2026, 1, 28)    # End of detection
    
    # Run backtest
    engine = BacktestEngine(warmup_days=BASELINE_LOOKBACK_DAYS)
    signals = engine.run(start_date, end_date)
    
    # Save results
    output = {
        "config": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "warmup_days": BASELINE_LOOKBACK_DAYS,
            "detection_threshold": DETECTION_THRESHOLD,
            "bucket_minutes": BUCKET_MINUTES,
            "min_notional": MIN_NOTIONAL,
            "cooldown_minutes": COOLDOWN_MINUTES,
        },
        "summary": {
            "days_processed": engine.days_processed,
            "total_signals": len(signals),
            "aggregator_metrics": engine.aggregator.get_metrics(),
            "detector_metrics": engine.detector.get_metrics(),
        },
        "daily_stats": engine.daily_stats,
        "signals": [
            {
                "symbol": s.symbol,
                "detection_time": s.detection_time.isoformat(),
                "bucket_start": s.bucket_start.strftime("%H:%M"),
                "notional": s.notional,
                "baseline_notional": s.baseline_notional,
                "ratio": s.ratio,
                "contracts": s.contracts,
                "prints": s.prints,
                "call_pct": s.call_pct,
                "confidence": s.confidence,
                "baseline_source": s.baseline_source,
                "stock_price": s.stock_price,
                "filtered_out": s.filtered_out,
                "filter_reason": s.filter_reason,
            }
            for s in signals
        ],
    }
    
    output_file = os.path.join(OUTPUT_DIR, "e2e_backtest_results.json")
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")
    
    # Quick analysis
    if signals:
        print("\n--- TOP 20 SIGNALS BY RATIO ---")
        sorted_signals = sorted(signals, key=lambda s: s.ratio, reverse=True)
        for s in sorted_signals[:20]:
            print(f"  {s.detection_time.strftime('%Y-%m-%d %H:%M')} {s.symbol:6} "
                  f"{s.ratio:6.1f}x ${s.notional:>12,.0f} (base: ${s.baseline_notional:>10,.0f}) "
                  f"calls:{s.call_pct:.0%}")


if __name__ == "__main__":
    main()
