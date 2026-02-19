"""
E2E Backtest V2 - Enhanced Signal Analysis

Extends base backtest with:
- Strike clustering / OTM concentration
- Trade condition classification (sweeps)
- Price context (trend, support distance)
- Multi-factor scoring

Usage:
    python e2e_backtest_v2.py --enhance=strikes    # Add strike analysis
    python e2e_backtest_v2.py --enhance=sweeps     # Add sweep detection
    python e2e_backtest_v2.py --enhance=price      # Add price context
    python e2e_backtest_v2.py --enhance=all        # All enhancements
    python e2e_backtest_v2.py --score --threshold=5  # Run scoring
"""

import argparse
import gzip
import os
import json
from datetime import datetime, date, time as dt_time, timedelta
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import re

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"
OPTIONS_DIR = os.path.join(BASE_DIR, "options")
STOCKS_DIR = os.path.join(BASE_DIR, "stocks")
OUTPUT_DIR = os.environ.get("BACKTEST_OUTPUT_DIR", os.path.join(BASE_DIR, "backtest_results"))

BUCKET_MINUTES = 30
BASELINE_LOOKBACK_DAYS = 20
DETECTION_THRESHOLD = 3.0
MIN_NOTIONAL = 10000
COOLDOWN_MINUTES = 60

# Sweep condition code
SWEEP_CONDITION = 209

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
# ENHANCED DATA STRUCTURES
# =============================================================================

@dataclass
class BucketStats:
    """Enhanced stats for a 30-minute bucket."""
    symbol: str
    trade_date: date
    bucket_start: dt_time
    prints: int = 0
    notional: float = 0.0
    contracts: int = 0
    call_contracts: int = 0
    put_contracts: int = 0
    unique_options: set = field(default_factory=set)
    
    # Strike analysis (6.2)
    strikes: Dict[int, float] = field(default_factory=dict)  # strike -> notional
    otm_notional: float = 0.0
    atm_notional: float = 0.0
    itm_notional: float = 0.0
    
    # Sweep analysis (6.3)
    sweep_count: int = 0
    sweep_notional: float = 0.0
    
    @property
    def strike_concentration(self) -> float:
        """Entropy-based concentration (lower = more concentrated)."""
        if not self.strikes or self.notional <= 0:
            return 1.0
        total = sum(self.strikes.values())
        if total <= 0:
            return 1.0
        entropy = 0.0
        for notional in self.strikes.values():
            p = notional / total
            if p > 0:
                entropy -= p * (p ** 0.5)  # Simplified entropy proxy
        # Normalize: 0 = single strike, 1 = many strikes
        return min(len(self.strikes) / 10, 1.0)
    
    @property
    def otm_pct(self) -> float:
        """Percentage of flow that is OTM."""
        total = self.otm_notional + self.atm_notional + self.itm_notional
        return self.otm_notional / total if total > 0 else 0.0
    
    @property
    def sweep_pct(self) -> float:
        """Percentage of flow that is sweeps."""
        return self.sweep_notional / self.notional if self.notional > 0 else 0.0


@dataclass
class Trade:
    """Enhanced parsed options trade."""
    timestamp: datetime
    underlying: str
    option_symbol: str
    is_call: bool
    strike: int  # Strike price * 1000
    expiry: date
    price: float
    size: int
    conditions: List[int] = field(default_factory=list)
    
    @property
    def notional(self) -> float:
        return self.price * self.size * 100
    
    @property
    def is_sweep(self) -> bool:
        return SWEEP_CONDITION in self.conditions


@dataclass
class Signal:
    """Enhanced UOA signal with all metrics."""
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
    
    # Strike metrics (6.2)
    strike_concentration: float = 0.0
    otm_pct: float = 0.0
    unique_strikes: int = 0
    
    # Sweep metrics (6.3)
    sweep_count: int = 0
    sweep_pct: float = 0.0
    avg_trade_size: float = 0.0
    
    # Price context (6.4)
    stock_price: Optional[float] = None
    dist_from_20d_low: Optional[float] = None
    dist_from_20d_high: Optional[float] = None
    trend: Optional[int] = None  # 1 = up, -1 = down
    
    # Scoring (6.5)
    score: int = 0


# =============================================================================
# ENHANCED PARSER
# =============================================================================

def parse_option_symbol_enhanced(ticker: str) -> Optional[tuple]:
    """
    Parse OCC option symbol to extract all components.
    
    Format: O:AAPL260220C00150000
            O:{underlying}{YYMMDD}{C/P}{strike*1000}
    
    Returns: (underlying, expiry, is_call, strike) or None
    """
    if not ticker.startswith("O:"):
        return None
    
    rest = ticker[2:]
    
    # Find where underlying ends (first digit)
    underlying = ""
    digit_start = 0
    for i, c in enumerate(rest):
        if c.isdigit():
            underlying = rest[:i]
            digit_start = i
            break
    
    if not underlying or len(underlying) > 5:
        return None
    
    # Need at least 15 more chars: YYMMDD (6) + C/P (1) + strike (8)
    remaining = rest[digit_start:]
    if len(remaining) < 15:
        return None
    
    try:
        # Parse date
        yy = int(remaining[0:2])
        mm = int(remaining[2:4])
        dd = int(remaining[4:6])
        expiry = date(2000 + yy, mm, dd)
        
        # Parse call/put
        cp_char = remaining[6]
        is_call = cp_char == "C"
        
        # Parse strike (in thousandths)
        strike = int(remaining[7:15])
        
        return underlying, expiry, is_call, strike
    except:
        return None


def parse_conditions(cond_str: str) -> List[int]:
    """Parse comma-separated condition codes."""
    if not cond_str:
        return []
    try:
        return [int(c) for c in cond_str.split(",") if c.strip()]
    except:
        return []


def parse_trades_file_enhanced(filepath: str, include_sweeps: bool = False):
    """
    Generator that yields enhanced Trade objects.
    """
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            
            ticker = parts[idx["ticker"]]
            parsed = parse_option_symbol_enhanced(ticker)
            if not parsed:
                continue
            
            underlying, expiry, is_call, strike = parsed
            
            # Parse timestamp
            ts_ns = int(parts[idx["sip_timestamp"]])
            ts_sec = ts_ns / 1e9
            dt = datetime.fromtimestamp(ts_sec)
            
            # Parse conditions if needed
            conditions = []
            if include_sweeps and "conditions" in idx:
                conditions = parse_conditions(parts[idx["conditions"]])
            
            yield Trade(
                timestamp=dt,
                underlying=underlying,
                option_symbol=ticker,
                is_call=is_call,
                strike=strike,
                expiry=expiry,
                price=float(parts[idx["price"]]),
                size=int(parts[idx["size"]]),
                conditions=conditions,
            )


# =============================================================================
# PRICE CONTEXT LOADER
# =============================================================================

class PriceContextLoader:
    """Loads and caches stock price context for signals."""
    
    def __init__(self):
        self.daily_cache: Dict[str, Dict[date, dict]] = defaultdict(dict)  # symbol -> date -> OHLC
        self.loaded_dates: set = set()
    
    def load_day(self, trade_date: date):
        """Load stock daily bars for a date."""
        if trade_date in self.loaded_dates:
            return
        
        filepath = os.path.join(STOCKS_DIR, f"{trade_date.isoformat()}.csv.gz")
        if not os.path.exists(filepath):
            self.loaded_dates.add(trade_date)
            return
        
        with gzip.open(filepath, "rt") as f:
            header = f.readline().strip().split(",")
            idx = {col: i for i, col in enumerate(header)}
            
            for line in f:
                parts = line.strip().split(",")
                ticker = parts[idx["ticker"]]
                
                # Aggregate to daily OHLC
                if trade_date not in self.daily_cache[ticker]:
                    self.daily_cache[ticker][trade_date] = {
                        "o": float(parts[idx["open"]]),
                        "h": float(parts[idx["high"]]),
                        "l": float(parts[idx["low"]]),
                        "c": float(parts[idx["close"]]),
                    }
                else:
                    bar = self.daily_cache[ticker][trade_date]
                    bar["h"] = max(bar["h"], float(parts[idx["high"]]))
                    bar["l"] = min(bar["l"], float(parts[idx["low"]]))
                    bar["c"] = float(parts[idx["close"]])
        
        self.loaded_dates.add(trade_date)
    
    def get_context(self, symbol: str, trade_date: date) -> dict:
        """Get 20-day price context for a symbol."""
        # Load necessary dates
        for i in range(21):
            d = trade_date - timedelta(days=i)
            if d.weekday() < 5:
                self.load_day(d)
        
        # Collect last 20 trading days
        closes = []
        highs = []
        lows = []
        
        d = trade_date
        for _ in range(30):  # Go back up to 30 calendar days
            d -= timedelta(days=1)
            if d in self.daily_cache.get(symbol, {}):
                bar = self.daily_cache[symbol][d]
                closes.append(bar["c"])
                highs.append(bar["h"])
                lows.append(bar["l"])
                if len(closes) >= 20:
                    break
        
        if len(closes) < 5:
            return {}
        
        current_price = self.daily_cache.get(symbol, {}).get(trade_date, {}).get("c")
        if not current_price:
            return {}
        
        sma_20 = sum(closes) / len(closes)
        high_20 = max(highs)
        low_20 = min(lows)
        
        return {
            "price": current_price,
            "sma_20": sma_20,
            "high_20": high_20,
            "low_20": low_20,
            "dist_from_low": (current_price - low_20) / low_20 if low_20 > 0 else None,
            "dist_from_high": (current_price - high_20) / high_20 if high_20 > 0 else None,
            "trend": 1 if current_price > sma_20 else -1,
        }


# =============================================================================
# ENHANCED BACKTEST ENGINE
# =============================================================================

class EnhancedBacktestEngine:
    """E2E backtest with enhanced signal analysis."""
    
    def __init__(self, 
                 include_strikes: bool = False,
                 include_sweeps: bool = False,
                 include_price: bool = False):
        self.include_strikes = include_strikes
        self.include_sweeps = include_sweeps
        self.include_price = include_price
        
        # State
        self.current_buckets: Dict[str, BucketStats] = {}
        self.current_bucket_start: Optional[dt_time] = None
        self.current_date: Optional[date] = None
        self.history: Dict[str, Dict[str, List[BucketStats]]] = defaultdict(lambda: defaultdict(list))
        
        self.cooldowns: Dict[str, datetime] = {}
        self.signals: List[Signal] = []
        
        self.days_processed = 0
        self.warmup_days = BASELINE_LOOKBACK_DAYS
        self.warmup_complete = False
        
        # Price context
        if include_price:
            self.price_loader = PriceContextLoader()
        else:
            self.price_loader = None
        
        # Current day stock prices for moneyness calculation
        self.current_stock_prices: Dict[str, float] = {}
    
    def _get_bucket_start(self, dt: datetime) -> dt_time:
        minute = (dt.minute // BUCKET_MINUTES) * BUCKET_MINUTES
        return dt_time(dt.hour, minute)
    
    def _bucket_key(self, bucket_start: dt_time) -> str:
        return bucket_start.strftime("%H:%M")
    
    def _load_stock_prices(self, trade_date: date):
        """Load stock prices for moneyness calculation."""
        self.current_stock_prices = {}
        filepath = os.path.join(STOCKS_DIR, f"{trade_date.isoformat()}.csv.gz")
        if not os.path.exists(filepath):
            return
        
        with gzip.open(filepath, "rt") as f:
            header = f.readline().strip().split(",")
            idx = {col: i for i, col in enumerate(header)}
            
            for line in f:
                parts = line.strip().split(",")
                ticker = parts[idx["ticker"]]
                # Use first price as reference (will be updated throughout day)
                if ticker not in self.current_stock_prices:
                    self.current_stock_prices[ticker] = float(parts[idx["open"]])
    
    def add_trade(self, trade: Trade) -> Optional[Dict[str, BucketStats]]:
        bucket_start = self._get_bucket_start(trade.timestamp)
        trade_date = trade.timestamp.date()
        
        # Check boundary
        completed = None
        if (self.current_bucket_start is not None and
            (bucket_start != self.current_bucket_start or trade_date != self.current_date)):
            completed = self._flush_buckets()
        
        self.current_bucket_start = bucket_start
        self.current_date = trade_date
        
        # Initialize bucket
        if trade.underlying not in self.current_buckets:
            self.current_buckets[trade.underlying] = BucketStats(
                symbol=trade.underlying,
                trade_date=trade_date,
                bucket_start=bucket_start,
            )
        
        bucket = self.current_buckets[trade.underlying]
        bucket.prints += 1
        bucket.notional += trade.notional
        bucket.contracts += trade.size
        
        if trade.is_call:
            bucket.call_contracts += trade.size
        else:
            bucket.put_contracts += trade.size
        
        bucket.unique_options.add(trade.option_symbol)
        
        # Strike analysis
        if self.include_strikes:
            strike_key = trade.strike // 1000  # Convert to dollar strike
            bucket.strikes[strike_key] = bucket.strikes.get(strike_key, 0) + trade.notional
            
            # Moneyness classification
            stock_price = self.current_stock_prices.get(trade.underlying)
            if stock_price and stock_price > 0:
                strike_dollar = trade.strike / 1000
                moneyness = (strike_dollar - stock_price) / stock_price
                
                if trade.is_call:
                    if moneyness > 0.02:  # OTM call
                        bucket.otm_notional += trade.notional
                    elif moneyness < -0.02:  # ITM call
                        bucket.itm_notional += trade.notional
                    else:  # ATM
                        bucket.atm_notional += trade.notional
                else:  # Put
                    if moneyness < -0.02:  # OTM put
                        bucket.otm_notional += trade.notional
                    elif moneyness > 0.02:  # ITM put
                        bucket.itm_notional += trade.notional
                    else:  # ATM
                        bucket.atm_notional += trade.notional
        
        # Sweep analysis
        if self.include_sweeps and trade.is_sweep:
            bucket.sweep_count += 1
            bucket.sweep_notional += trade.notional
        
        return completed
    
    def _flush_buckets(self) -> Dict[str, BucketStats]:
        if not self.current_buckets:
            return {}
        
        completed = dict(self.current_buckets)
        bucket_key = self._bucket_key(self.current_bucket_start)
        
        for symbol, stats in completed.items():
            self.history[symbol][bucket_key].append(stats)
            if len(self.history[symbol][bucket_key]) > BASELINE_LOOKBACK_DAYS:
                self.history[symbol][bucket_key].pop(0)
        
        self.current_buckets = {}
        return completed
    
    def get_baseline(self, symbol: str, bucket_start: dt_time) -> tuple:
        bucket_key = self._bucket_key(bucket_start)
        history_list = self.history.get(symbol, {}).get(bucket_key, [])
        
        if len(history_list) >= 5:
            avg_notional = sum(b.notional for b in history_list) / len(history_list)
            confidence = min(len(history_list) / BASELINE_LOOKBACK_DAYS, 1.0)
            return avg_notional, confidence, "history"
        
        multiplier = TIME_MULTIPLIERS.get(bucket_key, 1.0)
        default_baseline = 10000 * multiplier
        return default_baseline, 0.1, "default"
    
    def check_bucket(self, bucket: BucketStats) -> Optional[Signal]:
        if bucket.notional < MIN_NOTIONAL:
            return None
        
        if bucket.symbol in self.cooldowns:
            last_trigger = self.cooldowns[bucket.symbol]
            elapsed = datetime.combine(bucket.trade_date, bucket.bucket_start) - last_trigger
            if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
                return None
        
        baseline, confidence, source = self.get_baseline(bucket.symbol, bucket.bucket_start)
        if baseline <= 0:
            return None
        
        ratio = bucket.notional / baseline
        if ratio < DETECTION_THRESHOLD:
            return None
        
        trigger_time = datetime.combine(bucket.trade_date, bucket.bucket_start)
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
            strike_concentration=bucket.strike_concentration if self.include_strikes else 0,
            otm_pct=bucket.otm_pct if self.include_strikes else 0,
            unique_strikes=len(bucket.strikes) if self.include_strikes else 0,
            sweep_count=bucket.sweep_count if self.include_sweeps else 0,
            sweep_pct=bucket.sweep_pct if self.include_sweeps else 0,
            avg_trade_size=bucket.contracts / bucket.prints if bucket.prints > 0 else 0,
        )
        
        # Price context
        if self.include_price and self.price_loader:
            ctx = self.price_loader.get_context(bucket.symbol, bucket.trade_date)
            if ctx:
                signal.stock_price = ctx.get("price")
                signal.dist_from_20d_low = ctx.get("dist_from_low")
                signal.dist_from_20d_high = ctx.get("dist_from_high")
                signal.trend = ctx.get("trend")
        
        self.cooldowns[bucket.symbol] = trigger_time
        self.signals.append(signal)
        return signal
    
    def process_day(self, filepath: str, trade_date: date) -> List[Signal]:
        print(f"  Processing {trade_date}...", end=" ", flush=True)
        
        # Load stock prices for moneyness
        if self.include_strikes:
            self._load_stock_prices(trade_date)
        
        day_signals = []
        trade_count = 0
        
        for trade in parse_trades_file_enhanced(filepath, self.include_sweeps):
            trade_count += 1
            completed = self.add_trade(trade)
            
            if completed and self.warmup_complete:
                for symbol, bucket in completed.items():
                    signal = self.check_bucket(bucket)
                    if signal:
                        day_signals.append(signal)
        
        # Flush end of day
        final = self._flush_buckets()
        if final and self.warmup_complete:
            for symbol, bucket in final.items():
                signal = self.check_bucket(bucket)
                if signal:
                    day_signals.append(signal)
        
        self.days_processed += 1
        
        if not self.warmup_complete and self.days_processed >= self.warmup_days:
            self.warmup_complete = True
            print(f"{trade_count:,} trades [WARMUP COMPLETE]")
        else:
            status = "WARMUP" if not self.warmup_complete else f"{len(day_signals)} signals"
            print(f"{trade_count:,} trades [{status}]")
        
        return day_signals
    
    def run(self, start_date: date, end_date: date) -> List[Signal]:
        print("="*70)
        print("E2E BACKTEST V2 - ENHANCED SIGNAL ANALYSIS")
        print("="*70)
        print(f"Period: {start_date} to {end_date}")
        print(f"Enhancements: strikes={self.include_strikes}, sweeps={self.include_sweeps}, price={self.include_price}")
        print()
        
        current = start_date
        while current <= end_date:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            
            filepath = os.path.join(OPTIONS_DIR, f"{current.isoformat()}.csv.gz")
            if not os.path.exists(filepath):
                print(f"  {current} - FILE NOT FOUND")
                current += timedelta(days=1)
                continue
            
            self.process_day(filepath, current)
            current += timedelta(days=1)
        
        print()
        print(f"Total signals: {len(self.signals)}")
        return self.signals


# =============================================================================
# SCORING SYSTEM
# =============================================================================

def calculate_score(signal: Signal) -> int:
    """Calculate multi-factor score for a signal."""
    score = 0
    
    # Time factor: Early detection (4-7 AM)
    hour = signal.detection_time.hour
    if hour < 7:
        score += 2
    elif hour < 10:
        score += 1
    
    # Direction: Bullish flow
    if signal.call_pct > 0.8:
        score += 2
    elif signal.call_pct > 0.6:
        score += 1
    elif signal.call_pct < 0.3:
        score -= 1  # Bearish penalty
    
    # Ratio: High ratio
    if signal.ratio >= 15:
        score += 2
    elif signal.ratio >= 10:
        score += 1
    
    # Strike concentration (if available)
    if signal.strike_concentration < 0.3:
        score += 1  # Concentrated = good
    
    # OTM percentage (if available)
    if 0.3 < signal.otm_pct < 0.7:
        score += 1  # Mixed OTM/ATM often good
    
    # Sweep percentage (if available)
    if signal.sweep_pct > 0.3:
        score += 1  # High sweep = urgency
    
    # Trade size
    if signal.avg_trade_size > 25:
        score += 1  # Large trades = institutional
    
    # Trend (if available)
    if signal.trend == 1 and signal.call_pct > 0.6:
        score += 1  # Bullish flow + uptrend
    
    # Distance from low (if available)
    if signal.dist_from_20d_low is not None and signal.dist_from_20d_low < 0.1:
        score += 1  # Near support
    
    return score


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="E2E Backtest V2 - Enhanced")
    parser.add_argument("--enhance", type=str, default="", 
                       help="Enhancements: strikes, sweeps, price, all")
    parser.add_argument("--score", action="store_true",
                       help="Calculate scores")
    parser.add_argument("--threshold", type=int, default=0,
                       help="Minimum score threshold for output")
    parser.add_argument("--start", type=str, default="2025-07-01")
    parser.add_argument("--end", type=str, default="2026-01-28")
    args = parser.parse_args()
    
    # Parse enhancements
    enhancements = args.enhance.lower().split(",") if args.enhance else []
    include_strikes = "strikes" in enhancements or "all" in enhancements
    include_sweeps = "sweeps" in enhancements or "all" in enhancements
    include_price = "price" in enhancements or "all" in enhancements
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    
    # Run backtest
    engine = EnhancedBacktestEngine(
        include_strikes=include_strikes,
        include_sweeps=include_sweeps,
        include_price=include_price
    )
    signals = engine.run(start_date, end_date)
    
    # Calculate scores if requested
    if args.score:
        print("\nCalculating scores...")
        for s in signals:
            s.score = calculate_score(s)
    
    # Filter by threshold
    if args.threshold > 0:
        signals = [s for s in signals if s.score >= args.threshold]
        print(f"Signals after score >= {args.threshold} filter: {len(signals)}")
    
    # Save results
    suffix = ""
    if include_strikes:
        suffix += "_strikes"
    if include_sweeps:
        suffix += "_sweeps"
    if include_price:
        suffix += "_price"
    if args.score:
        suffix += "_scored"
    
    output_file = os.path.join(OUTPUT_DIR, f"e2e_backtest_v2{suffix}.json")
    
    output = {
        "config": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "include_strikes": include_strikes,
            "include_sweeps": include_sweeps,
            "include_price": include_price,
            "scored": args.score,
            "threshold": args.threshold,
        },
        "summary": {
            "total_signals": len(signals),
            "days_processed": engine.days_processed,
        },
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
                "strike_concentration": s.strike_concentration,
                "otm_pct": s.otm_pct,
                "unique_strikes": s.unique_strikes,
                "sweep_count": s.sweep_count,
                "sweep_pct": s.sweep_pct,
                "avg_trade_size": s.avg_trade_size,
                "stock_price": s.stock_price,
                "dist_from_20d_low": s.dist_from_20d_low,
                "dist_from_20d_high": s.dist_from_20d_high,
                "trend": s.trend,
                "score": s.score,
            }
            for s in signals
        ],
    }
    
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")
    
    # Quick stats
    if args.score and signals:
        print("\n=== SCORE DISTRIBUTION ===")
        score_dist = defaultdict(int)
        for s in signals:
            score_dist[s.score] += 1
        for score in sorted(score_dist.keys()):
            print(f"  Score {score}: {score_dist[score]}")


if __name__ == "__main__":
    main()
