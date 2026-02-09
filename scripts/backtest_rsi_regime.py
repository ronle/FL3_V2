#!/usr/bin/env python3
"""
RSI Regime Backtest — Bounce-Day Filter Relaxation

Compares two scenarios:
  Baseline:  RSI < 50 always (current V28 production rule)
  Adaptive:  RSI < 60 on bounce days, RSI < 50 otherwise

A "bounce day" = SPY closes green after 2+ consecutive red closes.

Usage:
    python -m scripts.backtest_rsi_regime
    python -m scripts.backtest_rsi_regime --baseline-rsi 50 --adaptive-rsi 65
    python -m scripts.backtest_rsi_regime --min-red-days 3
    python -m scripts.backtest_rsi_regime --from 2026-01-30 --to 2026-02-06
    python -m scripts.backtest_rsi_regime --output results/rsi_regime.json
"""

import argparse
import csv
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    # RSI thresholds
    baseline_rsi: float = 50.0
    adaptive_rsi: float = 60.0

    # Bounce-day definition
    min_red_days: int = 2
    spy_bounce_min_pct: float = 0.0  # SPY open vs prior close

    # Trade simulation
    entry_delay_min: int = 5
    slippage_pct: float = 0.001
    exit_time: str = "15:55"
    hard_stop_pct: float = -0.05
    use_hard_stop: bool = True
    max_positions: int = 5
    max_per_sector: int = 2
    account_size: float = 100_000
    max_position_pct: float = 0.10
    min_notional: float = 50_000

    # Date range
    from_date: str = "2025-07-01"
    to_date: str = "2026-12-31"

    # Paths
    bars_dir: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "polygon_data", "stocks")
    output: Optional[str] = None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SPYDay:
    date: date
    open_price: float
    high: float
    low: float
    close: float


@dataclass
class SignalEval:
    id: int
    symbol: str
    detected_at: datetime
    score_total: int
    passed_all_filters: bool
    rejection_reason: Optional[str]
    rsi_14: Optional[float]
    trend: int
    notional: float
    metadata: Optional[dict]


@dataclass
class Trade:
    symbol: str
    signal_date: date
    signal_time: datetime
    entry_time: Optional[datetime]
    entry_price: float
    exit_time: Optional[datetime]
    exit_price: float
    shares: int
    pnl_pct: float
    pnl_dollars: float
    stopped_out: bool = False
    scenario: str = ""  # "baseline", "adaptive", "new"


# ---------------------------------------------------------------------------
# Database connection (local via Cloud SQL Auth Proxy)
# ---------------------------------------------------------------------------

def get_db_connection():
    """Connect to fl3 database via Cloud SQL Auth Proxy on localhost:5433."""
    import psycopg2
    db_url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "")

    # Auto-transform Cloud SQL socket URL to TCP for local Windows dev
    if "/cloudsql/" in db_url or not db_url:
        db_url = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Step 1: SPY daily from polygon bars
# ---------------------------------------------------------------------------

def _extract_spy_from_barfile(fpath: Path) -> Optional[SPYDay]:
    """Extract SPY daily OHLC from a single 1-min bar file."""
    date_str = fpath.name.split(".")[0]
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return None

    rth_bars = []
    with gzip.open(str(fpath), "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["ticker"] != "SPY":
                continue
            ts = int(row["window_start"]) / 1e9
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            # RTH filter: 14:30-21:00 UTC = 9:30 AM-4:00 PM ET (EST)
            if 14 <= dt.hour <= 20:
                rth_bars.append(row)

    if not rth_bars:
        return None

    return SPYDay(
        date=d,
        open_price=float(rth_bars[0]["open"]),
        high=max(float(b["high"]) for b in rth_bars),
        low=min(float(b["low"]) for b in rth_bars),
        close=float(rth_bars[-1]["close"]),
    )


def compute_spy_daily(
    bars_dir: str,
    from_date: str,
    to_date: str,
    buffer_days: int = 10,
) -> List[SPYDay]:
    """
    Build SPY daily OHLC for bounce-day detection.

    Only scans bar files within [from_date - buffer_days, to_date] to avoid
    reading hundreds of files unnecessarily.
    """
    start = date.fromisoformat(from_date) - timedelta(days=buffer_days + 5)
    end = date.fromisoformat(to_date) + timedelta(days=1)

    bar_files = sorted(Path(bars_dir).glob("*.csv.gz"))
    files_to_scan = []
    for fpath in bar_files:
        try:
            d = date.fromisoformat(fpath.name.split(".")[0])
        except ValueError:
            continue
        if start <= d <= end:
            files_to_scan.append(fpath)

    print(f"  Scanning {len(files_to_scan)} bar files for SPY daily data "
          f"({start} to {end})...")

    spy_days = []
    for i, fpath in enumerate(files_to_scan):
        result = _extract_spy_from_barfile(fpath)
        if result:
            spy_days.append(result)
        if (i + 1) % 5 == 0:
            print(f"    ...processed {i+1}/{len(files_to_scan)} files "
                  f"({len(spy_days)} SPY days found)")

    spy_days.sort(key=lambda x: x.date)
    if spy_days:
        print(f"  Found {len(spy_days)} SPY trading days "
              f"({spy_days[0].date} to {spy_days[-1].date})")
    else:
        print("  WARNING: No SPY daily data found in bar files!")
    return spy_days


# ---------------------------------------------------------------------------
# Step 2: Bounce-day detection
# ---------------------------------------------------------------------------

def detect_bounce_days(spy_daily: List[SPYDay], min_red_days: int = 2) -> Set[date]:
    """
    Bounce day = SPY closes green after N+ consecutive red closes.

    "Green" = today's close > today's open
    "Red close" = close < prior day's close
    """
    bounce_days = set()

    for i in range(min_red_days, len(spy_daily)):
        today = spy_daily[i]

        # Today must be green (close > open)
        if today.close <= today.open_price:
            continue

        # Count consecutive prior red closes (close < prior close)
        red_streak = 0
        for j in range(i - 1, max(i - 10, 0), -1):
            if spy_daily[j].close < spy_daily[j - 1].close if j > 0 else False:
                red_streak += 1
            else:
                break

        if red_streak >= min_red_days:
            bounce_days.add(today.date)

    return bounce_days


# ---------------------------------------------------------------------------
# Step 3: Load signal evaluations from DB
# ---------------------------------------------------------------------------

def load_signal_evaluations(conn, from_date: str, to_date: str) -> List[SignalEval]:
    """Query all signal evaluations in date range."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, symbol, detected_at, score_total, passed_all_filters,
               rejection_reason, rsi_14, trend, notional, metadata
        FROM signal_evaluations
        WHERE detected_at >= %s AND detected_at < %s::date + 1
        ORDER BY detected_at
    """, (from_date, to_date))

    signals = []
    for row in cur.fetchall():
        signals.append(SignalEval(
            id=row[0],
            symbol=row[1],
            detected_at=row[2],
            score_total=row[3],
            passed_all_filters=row[4],
            rejection_reason=row[5],
            rsi_14=float(row[6]) if row[6] is not None else None,
            trend=row[7] or 0,
            notional=float(row[8]) if row[8] is not None else 0,
            metadata=row[9],
        ))
    cur.close()
    print(f"  Loaded {len(signals)} signal evaluations ({from_date} to {to_date})")
    return signals


def load_sector_map(conn) -> Dict[str, str]:
    """Load symbol -> sector mapping from master_tickers."""
    cur = conn.cursor()
    cur.execute("SELECT symbol, sector FROM master_tickers WHERE sector IS NOT NULL")
    sectors = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    print(f"  Loaded {len(sectors)} sector mappings")
    return sectors


# ---------------------------------------------------------------------------
# Step 3b: Load enriched signals from JSON
# ---------------------------------------------------------------------------

def load_enriched_signals(
    input_path: str, from_date: str, to_date: str
) -> Tuple[List[SignalEval], Dict[str, str]]:
    """Load pre-enriched signals from JSON file and map to SignalEval objects."""
    with open(input_path) as f:
        data = json.load(f)

    raw_signals = data.get("signals", data) if isinstance(data, dict) else data

    from_dt = date.fromisoformat(from_date)
    to_dt = date.fromisoformat(to_date)

    signals = []
    sectors = {}
    for i, sig in enumerate(raw_signals):
        det_str = sig.get("detection_time", "")
        try:
            detected_at = datetime.fromisoformat(det_str)
        except (ValueError, TypeError):
            continue

        sig_date = detected_at.date()
        if sig_date < from_dt or sig_date > to_dt:
            continue

        signals.append(SignalEval(
            id=i,
            symbol=sig["symbol"],
            detected_at=detected_at,
            score_total=sig.get("score", 0),
            passed_all_filters=sig.get("filter_verdict", False),
            rejection_reason=sig.get("rejection_reason"),
            rsi_14=sig.get("rsi_14"),
            trend=sig.get("trend", 0),
            notional=sig.get("notional", 0),
            metadata=None,
        ))

        # Collect sector data
        sector = sig.get("sector")
        if sector and sector != "Unknown":
            sectors[sig["symbol"]] = sector

    signals.sort(key=lambda s: s.detected_at)
    print(f"  Loaded {len(signals)} enriched signals ({from_date} to {to_date})")
    print(f"  Sectors: {len(sectors)} symbols")
    return signals, sectors


# ---------------------------------------------------------------------------
# Step 4: Filter reconstruction
# ---------------------------------------------------------------------------

def passes_baseline(sig: SignalEval) -> bool:
    """Signal passes under current V28 rules."""
    return sig.passed_all_filters


def is_rsi_only_rejection(rejection_reason: str) -> bool:
    """Check if RSI was the ONLY rejection reason."""
    if not rejection_reason:
        return False
    reasons = [r.strip() for r in rejection_reason.split(";")]
    non_rsi = [r for r in reasons if "RSI" not in r.upper()]
    return len(non_rsi) == 0


def passes_adaptive(sig: SignalEval, bounce_days: Set[date], config: BacktestConfig) -> bool:
    """Signal passes under adaptive RSI rules (relaxed on bounce days)."""
    # Already passes baseline -> passes adaptive too
    if sig.passed_all_filters:
        return True

    if sig.rejection_reason is None:
        return False

    sig_date = sig.detected_at.date()
    if sig_date not in bounce_days:
        return False  # Not a bounce day — same strict rules apply

    # Check if RSI was the ONLY rejection reason
    if not is_rsi_only_rejection(sig.rejection_reason):
        return False

    # RSI must be in the relaxation window [baseline, adaptive)
    if sig.rsi_14 is None or sig.rsi_14 >= config.adaptive_rsi:
        return False

    # Double-check: notional is available in the table, verify it passes
    if sig.notional < config.min_notional:
        return False

    return True


def is_new_trade(sig: SignalEval, bounce_days: Set[date], config: BacktestConfig) -> bool:
    """Signal is newly admitted ONLY under adaptive (not baseline)."""
    return (not sig.passed_all_filters
            and passes_adaptive(sig, bounce_days, config))


# ---------------------------------------------------------------------------
# Step 5: Position limit simulation
# ---------------------------------------------------------------------------

def apply_position_limits(
    signals: List[SignalEval],
    sectors: Dict[str, str],
    max_positions: int,
    max_per_sector: int,
) -> List[SignalEval]:
    """
    Apply per-day position limits. First-come-first-served by detection time.
    Returns the subset of signals that would actually be traded.
    """
    admitted = []
    by_day = defaultdict(list)
    for sig in signals:
        by_day[sig.detected_at.date()].append(sig)

    for day in sorted(by_day.keys()):
        day_signals = by_day[day]  # already sorted by detected_at
        day_positions = 0
        sector_counts = defaultdict(int)
        seen_symbols = set()

        for sig in day_signals:
            if day_positions >= max_positions:
                break
            # Deduplicate: same symbol can only be entered once per day
            if sig.symbol in seen_symbols:
                continue
            sector = sectors.get(sig.symbol, "Unknown")
            if sector_counts[sector] >= max_per_sector:
                continue

            admitted.append(sig)
            day_positions += 1
            sector_counts[sector] += 1
            seen_symbols.add(sig.symbol)

    return admitted


# ---------------------------------------------------------------------------
# Step 6: Trade simulation with 1-min bars
# ---------------------------------------------------------------------------

def load_bars_for_day(date_str: str, symbols: Set[str], bars_dir: str) -> Dict[str, List[dict]]:
    """Load 1-min bars for specific symbols on a specific day."""
    filepath = os.path.join(bars_dir, f"{date_str}.csv.gz")
    if not os.path.exists(filepath):
        return {}

    bars = defaultdict(list)
    with gzip.open(filepath, "rt") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["ticker"] in symbols:
                ts = int(row["window_start"]) / 1e9
                bars[row["ticker"]].append({
                    "time": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                })

    # Sort each symbol's bars by time
    for sym in bars:
        bars[sym].sort(key=lambda b: b["time"])

    return dict(bars)


def get_bar_at_or_after(bars: List[dict], target: datetime) -> Optional[dict]:
    """Find first bar at or after target time."""
    for bar in bars:
        if bar["time"] >= target:
            return bar
    return None


def get_bar_at_or_before(bars: List[dict], target: datetime) -> Optional[dict]:
    """Find last bar at or before target time."""
    result = None
    for bar in bars:
        if bar["time"] <= target:
            result = bar
        else:
            break
    return result


def simulate_trade(
    sig: SignalEval,
    bars: List[dict],
    config: BacktestConfig,
    scenario: str,
) -> Optional[Trade]:
    """Simulate a single trade: entry delay, slippage, hard stop, EOD exit."""

    signal_time = sig.detected_at.replace(tzinfo=timezone.utc) if sig.detected_at.tzinfo is None else sig.detected_at
    entry_target = signal_time + timedelta(minutes=config.entry_delay_min)

    # Entry
    entry_bar = get_bar_at_or_after(bars, entry_target)
    if entry_bar is None:
        return None
    entry_price = entry_bar["high"] * (1 + config.slippage_pct)  # worst case

    # Position sizing
    max_dollars = config.account_size * config.max_position_pct
    shares = int(max_dollars / entry_price)
    if shares < 1:
        return None

    # Exit time target (3:55 PM ET = ~20:55 UTC in EST, ~19:55 UTC in EDT)
    # Use the same date as the signal, set exit at ~20:55 UTC (conservative)
    exit_h, exit_m = map(int, config.exit_time.split(":"))
    # Convert ET exit time to UTC (add 5 hours for EST)
    exit_target = entry_bar["time"].replace(
        hour=exit_h + 5, minute=exit_m, second=0, microsecond=0
    )
    # Handle EDT (March-November): add 4 instead of 5
    sig_month = sig.detected_at.month
    if 3 <= sig_month <= 10:  # rough EDT check
        exit_target = entry_bar["time"].replace(
            hour=exit_h + 4, minute=exit_m, second=0, microsecond=0
        )

    # Walk bars from entry to exit, check hard stop
    stopped_out = False
    exit_price = None
    exit_time = None

    entry_idx = None
    for i, bar in enumerate(bars):
        if bar["time"] >= entry_bar["time"]:
            entry_idx = i
            break
    if entry_idx is None:
        return None

    if config.use_hard_stop:
        stop_price = entry_price * (1 + config.hard_stop_pct)
        for bar in bars[entry_idx:]:
            if bar["time"] > exit_target:
                break
            if bar["low"] * (1 - config.slippage_pct) <= stop_price:
                exit_price = stop_price
                exit_time = bar["time"]
                stopped_out = True
                break

    # If not stopped out, exit at target time
    if exit_price is None:
        exit_bar = get_bar_at_or_before(bars, exit_target)
        if exit_bar is None or exit_bar["time"] < entry_bar["time"]:
            # No exit bar found — use last available bar
            exit_bar = bars[-1] if bars else None
            if exit_bar is None or exit_bar["time"] < entry_bar["time"]:
                return None
        exit_price = exit_bar["low"] * (1 - config.slippage_pct)  # worst case
        exit_time = exit_bar["time"]

    pnl_pct = (exit_price - entry_price) / entry_price
    pnl_dollars = shares * entry_price * pnl_pct

    return Trade(
        symbol=sig.symbol,
        signal_date=sig.detected_at.date(),
        signal_time=sig.detected_at,
        entry_time=entry_bar["time"],
        entry_price=round(entry_price, 4),
        exit_time=exit_time,
        exit_price=round(exit_price, 4),
        shares=shares,
        pnl_pct=round(pnl_pct, 6),
        pnl_dollars=round(pnl_dollars, 2),
        stopped_out=stopped_out,
        scenario=scenario,
    )


# ---------------------------------------------------------------------------
# Step 7: Reporting
# ---------------------------------------------------------------------------

def calc_stats(trades: List[Trade]) -> dict:
    """Calculate aggregate statistics for a list of trades."""
    if not trades:
        return {
            "count": 0, "wins": 0, "win_rate": 0,
            "avg_pnl_pct": 0, "total_pnl": 0,
            "sharpe": 0, "max_dd_pct": 0,
            "best": None, "worst": None,
        }

    pnls = [t.pnl_pct for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    avg_pnl = sum(pnls) / len(pnls)
    total_pnl = sum(t.pnl_dollars for t in trades)

    # Sharpe (annualized from daily, rough)
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0001
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0
    else:
        sharpe = 0

    # Max drawdown (relative to $100K account)
    account = 100_000
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in sorted(trades, key=lambda x: x.signal_time):
        cumulative += t.pnl_dollars
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / account
        if dd > max_dd:
            max_dd = dd

    best = max(trades, key=lambda t: t.pnl_pct)
    worst = min(trades, key=lambda t: t.pnl_pct)

    return {
        "count": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades) * 100,
        "avg_pnl_pct": avg_pnl * 100,
        "total_pnl": total_pnl,
        "sharpe": sharpe,
        "max_dd_pct": max_dd * 100,
        "best": f"{best.symbol} +{best.pnl_pct*100:.2f}%",
        "worst": f"{worst.symbol} {worst.pnl_pct*100:.2f}%",
        "stopped_out": sum(1 for t in trades if t.stopped_out),
    }


def print_report(
    baseline_trades: List[Trade],
    adaptive_trades: List[Trade],
    new_trades: List[Trade],
    bounce_days: Set[date],
    total_days: int,
    config: BacktestConfig,
    signal_stats: dict,
):
    """Print formatted comparison report."""
    bs = calc_stats(baseline_trades)
    ad = calc_stats(adaptive_trades)
    nw = calc_stats(new_trades)

    # Bounce-day only trades
    bounce_baseline = [t for t in baseline_trades if t.signal_date in bounce_days]
    bounce_adaptive = [t for t in adaptive_trades if t.signal_date in bounce_days]
    bbs = calc_stats(bounce_baseline)
    bas = calc_stats(bounce_adaptive)

    # Normal-day trades (should be identical between scenarios)
    normal_baseline = [t for t in baseline_trades if t.signal_date not in bounce_days]
    nbs = calc_stats(normal_baseline)

    w = 24  # column width

    print()
    print("=" * 72)
    print("RSI REGIME BACKTEST RESULTS")
    print("=" * 72)
    print()
    print(f"Date Range:            {config.from_date} to {config.to_date}")
    print(f"Signal Evaluations:    {signal_stats['total']} total across {signal_stats['days']} trading days")
    print(f"RSI-Only Rejections:   {signal_stats['rsi_only_50_60']} (RSI 50-60, sole reason)")
    print(f"Bounce Days:           {len(bounce_days)} out of {total_days} trading days")
    if bounce_days:
        print(f"  Dates:               {', '.join(str(d) for d in sorted(bounce_days))}")
    print(f"Bounce Definition:     SPY green after {config.min_red_days}+ red closes")
    print(f"RSI Thresholds:        Baseline < {config.baseline_rsi}  |  Adaptive < {config.adaptive_rsi} (bounce only)")
    print()

    print("-" * 72)
    print("OVERALL COMPARISON")
    print("-" * 72)
    hdr = f"{'':30s} {'Baseline (RSI<'+str(int(config.baseline_rsi))+')':>{w}s} {'Adaptive (RSI<'+str(int(config.adaptive_rsi))+' bounce)':>{w}s}"
    print(hdr)
    print(f"{'Total Trades':30s} {bs['count']:>{w}d} {ad['count']:>{w}d}")
    print(f"{'Win Rate':30s} {bs['win_rate']:>{w}.1f}% {ad['win_rate']:>{w}.1f}%")
    print(f"{'Avg PnL/Trade':30s} {bs['avg_pnl_pct']:>{w}.3f}% {ad['avg_pnl_pct']:>{w}.3f}%")
    print(f"{'Total PnL':30s} ${bs['total_pnl']:>{w-1},.2f} ${ad['total_pnl']:>{w-1},.2f}")
    print(f"{'Sharpe Ratio':30s} {bs['sharpe']:>{w}.2f} {ad['sharpe']:>{w}.2f}")
    print(f"{'Max Drawdown':30s} {bs['max_dd_pct']:>{w}.1f}% {ad['max_dd_pct']:>{w}.1f}%")
    print(f"{'Stopped Out':30s} {bs['stopped_out']:>{w}d} {ad['stopped_out']:>{w}d}")
    print()

    if bounce_days:
        print("-" * 72)
        print(f"BOUNCE DAY PERFORMANCE ({len(bounce_days)} days)")
        print("-" * 72)
        print(f"{'':30s} {'Baseline':>{w}s} {'Adaptive':>{w}s}")
        print(f"{'Trades on Bounce Days':30s} {bbs['count']:>{w}d} {bas['count']:>{w}d}")
        print(f"{'Win Rate':30s} {bbs['win_rate']:>{w}.1f}% {bas['win_rate']:>{w}.1f}%")
        print(f"{'Avg PnL/Trade':30s} {bbs['avg_pnl_pct']:>{w}.3f}% {bas['avg_pnl_pct']:>{w}.3f}%")
        print(f"{'Total PnL':30s} ${bbs['total_pnl']:>{w-1},.2f} ${bas['total_pnl']:>{w-1},.2f}")
        print()

    print("-" * 72)
    print(f"NORMAL DAY PERFORMANCE (identical between scenarios)")
    print("-" * 72)
    print(f"{'Trades':30s} {nbs['count']:>d}")
    print(f"{'Win Rate':30s} {nbs['win_rate']:.1f}%")
    print(f"{'Avg PnL/Trade':30s} {nbs['avg_pnl_pct']:.3f}%")
    print(f"{'Total PnL':30s} ${nbs['total_pnl']:,.2f}")
    print()

    print("-" * 72)
    print(f"NEW TRADES ANALYSIS (RSI {int(config.baseline_rsi)}-{int(config.adaptive_rsi)}, bounce days only)")
    print("-" * 72)
    if nw["count"] > 0:
        print(f"{'New Trades Added':30s} {nw['count']}")
        print(f"{'Win Rate':30s} {nw['win_rate']:.1f}%")
        print(f"{'Avg PnL/Trade':30s} {nw['avg_pnl_pct']:.3f}%")
        print(f"{'Total PnL':30s} ${nw['total_pnl']:,.2f}")
        print(f"{'Best New Trade':30s} {nw['best']}")
        print(f"{'Worst New Trade':30s} {nw['worst']}")
        print(f"{'Stopped Out':30s} {nw['stopped_out']}")
    else:
        print("  No new trades admitted (no bounce days with RSI-only rejections)")
    print()

    # Verdict
    print("=" * 72)
    if nw["count"] == 0:
        verdict = "INSUFFICIENT DATA"
        reason = "No new trades to evaluate — need more bounce days with RSI-only rejections."
    elif nw["count"] < 10:
        if nw["win_rate"] >= 55 and nw["avg_pnl_pct"] > 0:
            verdict = "EARLY POSITIVE — MORE DATA NEEDED"
            reason = f"New trades show {nw['win_rate']:.0f}% win rate / {nw['avg_pnl_pct']:.2f}% avg, but only {nw['count']} trades. Need 20+ for confidence."
        elif nw["avg_pnl_pct"] < 0:
            verdict = "EARLY NEGATIVE — MORE DATA NEEDED"
            reason = f"New trades avg {nw['avg_pnl_pct']:.2f}% PnL. Small sample ({nw['count']}), but not encouraging."
        else:
            verdict = "INCONCLUSIVE — MORE DATA NEEDED"
            reason = f"Only {nw['count']} new trades. Need 20+ for statistical confidence."
    else:
        if nw["win_rate"] >= 55 and nw["avg_pnl_pct"] > 0 and ad["sharpe"] >= bs["sharpe"] * 0.9:
            verdict = "ADOPT"
            reason = f"New trades: {nw['win_rate']:.0f}% WR, +{nw['avg_pnl_pct']:.2f}% avg. Adaptive Sharpe {ad['sharpe']:.2f} vs baseline {bs['sharpe']:.2f}."
        elif nw["win_rate"] < 50 or nw["avg_pnl_pct"] < 0:
            verdict = "REJECT"
            reason = f"New trades underperform: {nw['win_rate']:.0f}% WR, {nw['avg_pnl_pct']:.2f}% avg PnL."
        else:
            verdict = "MORE DATA NEEDED"
            reason = f"Marginal: {nw['win_rate']:.0f}% WR, {nw['avg_pnl_pct']:.2f}% avg. Collect more bounce-day data."

    print(f"VERDICT: {verdict}")
    print(f"  {reason}")
    print()

    # Caveats
    print("CAVEATS:")
    if signal_stats.get("source") == "enriched_json":
        print("  - Full filter chain applied (ETF, score, trend, RSI, SMA50, notional, sentiment, earnings).")
        print("  - Sentiment data missing for Jul-Aug 2025 (treated as pass).")
        print("  - TA data from ta_daily_close (prior trading day). ~10% of symbols may lack coverage.")
    else:
        print("  - Signals rejected by RSI were never checked against downstream filters")
        print("    (SMA50, sentiment, earnings). Actual new-trade count may be ~20% lower.")
        print("  - Notional filter IS applied (data available in signal_evaluations).")
    print(f"  - {signal_stats['days']} trading days of signal data.")
    print("=" * 72)

    return {
        "verdict": verdict,
        "reason": reason,
        "baseline": bs,
        "adaptive": ad,
        "new_trades": nw,
        "bounce_days": sorted(str(d) for d in bounce_days),
        "config": asdict(config),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="RSI Regime Backtest")
    parser.add_argument("--baseline-rsi", type=float, default=50.0)
    parser.add_argument("--adaptive-rsi", type=float, default=60.0)
    parser.add_argument("--min-red-days", type=int, default=2)
    parser.add_argument("--spy-bounce-pct", type=float, default=0.0)
    parser.add_argument("--from", dest="from_date", default="2025-07-01")
    parser.add_argument("--to", dest="to_date", default="2026-12-31")
    parser.add_argument("--output", default=None, help="JSON output file")
    parser.add_argument("--bars-dir", default=None)
    parser.add_argument("--input", default=None,
                        help="Path to enriched signals JSON (skips DB query)")
    args = parser.parse_args()

    config = BacktestConfig(
        baseline_rsi=args.baseline_rsi,
        adaptive_rsi=args.adaptive_rsi,
        min_red_days=args.min_red_days,
        spy_bounce_min_pct=args.spy_bounce_pct,
        from_date=args.from_date,
        to_date=args.to_date,
        output=args.output,
    )
    if args.bars_dir:
        config.bars_dir = args.bars_dir

    print("=" * 72)
    print("RSI REGIME BACKTEST — Bounce-Day Filter Relaxation")
    print("=" * 72)
    print(f"Baseline RSI: < {config.baseline_rsi}")
    print(f"Adaptive RSI: < {config.adaptive_rsi} (on bounce days)")
    print(f"Bounce def:   SPY green after {config.min_red_days}+ red closes")
    print()

    # ---- Step 1: SPY daily ----
    print("[1/7] Computing SPY daily from bar files...")
    spy_daily = compute_spy_daily(config.bars_dir, config.from_date, config.to_date)
    if not spy_daily:
        print("ERROR: No SPY daily data found. Check bars_dir path.")
        sys.exit(1)

    # ---- Step 2: Bounce days ----
    print(f"\n[2/7] Detecting bounce days (min {config.min_red_days} red days)...")
    bounce_days = detect_bounce_days(spy_daily, config.min_red_days)
    print(f"  Found {len(bounce_days)} bounce days")
    for d in sorted(bounce_days):
        spy_d = next((s for s in spy_daily if s.date == d), None)
        if spy_d:
            chg = (spy_d.close - spy_d.open_price) / spy_d.open_price * 100
            print(f"    {d}: SPY {spy_d.open_price:.2f} -> {spy_d.close:.2f} ({chg:+.2f}%)")

    # ---- Step 3: Load signal evaluations ----
    if args.input:
        print(f"\n[3/7] Loading enriched signals from {args.input}...")
        signals, sectors = load_enriched_signals(args.input, config.from_date, config.to_date)
    else:
        print(f"\n[3/7] Loading signal evaluations from DB...")
        conn = get_db_connection()
        signals = load_signal_evaluations(conn, config.from_date, config.to_date)
        sectors = load_sector_map(conn)
        conn.close()

    if not signals:
        print("ERROR: No signal evaluations found in date range.")
        sys.exit(1)

    # Signal stats
    signal_dates = set(s.detected_at.date() for s in signals)
    rsi_only_50_60 = sum(
        1 for s in signals
        if is_rsi_only_rejection(s.rejection_reason)
        and s.rsi_14 is not None
        and config.baseline_rsi <= s.rsi_14 < config.adaptive_rsi
    )
    signal_stats = {
        "total": len(signals),
        "days": len(signal_dates),
        "rsi_only_50_60": rsi_only_50_60,
        "source": "enriched_json" if args.input else "signal_evaluations",
    }

    # ---- Step 4: Classify signals ----
    print(f"\n[4/7] Classifying signals under both scenarios...")
    baseline_signals = [s for s in signals if passes_baseline(s)]
    adaptive_signals = [s for s in signals if passes_adaptive(s, bounce_days, config)]
    new_signals = [s for s in signals if is_new_trade(s, bounce_days, config)]

    print(f"  Baseline passes:  {len(baseline_signals)}")
    print(f"  Adaptive passes:  {len(adaptive_signals)} (+{len(new_signals)} new)")
    for ns in new_signals:
        print(f"    NEW: {ns.symbol} RSI={ns.rsi_14:.1f} score={ns.score_total} "
              f"notional=${ns.notional:,.0f} @ {ns.detected_at.strftime('%m/%d %H:%M')}")

    # ---- Step 5: Apply position limits ----
    print(f"\n[5/7] Applying position limits (max {config.max_positions}, max {config.max_per_sector}/sector)...")
    baseline_admitted = apply_position_limits(baseline_signals, sectors, config.max_positions, config.max_per_sector)
    adaptive_admitted = apply_position_limits(adaptive_signals, sectors, config.max_positions, config.max_per_sector)

    # Identify which new signals actually got admitted (weren't blocked by position limits)
    baseline_set = {(s.symbol, s.detected_at.date()) for s in baseline_admitted}
    new_admitted = [s for s in adaptive_admitted
                    if (s.symbol, s.detected_at.date()) not in baseline_set]

    print(f"  Baseline admitted: {len(baseline_admitted)} trades")
    print(f"  Adaptive admitted: {len(adaptive_admitted)} trades (+{len(new_admitted)} new)")

    # ---- Step 6: Simulate trades ----
    print(f"\n[6/7] Simulating trades with 1-min bars...")

    # Collect all symbols+dates needed
    all_admitted = set()
    for s in baseline_admitted + adaptive_admitted:
        all_admitted.add((s.detected_at.date(), s.symbol))

    # Load bars by date
    dates_needed = set(d for d, _ in all_admitted)
    day_bars_cache = {}
    for d in sorted(dates_needed):
        date_str = d.isoformat()
        symbols_today = {sym for dd, sym in all_admitted if dd == d}
        print(f"  Loading bars for {date_str} ({len(symbols_today)} symbols)...", end=" ")
        bars = load_bars_for_day(date_str, symbols_today, config.bars_dir)
        day_bars_cache[d] = bars
        print(f"{len(bars)} found")

    # Simulate baseline trades
    baseline_trades = []
    for sig in baseline_admitted:
        d = sig.detected_at.date()
        bars = day_bars_cache.get(d, {})
        if sig.symbol not in bars:
            continue
        trade = simulate_trade(sig, bars[sig.symbol], config, "baseline")
        if trade:
            baseline_trades.append(trade)

    # Simulate adaptive trades
    adaptive_trades = []
    for sig in adaptive_admitted:
        d = sig.detected_at.date()
        bars = day_bars_cache.get(d, {})
        if sig.symbol not in bars:
            continue
        trade = simulate_trade(sig, bars[sig.symbol], config, "adaptive")
        if trade:
            adaptive_trades.append(trade)

    # Isolate NEW trades
    new_trades = [t for t in adaptive_trades
                  if (t.symbol, t.signal_date) not in baseline_set]

    print(f"  Baseline trades simulated: {len(baseline_trades)}")
    print(f"  Adaptive trades simulated: {len(adaptive_trades)}")
    print(f"  New trades simulated:      {len(new_trades)}")

    # ---- Step 7: Report ----
    print(f"\n[7/7] Generating report...")
    total_days = len(set(s.date for s in spy_daily
                         if config.from_date <= s.date.isoformat() <= config.to_date))

    result = print_report(
        baseline_trades, adaptive_trades, new_trades,
        bounce_days, total_days, config, signal_stats,
    )

    # Print individual trade detail
    if new_trades:
        print()
        print("-" * 72)
        print("NEW TRADE DETAILS")
        print("-" * 72)
        print(f"{'Symbol':<8s} {'Date':<12s} {'Entry':>8s} {'Exit':>8s} {'PnL%':>8s} {'PnL$':>10s} {'Stop':>5s}")
        for t in sorted(new_trades, key=lambda x: x.signal_time):
            print(f"{t.symbol:<8s} {str(t.signal_date):<12s} "
                  f"${t.entry_price:>7.2f} ${t.exit_price:>7.2f} "
                  f"{t.pnl_pct*100:>+7.2f}% ${t.pnl_dollars:>+9.2f} "
                  f"{'YES' if t.stopped_out else '':>5s}")

    # JSON output
    if config.output:
        os.makedirs(os.path.dirname(config.output) or ".", exist_ok=True)
        output_data = {
            **result,
            "baseline_trades": [asdict(t) for t in baseline_trades],
            "adaptive_trades": [asdict(t) for t in adaptive_trades],
            "new_trades": [asdict(t) for t in new_trades],
        }
        # Convert dates/datetimes to strings
        def serialize(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return obj
        with open(config.output, "w") as f:
            json.dump(output_data, f, indent=2, default=serialize)
        print(f"\nResults saved to {config.output}")


if __name__ == "__main__":
    main()
