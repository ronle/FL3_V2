"""
E2E Backtest Outcome Labeler

Takes signals from e2e_backtest.py and adds price outcomes using stock minute bars.

Measures:
- Price at signal time
- Price at market open (if pre-market signal)
- Price at close
- Max gain/loss after signal
- Gap at open (for pre-market signals)

Also applies liquidity filters:
- MIN_STOCK_PRICE: Filter penny stocks
"""

import gzip
import os
import json
from datetime import datetime, date, time as dt_time, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

BASE_DIR = "C:\\Users\\levir\\Documents\\FL3_V2\\polygon_data"
STOCKS_DIR = os.path.join(BASE_DIR, "stocks")
OUTPUT_DIR = os.path.join(BASE_DIR, "backtest_results")

# Filters
MIN_STOCK_PRICE = 5.00
APPLY_FILTERS = True

# Common ETFs to exclude (high volume, not pump targets)
EXCLUDED_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "VXX", "UVXY", "SVXY",
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "XLB", "XLY", "XLRE",
    "GLD", "SLV", "USO", "UNG", "TLT", "HYG", "LQD", "JNK",
    "EEM", "EFA", "VWO", "FXI", "EWZ", "EWJ",
    "ARKK", "ARKG", "ARKF", "ARKW", "ARKQ",
    "SOXL", "SOXS", "TQQQ", "SQQQ", "SPXU", "SPXL", "TNA", "TZA",
    "LABU", "LABD", "JNUG", "JDST", "NUGT", "DUST",
    "VTI", "VOO", "VEA", "VGK", "VPL",
    "IEMG", "AGG", "BND", "VCIT", "VCSH",
    "XBI", "IBB", "SMH", "SOXX", "KRE", "XOP", "XHB", "ITB",
    "KWEB", "MCHI", "ASHR", "YINN", "YANG",
}


def load_stock_bars(filepath: str) -> Dict[str, List[dict]]:
    """
    Load stock minute bars from flat file.
    
    Returns: {ticker: [{t, o, h, l, c, v}, ...]}
    """
    bars = defaultdict(list)
    
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}
        
        for line in f:
            parts = line.strip().split(",")
            ticker = parts[idx["ticker"]]
            
            bars[ticker].append({
                "t": int(parts[idx["window_start"]]) // 1000000,  # ns -> ms
                "o": float(parts[idx["open"]]),
                "h": float(parts[idx["high"]]),
                "l": float(parts[idx["low"]]),
                "c": float(parts[idx["close"]]),
                "v": int(parts[idx["volume"]]),
            })
    
    # Sort by time
    for ticker in bars:
        bars[ticker].sort(key=lambda x: x["t"])
    
    return dict(bars)


def find_price_at_time(bars: List[dict], target_ms: int) -> Optional[float]:
    """Find price at or just before target time."""
    if not bars:
        return None
    
    closest = None
    for bar in bars:
        if bar["t"] <= target_ms:
            closest = bar
        else:
            break
    
    return closest["c"] if closest else bars[0]["o"]


def find_market_open_price(bars: List[dict]) -> Optional[float]:
    """Find price at 9:30 AM market open."""
    for bar in bars:
        bar_time = datetime.fromtimestamp(bar["t"] / 1000)
        if bar_time.hour == 9 and bar_time.minute >= 30:
            return bar["o"]
        elif bar_time.hour > 9:
            return bar["o"]
    return None


def label_signals(signals: List[dict]) -> List[dict]:
    """
    Add price outcomes to signals.
    """
    print("="*70)
    print("LABELING SIGNAL OUTCOMES")
    print("="*70)
    
    # Group signals by date
    by_date = defaultdict(list)
    for s in signals:
        signal_date = s["detection_time"][:10]
        by_date[signal_date].append(s)
    
    print(f"Signals to label: {len(signals)}")
    print(f"Unique dates: {len(by_date)}")
    
    labeled = []
    success = 0
    missing = 0
    
    for signal_date in sorted(by_date.keys()):
        date_signals = by_date[signal_date]
        
        # Load stock bars for this date
        stock_file = os.path.join(STOCKS_DIR, f"{signal_date}.csv.gz")
        if not os.path.exists(stock_file):
            print(f"  {signal_date}: Stock file missing, skipping {len(date_signals)} signals")
            missing += len(date_signals)
            continue
        
        print(f"  {signal_date}: Loading stock bars...", end=" ", flush=True)
        stock_bars = load_stock_bars(stock_file)
        print(f"{len(stock_bars)} tickers, {len(date_signals)} signals")
        
        for sig in date_signals:
            ticker = sig["symbol"]
            bars = stock_bars.get(ticker, [])
            
            if not bars:
                labeled.append({**sig, "price_at_signal": None, "outcome": None})
                continue
            
            # Parse detection time
            det_time = datetime.fromisoformat(sig["detection_time"])
            det_ms = det_time.timestamp() * 1000
            
            # Find prices
            price_at_signal = find_price_at_time(bars, det_ms)
            market_open = find_market_open_price(bars)
            
            # Day stats
            day_high = max(b["h"] for b in bars)
            day_low = min(b["l"] for b in bars)
            day_close = bars[-1]["c"]
            
            # Bars after signal
            bars_after = [b for b in bars if b["t"] >= det_ms]
            max_after = max(b["h"] for b in bars_after) if bars_after else None
            min_after = min(b["l"] for b in bars_after) if bars_after else None
            
            # Calculate returns
            if price_at_signal and price_at_signal > 0:
                pct_to_close = (day_close - price_at_signal) / price_at_signal * 100
                pct_max_gain = (max_after - price_at_signal) / price_at_signal * 100 if max_after else None
                pct_max_loss = (min_after - price_at_signal) / price_at_signal * 100 if min_after else None
            else:
                pct_to_close = pct_max_gain = pct_max_loss = None
            
            # Pre-market check
            is_premarket = det_time.hour < 9 or (det_time.hour == 9 and det_time.minute < 30)
            
            # Gap at open
            gap_pct = None
            if is_premarket and price_at_signal and market_open:
                gap_pct = (market_open - price_at_signal) / price_at_signal * 100
            
            # Apply filters
            filtered_out = False
            filter_reason = None
            if APPLY_FILTERS:
                if ticker in EXCLUDED_ETFS:
                    filtered_out = True
                    filter_reason = "excluded_etf"
                elif price_at_signal and price_at_signal < MIN_STOCK_PRICE:
                    filtered_out = True
                    filter_reason = f"penny_stock (${price_at_signal:.2f} < ${MIN_STOCK_PRICE})"
            
            labeled.append({
                **sig,
                "price_at_signal": round(price_at_signal, 4) if price_at_signal else None,
                "market_open": round(market_open, 4) if market_open else None,
                "day_close": round(day_close, 4),
                "day_high": round(day_high, 4),
                "day_low": round(day_low, 4),
                "pct_to_close": round(pct_to_close, 2) if pct_to_close is not None else None,
                "pct_max_gain": round(pct_max_gain, 2) if pct_max_gain is not None else None,
                "pct_max_loss": round(pct_max_loss, 2) if pct_max_loss is not None else None,
                "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
                "is_premarket": is_premarket,
                "filtered_out": filtered_out,
                "filter_reason": filter_reason,
            })
            success += 1
    
    print(f"\nLabeled: {success}, Missing stock data: {missing}")
    return labeled


def analyze_outcomes(signals: List[dict]):
    """Analyze labeled signal outcomes."""
    print("\n" + "="*70)
    print("OUTCOME ANALYSIS")
    print("="*70)
    
    # Split filtered vs unfiltered
    all_with_outcomes = [s for s in signals if s.get("pct_to_close") is not None]
    filtered_out = [s for s in all_with_outcomes if s.get("filtered_out")]
    with_outcomes = [s for s in all_with_outcomes if not s.get("filtered_out")]
    
    print(f"Total signals with outcomes: {len(all_with_outcomes)}")
    print(f"  Filtered out (penny stocks etc): {len(filtered_out)}")
    print(f"  Remaining for analysis: {len(with_outcomes)}")
    
    if not with_outcomes:
        return {}
    
    closes = [s["pct_to_close"] for s in with_outcomes]
    
    print(f"\n--- OVERALL ---")
    print(f"Avg % to close: {sum(closes)/len(closes):.2f}%")
    print(f"Median: {sorted(closes)[len(closes)//2]:.2f}%")
    print(f"Win rate: {len([c for c in closes if c > 0])/len(closes)*100:.1f}%")
    print(f"Big winners (>5%): {len([c for c in closes if c > 5])}")
    print(f"Big losers (<-5%): {len([c for c in closes if c < -5])}")
    
    # By baseline source
    print(f"\n--- BY BASELINE SOURCE ---")
    for source in ["history", "default"]:
        bucket = [s for s in with_outcomes if s.get("baseline_source") == source]
        if bucket:
            avg = sum(s["pct_to_close"] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s["pct_to_close"] > 0]) / len(bucket) * 100
            print(f"  {source:10}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # By confidence
    print(f"\n--- BY CONFIDENCE ---")
    for conf_min, conf_max, label in [(0, 0.3, "low"), (0.3, 0.7, "medium"), (0.7, 1.1, "high")]:
        bucket = [s for s in with_outcomes if conf_min <= s.get("confidence", 0) < conf_max]
        if bucket:
            avg = sum(s["pct_to_close"] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s["pct_to_close"] > 0]) / len(bucket) * 100
            print(f"  {label:10}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # By ratio bucket
    print(f"\n--- BY RATIO ---")
    for name, lo, hi in [("3-5x", 3, 5), ("5-10x", 5, 10), ("10-20x", 10, 20), ("20x+", 20, 9999)]:
        bucket = [s for s in with_outcomes if lo <= s["ratio"] < hi]
        if bucket:
            avg = sum(s["pct_to_close"] for s in bucket) / len(bucket)
            wr = len([s for s in bucket if s["pct_to_close"] > 0]) / len(bucket) * 100
            print(f"  {name:10}: n={len(bucket):5}, avg={avg:+.2f}%, win={wr:.1f}%")
    
    # Pre-market
    print(f"\n--- PRE-MARKET SIGNALS ---")
    premarket = [s for s in with_outcomes if s.get("is_premarket")]
    if premarket:
        pm_closes = [s["pct_to_close"] for s in premarket]
        print(f"Count: {len(premarket)}")
        print(f"Avg: {sum(pm_closes)/len(pm_closes):.2f}%")
        print(f"Win rate: {len([c for c in pm_closes if c > 0])/len(pm_closes)*100:.1f}%")
        
        with_gap = [s for s in premarket if s.get("gap_pct") is not None]
        if with_gap:
            gaps = [s["gap_pct"] for s in with_gap]
            print(f"Avg gap at open: {sum(gaps)/len(gaps):.2f}%")
    
    # Top performers
    print(f"\n--- TOP 25 PERFORMERS ---")
    sorted_signals = sorted(with_outcomes, key=lambda x: x["pct_to_close"], reverse=True)
    for s in sorted_signals[:25]:
        t = s["detection_time"][11:16]
        p = f"${s['price_at_signal']:.2f}" if s.get("price_at_signal") else "N/A"
        print(f"{s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} {p:>8} => {s['pct_to_close']:+.1f}%")
    
    # Bottom performers
    print(f"\n--- BOTTOM 25 PERFORMERS ---")
    for s in sorted_signals[-25:]:
        t = s["detection_time"][11:16]
        p = f"${s['price_at_signal']:.2f}" if s.get("price_at_signal") else "N/A"
        print(f"{s['detection_time'][:10]} {s['symbol']:6} {s['ratio']:5.1f}x @ {t} {p:>8} => {s['pct_to_close']:+.1f}%")
    
    return {
        "total": len(with_outcomes),
        "avg_return": sum(closes)/len(closes),
        "win_rate": len([c for c in closes if c > 0])/len(closes),
    }


def main():
    # Load backtest results
    input_file = os.path.join(OUTPUT_DIR, "e2e_backtest_results.json")
    
    if not os.path.exists(input_file):
        print(f"ERROR: {input_file} not found. Run e2e_backtest.py first.")
        return
    
    print(f"Loading signals from {input_file}...")
    with open(input_file) as f:
        data = json.load(f)
    
    signals = data["signals"]
    print(f"Loaded {len(signals)} signals")
    
    # Label outcomes
    labeled = label_signals(signals)
    
    # Analyze
    summary = analyze_outcomes(labeled)
    
    # Save
    output_file = os.path.join(OUTPUT_DIR, "e2e_backtest_with_outcomes.json")
    with open(output_file, "w") as f:
        json.dump({
            "config": data["config"],
            "summary": {**data["summary"], "outcome_summary": summary},
            "signals": labeled,
        }, f, indent=2)
    
    print(f"\nSaved to: {output_file}")


if __name__ == "__main__":
    main()
