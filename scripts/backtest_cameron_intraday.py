"""
Cameron Intraday Pattern Backtest — Phase 3.1

Replays historical 1-min bars through Cameron pattern detectors to simulate
true Cameron-style trades: pattern-based entries, pattern-derived stops,
rapid exits (5-30 min holds).

Input:
  - cameron_daily_universe.parquet (from Phase 1: gap%, rvol, price pre-computed)
  - stock_minutes.parquet (2.12B 1-min bars, 2020-2026)

For each trading day:
  1. Pre-filter Cameron candidates (gap>=4%, rvol>=5x, $1-$20)
  2. Load 1-min bars for candidates, 9:30-11:30 AM window
  3. Aggregate to 5-min candles rolling
  4. Run bull_flag, consolidation_breakout, vwap_reclaim detectors
  5. On trigger: simulate entry at pattern's entry_price
  6. Track stop/target hit using subsequent 1-min bars
  7. Record trade with hold time, pattern type, outcome

Usage:
    python -m scripts.backtest_cameron_intraday
    python -m scripts.backtest_cameron_intraday --start-date 2025-01-01
    python -m scripts.backtest_cameron_intraday --pattern bull_flag
    python -m scripts.backtest_cameron_intraday --quick  (10 random days for sanity check)
"""

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

import duckdb
import numpy as np
import pandas as pd

# Pattern imports — relative to FL3_V2 project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.patterns.bull_flag import detect_bull_flag, BullFlagPattern
from scripts.patterns.consolidation_breakout import detect_consolidation_breakout, ConsolidationBreakout
from scripts.patterns.vwap_reclaim import detect_vwap_reclaim, compute_vwap, VWAPReclaim

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

UNIVERSE_PATH = "E:/backtest_cache/cameron_daily_universe.parquet"
MINUTES_PATH = "E:/backtest_cache/stock_minutes.parquet"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_results")

# Cameron filter thresholds (from Phase 1 best config)
GAP_PCT_MIN = 0.04
RVOL_MIN = 5.0
PRICE_MIN = 1.0
PRICE_MAX = 20.0

# Trading window (ET)
SCAN_START_MINUTES = 15   # start scanning 15 min after open (9:45 AM)
SCAN_END_MINUTES = 120    # stop scanning 2 hours after open (11:30 AM)
MAX_HOLD_CANDLES = 10     # max hold = 10 five-min candles = 50 min
CANDLE_INTERVAL = 5       # 5-minute candles

# Slippage
SLIPPAGE_PCT = 0.001      # 0.1% per side (0.2% round trip)

# Max trades per day
MAX_TRADES_PER_DAY = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IntradayTrade:
    trade_date: str
    symbol: str
    pattern_type: str       # bull_flag, consolidation_breakout, vwap_reclaim
    pattern_strength: str   # strong, moderate, weak
    entry_time: str         # timestamp of entry
    exit_time: str          # timestamp of exit
    entry_price: float
    exit_price: float
    stop_loss: float
    target_1: float
    target_2: float
    pnl_pct: float
    pnl_dollar: float
    hold_minutes: int
    exit_reason: str        # "target_1", "target_2", "stop", "max_hold", "eod"
    gap_pct: float
    rvol: float
    stopped_out: bool
    exit_strategy: str = "target_1"  # which exit strategy was used for this trade


@dataclass
class IntradayMetrics:
    pattern_type: str
    exit_strategy: str
    total_signals: int      # Cameron candidates per day
    total_patterns: int     # patterns detected
    total_trades: int       # trades executed
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    median_pnl_pct: float
    sharpe: float
    profit_factor: float
    max_drawdown_pct: float
    avg_hold_minutes: float
    stop_out_rate: float
    trades_per_day: float
    yearly: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 5-min candle aggregation from 1-min bars
# ---------------------------------------------------------------------------

def aggregate_to_5min(bars_1m: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 1-min bars to 5-min candles.
    Input: DataFrame with columns [ts, open, high, low, close, volume]
    ts should be datetime, sorted ascending.
    """
    if bars_1m.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    df = bars_1m.copy()
    df["bucket"] = df["ts"].dt.floor(f"{CANDLE_INTERVAL}min")

    agg = df.groupby("bucket").agg(
        ts=("bucket", "first"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index(drop=True).sort_values("ts")

    return agg


# ---------------------------------------------------------------------------
# Trade simulation: walk forward through 1-min bars after pattern trigger
# ---------------------------------------------------------------------------

def simulate_trade(
    entry_price: float,
    stop_loss: float,
    target_1: float,
    target_2: float,
    bars_after: pd.DataFrame,
    exit_strategy: str = "target_1",
    max_hold_minutes: int = MAX_HOLD_CANDLES * CANDLE_INTERVAL,
) -> Tuple[float, str, str, int]:
    """
    Walk forward through 1-min bars after pattern trigger.
    Returns (exit_price, exit_reason, exit_time, hold_minutes).
    """
    if bars_after.empty:
        return entry_price, "no_data", "", 0

    # Apply slippage to entry
    entry_with_slip = entry_price * (1 + SLIPPAGE_PCT)

    for i, (_, bar) in enumerate(bars_after.iterrows()):
        bar_low = float(bar["low"])
        bar_high = float(bar["high"])
        bar_close = float(bar["close"])
        bar_ts = str(bar["ts"])
        minutes_held = (i + 1)

        # Check stop first (conservative: assume stop hit before target in same bar)
        if bar_low <= stop_loss:
            exit_p = stop_loss * (1 - SLIPPAGE_PCT)
            return exit_p, "stop", bar_ts, minutes_held

        # Check target
        if exit_strategy == "target_1" and bar_high >= target_1:
            exit_p = target_1 * (1 - SLIPPAGE_PCT)
            return exit_p, "target_1", bar_ts, minutes_held

        if exit_strategy == "target_2" and bar_high >= target_2:
            exit_p = target_2 * (1 - SLIPPAGE_PCT)
            return exit_p, "target_2", bar_ts, minutes_held

        # Max hold time
        if minutes_held >= max_hold_minutes:
            exit_p = bar_close * (1 - SLIPPAGE_PCT)
            return exit_p, "max_hold", bar_ts, minutes_held

    # Ran out of bars (EOD)
    last_bar = bars_after.iloc[-1]
    exit_p = float(last_bar["close"]) * (1 - SLIPPAGE_PCT)
    return exit_p, "eod", str(last_bar["ts"]), len(bars_after)


# ---------------------------------------------------------------------------
# Per-day processing
# ---------------------------------------------------------------------------

def process_day(
    trade_date: str,
    candidates: pd.DataFrame,
    bars_1m: pd.DataFrame,
    exit_strategy: str = "target_1",
    pattern_filter: Optional[str] = None,
    dual_exit: bool = False,
) -> Tuple[List[IntradayTrade], int]:
    """
    Process one trading day:
    1. For each Cameron candidate symbol with bars
    2. Aggregate 1-min to 5-min candles progressively
    3. At each 5-min candle (after SCAN_START), run pattern detectors
    4. On first trigger per symbol: simulate trade
    5. Max MAX_TRADES_PER_DAY trades per day

    If dual_exit=True, simulates BOTH target_1 and target_2 for each pattern detection.
    Returns trades tagged with exit_strategy in the exit_reason field.
    """
    trades = []
    patterns_found = 0
    symbols_traded = set()

    # Market open is the first bar timestamp
    if bars_1m.empty:
        return trades, 0

    # Group bars by symbol
    bars_by_symbol = {}
    for sym, grp in bars_1m.groupby("symbol"):
        bars_by_symbol[sym] = grp.sort_values("ts").reset_index(drop=True)

    # Process each candidate
    for _, cand in candidates.iterrows():
        sym = cand["symbol"]
        if sym not in bars_by_symbol:
            continue
        if sym in symbols_traded:
            continue
        if len(trades) >= MAX_TRADES_PER_DAY:
            break

        sym_bars = bars_by_symbol[sym]
        if len(sym_bars) < 20:  # need enough bars
            continue

        market_open_ts = sym_bars["ts"].iloc[0]

        # Compute VWAP from all 1-min bars (cumulative from open)
        vwap_series_1m = compute_vwap(sym_bars)

        # Walk through 5-min candle boundaries
        # Scan window: 15 min to 120 min after open
        for scan_minute in range(SCAN_START_MINUTES, SCAN_END_MINUTES + 1, CANDLE_INTERVAL):
            cutoff_ts = market_open_ts + pd.Timedelta(minutes=scan_minute)
            bars_so_far = sym_bars[sym_bars["ts"] <= cutoff_ts]

            if len(bars_so_far) < 6:  # need enough for pattern detection
                continue

            # Aggregate to 5-min candles
            candles_5m = aggregate_to_5min(bars_so_far)
            if len(candles_5m) < 4:
                continue

            # Also compute VWAP aligned to 5-min candles
            vwap_5m = compute_vwap(candles_5m)

            # Run pattern detectors
            detected = None

            if pattern_filter is None or pattern_filter == "bull_flag":
                bf = detect_bull_flag(sym, candles_5m, "5min")
                if bf and bf.pattern_strength != "weak":
                    detected = bf

            if detected is None and (pattern_filter is None or pattern_filter == "consolidation_breakout"):
                cb = detect_consolidation_breakout(
                    sym, candles_5m, "5min", gap_pct=float(cand.get("gap_pct", 0))
                )
                if cb and cb.pattern_strength != "weak":
                    detected = cb

            if detected is None and (pattern_filter is None or pattern_filter == "vwap_reclaim"):
                vr = detect_vwap_reclaim(sym, candles_5m, "5min", vwap_series=vwap_5m)
                if vr and vr.pattern_strength != "weak":
                    detected = vr

            if detected is None:
                continue

            patterns_found += 1

            # Simulate trade using 1-min bars AFTER the pattern detection point
            bars_after = sym_bars[sym_bars["ts"] > cutoff_ts].copy()
            if bars_after.empty:
                continue

            entry_price = detected.entry_price
            stop_loss = detected.stop_loss
            target_1 = detected.target_1
            target_2 = detected.target_2

            # Sanity: entry must be achievable (within reasonable range of current price)
            last_price = float(bars_so_far.iloc[-1]["close"])
            if entry_price > last_price * 1.05 or entry_price < last_price * 0.95:
                continue

            # Check if entry was triggered in subsequent bars
            entry_triggered = False
            entry_bar_idx = None
            for j in range(len(bars_after)):
                bar = bars_after.iloc[j]
                if float(bar["high"]) >= entry_price:
                    entry_triggered = True
                    entry_bar_idx = j
                    break
                # If stop hit before entry triggered, skip
                if float(bar["low"]) <= stop_loss:
                    break

            if not entry_triggered or entry_bar_idx is None:
                continue

            # Simulate from entry bar onward
            remaining_bars = bars_after.iloc[entry_bar_idx + 1:]
            entry_with_slip = entry_price * (1 + SLIPPAGE_PCT)
            entry_ts = str(bars_after.iloc[entry_bar_idx]["ts"])

            # Determine which exit strategies to simulate
            strategies_to_run = ["target_1", "target_2"] if dual_exit else [exit_strategy]

            for strat in strategies_to_run:
                exit_price, exit_reason, exit_time, hold_min = simulate_trade(
                    entry_price, stop_loss, target_1, target_2,
                    remaining_bars, strat,
                )

                pnl_pct = (exit_price - entry_with_slip) / entry_with_slip
                shares = max(1, int(10_000 / entry_with_slip))
                pnl_dollar = (exit_price - entry_with_slip) * shares

                trade = IntradayTrade(
                    trade_date=trade_date,
                    symbol=sym,
                    pattern_type=detected.pattern_type,
                    pattern_strength=detected.pattern_strength,
                    entry_time=entry_ts,
                    exit_time=exit_time,
                    entry_price=round(entry_with_slip, 4),
                    exit_price=round(exit_price, 4),
                    stop_loss=round(stop_loss, 4),
                    target_1=round(target_1, 4),
                    target_2=round(target_2, 4),
                    pnl_pct=round(pnl_pct, 6),
                    pnl_dollar=round(pnl_dollar, 2),
                    hold_minutes=hold_min,
                    exit_reason=exit_reason,
                    gap_pct=float(cand.get("gap_pct", 0)),
                    rvol=float(cand.get("rvol", 0)),
                    stopped_out=exit_reason == "stop",
                )
                # Tag trade with exit_strategy used
                trade.exit_strategy = strat
                trades.append(trade)

            symbols_traded.add(sym)
            break  # one trade per symbol, move to next

    return trades, patterns_found


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(
    trades: List[IntradayTrade],
    total_signals: int,
    total_patterns: int,
    pattern_type: str,
    exit_strategy: str,
    trading_days: int,
) -> IntradayMetrics:
    if not trades:
        return IntradayMetrics(
            pattern_type=pattern_type, exit_strategy=exit_strategy,
            total_signals=total_signals, total_patterns=total_patterns,
            total_trades=0, wins=0, losses=0, win_rate=0,
            avg_pnl_pct=0, median_pnl_pct=0, sharpe=0, profit_factor=0,
            max_drawdown_pct=0, avg_hold_minutes=0, stop_out_rate=0,
            trades_per_day=0,
        )

    pnls = [t.pnl_pct for t in trades]
    n = len(pnls)
    wins_list = [p for p in pnls if p > 0]
    losses_list = [p for p in pnls if p <= 0]
    avg = sum(pnls) / n
    sorted_p = sorted(pnls)
    median = sorted_p[n // 2] if n % 2 == 1 else (sorted_p[n // 2 - 1] + sorted_p[n // 2]) / 2

    std = (sum((p - avg) ** 2 for p in pnls) / n) ** 0.5 if n > 1 else 0
    tpd = n / max(trading_days, 1)
    sharpe = (avg / std) * math.sqrt(252 * max(tpd, 0.01)) if std > 0 else 0

    # Max drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    gp = sum(wins_list) if wins_list else 0
    gl = abs(sum(losses_list)) if losses_list else 0
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)

    hold_mins = [t.hold_minutes for t in trades]

    # Yearly
    yearly = {}
    for t in trades:
        yr = t.trade_date[:4]
        if yr not in yearly:
            yearly[yr] = {"trades": 0, "wins": 0, "total_pnl_pct": 0, "total_pnl_dollar": 0}
        yearly[yr]["trades"] += 1
        if t.pnl_pct > 0:
            yearly[yr]["wins"] += 1
        yearly[yr]["total_pnl_pct"] += t.pnl_pct
        yearly[yr]["total_pnl_dollar"] += t.pnl_dollar
    for yr in yearly:
        y = yearly[yr]
        y["win_rate"] = round(y["wins"] / y["trades"], 4) if y["trades"] else 0
        y["avg_pnl_pct"] = round(y["total_pnl_pct"] / y["trades"], 6) if y["trades"] else 0

    return IntradayMetrics(
        pattern_type=pattern_type,
        exit_strategy=exit_strategy,
        total_signals=total_signals,
        total_patterns=total_patterns,
        total_trades=n,
        wins=len(wins_list),
        losses=len(losses_list),
        win_rate=round(len(wins_list) / n, 4),
        avg_pnl_pct=round(avg, 6),
        median_pnl_pct=round(median, 6),
        sharpe=round(sharpe, 2),
        profit_factor=round(pf, 2),
        max_drawdown_pct=round(max_dd, 4),
        avg_hold_minutes=round(sum(hold_mins) / n, 1),
        stop_out_rate=round(sum(1 for t in trades if t.stopped_out) / n, 4),
        trades_per_day=round(tpd, 2),
        yearly=yearly,
    )


# ---------------------------------------------------------------------------
# Report + Save helper
# ---------------------------------------------------------------------------

def _report_and_save(
    trades: List[IntradayTrade],
    total_signals: int,
    total_patterns: int,
    pattern_label: str,
    exit_strategy: str,
    trading_days: int,
    suffix: str = "",
    pattern_filter: Optional[str] = None,
):
    """Print metrics to log and save CSV + JSON."""
    metrics = compute_metrics(
        trades, total_signals, total_patterns,
        pattern_label, exit_strategy, trading_days,
    )

    log.info("=" * 80)
    log.info(f"RESULTS: {pattern_label} | exit={exit_strategy} | suffix={suffix}")
    log.info(f"  Candidates: {total_signals:,}")
    log.info(f"  Patterns detected: {total_patterns:,}")
    log.info(f"  Trades executed: {metrics.total_trades}")
    log.info(f"  Win rate: {metrics.win_rate:.1%}")
    log.info(f"  Avg PnL: {metrics.avg_pnl_pct:.2%}")
    log.info(f"  Median PnL: {metrics.median_pnl_pct:.2%}")
    log.info(f"  Sharpe: {metrics.sharpe:.2f}")
    log.info(f"  Profit Factor: {metrics.profit_factor:.2f}")
    log.info(f"  Max Drawdown: {metrics.max_drawdown_pct:.2%}")
    log.info(f"  Avg Hold: {metrics.avg_hold_minutes:.1f} min")
    log.info(f"  Stop-out rate: {metrics.stop_out_rate:.1%}")
    log.info(f"  Trades/day: {metrics.trades_per_day:.2f}")

    if metrics.yearly:
        log.info("")
        log.info("  YEARLY BREAKDOWN:")
        for yr, y in sorted(metrics.yearly.items()):
            log.info(
                f"    {yr}: {y['trades']} trades, WR={y['win_rate']:.1%}, "
                f"avg={y['avg_pnl_pct']:.2%}, total=${y['total_pnl_dollar']:,.0f}"
            )

    # By pattern type
    if not pattern_filter:
        log.info("")
        log.info("  BY PATTERN TYPE:")
        for ptype in ["bull_flag", "consolidation_breakout", "vwap_reclaim"]:
            pt_trades = [t for t in trades if t.pattern_type == ptype]
            if pt_trades:
                pt_m = compute_metrics(pt_trades, 0, 0, ptype, exit_strategy, trading_days)
                log.info(
                    f"    {ptype:<28} {pt_m.total_trades:>5} trades, "
                    f"WR={pt_m.win_rate:.1%}, avg={pt_m.avg_pnl_pct:.2%}, "
                    f"Sharpe={pt_m.sharpe:.2f}, hold={pt_m.avg_hold_minutes:.0f}min, "
                    f"stop={pt_m.stop_out_rate:.1%}"
                )

    # By strength
    log.info("")
    log.info("  BY PATTERN STRENGTH:")
    for strength in ["strong", "moderate"]:
        s_trades = [t for t in trades if t.pattern_strength == strength]
        if s_trades:
            s_m = compute_metrics(s_trades, 0, 0, strength, exit_strategy, trading_days)
            log.info(
                f"    {strength:<28} {s_m.total_trades:>5} trades, "
                f"WR={s_m.win_rate:.1%}, avg={s_m.avg_pnl_pct:.2%}, "
                f"Sharpe={s_m.sharpe:.2f}"
            )

    # Save trades CSV
    trades_path = os.path.join(RESULTS_DIR, f"cameron_intraday_trades{suffix}.csv")
    if trades:
        fields = list(asdict(trades[0]).keys())
        with open(trades_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for t in trades:
                writer.writerow(asdict(t))
        log.info(f"\nTrades saved: {trades_path} ({len(trades)} trades)")

    # Save summary JSON
    summary_path = os.path.join(RESULTS_DIR, f"cameron_intraday_summary{suffix}.json")
    with open(summary_path, "w") as f:
        json.dump(asdict(metrics), f, indent=2, default=str)
    log.info(f"Summary saved: {summary_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cameron Intraday Pattern Backtest")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-12-31")
    parser.add_argument("--pattern", choices=["bull_flag", "consolidation_breakout", "vwap_reclaim"],
                        help="Test single pattern only")
    parser.add_argument("--exit", choices=["target_1", "target_2"], default="target_1",
                        help="Exit strategy (default: target_1 = 1:1 R:R)")
    parser.add_argument("--dual-exit", action="store_true",
                        help="Simulate BOTH exit strategies in a single pass (outputs two CSVs)")
    parser.add_argument("--output-suffix", default="",
                        help="Suffix for output files (e.g., '_e2e' -> cameron_intraday_trades_e2e.csv)")
    parser.add_argument("--quick", action="store_true", help="Sample 10 days for sanity check")
    args = parser.parse_args()

    for path, name in [(UNIVERSE_PATH, "universe"), (MINUTES_PATH, "stock_minutes")]:
        if not os.path.exists(path):
            log.error(f"Missing {name}: {path}")
            return

    duck = duckdb.connect()
    duck.execute("SET memory_limit = '8GB'")
    duck.execute("SET threads TO 8")

    t_total = time.time()

    # Step 1: Get Cameron candidate days and symbols from universe
    log.info("Loading Cameron candidates from universe...")
    candidates_df = duck.execute(f"""
        SELECT trade_date, symbol, gap_pct, rvol, open_price, close_price
        FROM read_parquet('{UNIVERSE_PATH}')
        WHERE gap_pct >= {GAP_PCT_MIN}
          AND rvol >= {RVOL_MIN} AND rvol < 1000000
          AND close_price >= {PRICE_MIN} AND close_price <= {PRICE_MAX}
          AND trade_date BETWEEN '{args.start_date}' AND '{args.end_date}'
        ORDER BY trade_date, gap_pct DESC
    """).fetchdf()
    log.info(f"  {len(candidates_df):,} candidate rows across {candidates_df['trade_date'].nunique()} days")

    # Group by date, cap per day
    candidates_by_date = {}
    for dt, grp in candidates_df.groupby("trade_date"):
        dt_str = str(dt.date()) if hasattr(dt, "date") else str(dt)[:10]
        candidates_by_date[dt_str] = grp.head(30)  # max 30 candidates per day

    trading_dates = sorted(candidates_by_date.keys())

    if args.quick:
        import random
        random.seed(42)
        trading_dates = sorted(random.sample(trading_dates, min(10, len(trading_dates))))
        log.info(f"  Quick mode: sampling {len(trading_dates)} days")

    # Step 2: Process each day
    all_trades = []
    total_patterns_found = 0
    total_signals = len(candidates_df)

    for i, date_str in enumerate(trading_dates):
        cands = candidates_by_date[date_str]
        symbols = cands["symbol"].tolist()

        if not symbols:
            continue

        # Load 1-min bars for these symbols on this date
        # Filter to 9:30 AM - 11:30 AM ET window (scan window)
        # Plus extend to 12:30 PM for trade completion (max 50 min hold after 11:30)
        sym_list = "', '".join(symbols)
        bars_query = f"""
            SELECT symbol,
                   timestamp_et AS ts,
                   open, high, low, close, volume
            FROM read_parquet('{MINUTES_PATH}')
            WHERE CAST(timestamp_et AS DATE) = '{date_str}'
              AND symbol IN ('{sym_list}')
              AND EXTRACT(HOUR FROM timestamp_et) >= 9
              AND (EXTRACT(HOUR FROM timestamp_et) < 12
                   OR (EXTRACT(HOUR FROM timestamp_et) = 12 AND EXTRACT(MINUTE FROM timestamp_et) <= 30))
            ORDER BY symbol, timestamp_et
        """

        try:
            bars_1m = duck.execute(bars_query).fetchdf()
        except Exception as e:
            log.warning(f"  {date_str}: Error loading bars: {e}")
            continue

        if bars_1m.empty:
            continue

        day_trades, patterns = process_day(
            date_str, cands, bars_1m,
            exit_strategy=args.exit,
            pattern_filter=args.pattern,
            dual_exit=args.dual_exit,
        )
        all_trades.extend(day_trades)
        total_patterns_found += patterns

        if (i + 1) % 20 == 0 or (i + 1) == len(trading_dates):
            log.info(
                f"  [{i+1}/{len(trading_dates)}] {date_str}: "
                f"{len(cands)} candidates, {patterns} patterns, "
                f"{len(day_trades)} trades (running total: {len(all_trades)})"
            )

    elapsed = time.time() - t_total
    log.info(f"\nBacktest complete in {elapsed:.1f}s")

    # Step 3: Compute and display metrics
    os.makedirs(RESULTS_DIR, exist_ok=True)
    suffix = args.output_suffix
    pattern_label = args.pattern or "all_patterns"

    if args.dual_exit:
        # Split trades by exit strategy
        for strat in ["target_1", "target_2"]:
            strat_trades = [t for t in all_trades if t.exit_strategy == strat]
            _report_and_save(
                strat_trades, total_signals, total_patterns_found,
                pattern_label, strat, len(trading_dates),
                suffix=f"_{strat}{suffix}", pattern_filter=args.pattern,
            )
    else:
        _report_and_save(
            all_trades, total_signals, total_patterns_found,
            pattern_label, args.exit, len(trading_dates),
            suffix=suffix, pattern_filter=args.pattern,
        )

    duck.close()


if __name__ == "__main__":
    main()
