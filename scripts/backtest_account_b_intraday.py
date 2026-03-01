"""
Account B Intraday Backtest — Engulfing Patterns + 1-Min Polygon Bars

Simulates Account B with REAL intraday price data (Jan 2023 - Feb 2026):
  - Signal: Bullish engulfing (1D) from engulfing_scores
  - Entry: Next trading day at 9:35 AM ET, bar HIGH * 1.001 (slippage)
  - Exit: EOD at 3:55 PM ET, bar LOW * 0.999 (slippage)
  - Hard stop: -2% intraday, scanned on bar LOWs
  - Max 10 trades per day, $10K per trade
  - NO look-ahead: pattern known at close of Day T, trade on Day T+1

Data:
  - engulfing_scores (DB): bullish daily patterns with score/strength
  - polygon_data/stocks/*.csv.gz: 1-min OHLCV bars (~10K tickers/day)

Usage:
    python -m scripts.backtest_account_b_intraday
    python -m scripts.backtest_account_b_intraday --entry-time 10:00
    python -m scripts.backtest_account_b_intraday --min-score 0.65
"""

import argparse
import csv
import gzip
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Optional, Dict, List, Tuple

import psycopg2

# ── DB connection ────────────────────────────────────────────────────
_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
DATABASE_URL = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL or "/cloudsql/" in DATABASE_URL:
    DATABASE_URL = _LOCAL_DB

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
POLYGON_DIR = os.path.join(BASE_DIR, "polygon_data", "stocks")
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")

# ── Configuration ────────────────────────────────────────────────────
ACCOUNT_SIZE = 100_000
POSITION_SIZE = 10_000
MAX_TRADES_PER_DAY = 10
HARD_STOP_PCT = -0.02
SLIPPAGE_PCT = 0.001          # 0.1% each way
DEFAULT_ENTRY_TIME = dt_time(9, 35)
EXIT_TIME = dt_time(15, 55)
LAST_ENTRY_TIME = dt_time(15, 45)


# ── Data Structures ──────────────────────────────────────────────────
@dataclass
class EngulfingSignal:
    symbol: str
    pattern_date: date         # date pattern formed (trade next day)
    engulfing_score: float
    pattern_strength: str
    volume_confirmed: bool


@dataclass
class Trade:
    symbol: str
    trade_date: date
    engulfing_score: float
    pattern_strength: str
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    shares: int = 0
    pnl_dollars: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""      # 'eod', 'hard_stop', 'no_data', 'no_entry'


@dataclass
class ScenarioResult:
    name: str
    total_trades: int = 0
    executed_trades: int = 0
    no_data: int = 0
    no_entry: int = 0
    trading_days: int = 0
    wins: int = 0
    losses: int = 0
    hard_stops: int = 0
    eod_exits: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    max_drawdown: float = 0.0
    daily_returns: list = field(default_factory=list)
    monthly_pnl: dict = field(default_factory=dict)
    yearly_stats: dict = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        return self.wins / self.executed_trades if self.executed_trades > 0 else 0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.executed_trades if self.executed_trades > 0 else 0

    @property
    def profit_factor(self) -> float:
        return abs(self.gross_profit / self.gross_loss) if self.gross_loss != 0 else float('inf')

    @property
    def sharpe(self) -> float:
        if len(self.daily_returns) < 2:
            return 0.0
        avg = sum(self.daily_returns) / len(self.daily_returns)
        var = sum((r - avg) ** 2 for r in self.daily_returns) / (len(self.daily_returns) - 1)
        std = math.sqrt(var) if var > 0 else 0.0001
        return (avg / std) * math.sqrt(252)


# ── Polygon CSV Loading ──────────────────────────────────────────────
def load_polygon_day(trade_date: str, symbols: set) -> Dict[str, List[dict]]:
    """Load 1-min bars for given date and symbol set."""
    filepath = os.path.join(POLYGON_DIR, f"{trade_date}.csv.gz")
    if not os.path.exists(filepath):
        return {}

    bars: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(filepath, "rt") as f:
        header = f.readline().strip().split(",")
        idx = {col: i for i, col in enumerate(header)}

        for line in f:
            parts = line.strip().split(",")
            sym = parts[idx["ticker"]]
            if sym not in symbols:
                continue

            ts_ns = int(parts[idx["window_start"]])
            ts_s = ts_ns // 1_000_000_000
            dt = datetime.utcfromtimestamp(ts_s)
            # Polygon timestamps are UTC; convert to ET (EST = UTC-5, EDT = UTC-4)
            # Approximate: Jan-Mar, Nov-Dec = EST; Apr-Oct = EDT
            month = dt.month
            if month >= 4 and month <= 10:
                dt_et = dt - timedelta(hours=4)
            elif month == 3:
                # Approximate: DST starts 2nd Sunday of March
                dt_et = dt - timedelta(hours=4) if dt.day >= 10 else dt - timedelta(hours=5)
            elif month == 11:
                # Approximate: DST ends 1st Sunday of November
                dt_et = dt - timedelta(hours=5) if dt.day >= 3 else dt - timedelta(hours=4)
            else:
                dt_et = dt - timedelta(hours=5)

            bars[sym].append({
                "time": dt_et,
                "open": float(parts[idx["open"]]),
                "high": float(parts[idx["high"]]),
                "low": float(parts[idx["low"]]),
                "close": float(parts[idx["close"]]),
                "volume": int(parts[idx["volume"]]),
            })

    for sym in bars:
        bars[sym].sort(key=lambda b: b["time"])

    return bars


# ── Trade Simulation ─────────────────────────────────────────────────
def simulate_trade(
    signal: EngulfingSignal,
    bars: List[dict],
    trade_date: date,
    entry_time: dt_time,
) -> Trade:
    """Simulate one intraday trade with real 1-min bars."""
    trade = Trade(
        symbol=signal.symbol,
        trade_date=trade_date,
        engulfing_score=signal.engulfing_score,
        pattern_strength=signal.pattern_strength,
    )

    if not bars:
        trade.exit_reason = "no_data"
        return trade

    # ── Entry ────────────────────────────────────────────────────
    entry_target = datetime.combine(trade_date, entry_time)
    entry_bar = None
    for bar in bars:
        if bar["time"] >= entry_target:
            entry_bar = bar
            break

    if entry_bar is None or entry_bar["time"].time() >= LAST_ENTRY_TIME:
        trade.exit_reason = "no_entry"
        return trade

    entry_price = entry_bar["high"] * (1 + SLIPPAGE_PCT)
    trade.entry_time = entry_bar["time"]
    trade.entry_price = entry_price
    trade.shares = max(int(POSITION_SIZE / entry_price), 1)

    # ── Scan for hard stop or EOD exit ───────────────────────────
    exit_target = datetime.combine(trade_date, EXIT_TIME)

    for bar in bars:
        if bar["time"] <= entry_bar["time"]:
            continue
        if bar["time"] > exit_target:
            break

        # Check hard stop on bar LOW
        worst_price = bar["low"] * (1 - SLIPPAGE_PCT)
        pnl_check = (worst_price - entry_price) / entry_price
        if pnl_check <= HARD_STOP_PCT:
            stop_price = entry_price * (1 + HARD_STOP_PCT) * (1 - SLIPPAGE_PCT)
            trade.exit_time = bar["time"]
            trade.exit_price = stop_price
            trade.exit_reason = "hard_stop"
            break
    else:
        # EOD exit — find last bar at or before 3:55 PM
        eod_bar = None
        for bar in bars:
            if bar["time"] <= exit_target and bar["time"] > entry_bar["time"]:
                eod_bar = bar
        if eod_bar:
            trade.exit_time = eod_bar["time"]
            trade.exit_price = eod_bar["close"] * (1 - SLIPPAGE_PCT)
            trade.exit_reason = "eod"
        else:
            trade.exit_time = entry_bar["time"]
            trade.exit_price = entry_bar["close"] * (1 - SLIPPAGE_PCT)
            trade.exit_reason = "eod_fallback"

    # ── P&L ──────────────────────────────────────────────────────
    if trade.exit_price and trade.entry_price:
        trade.pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price
        trade.pnl_dollars = trade.shares * trade.entry_price * trade.pnl_pct

    return trade


# ── Signal Loading ────────────────────────────────────────────────────
def load_engulfing_signals(conn, min_score=0.0, strength=None) -> Dict[date, List[EngulfingSignal]]:
    """Load bullish daily engulfing patterns from DB, grouped by pattern_date."""
    where_clauses = [
        "timeframe = '1D'",
        "direction = 'bullish'",
        "pattern_date >= '2023-01-01'",
        "pattern_date < '2026-01-01'",
    ]
    params = []

    if min_score > 0:
        where_clauses.append(f"score >= %s")
        params.append(min_score)
    if strength:
        where_clauses.append(f"pattern_strength = %s")
        params.append(strength)

    sql = f"""
        SELECT symbol, pattern_date::date, score, pattern_strength, volume_confirmed
        FROM engulfing_scores
        WHERE {' AND '.join(where_clauses)}
        ORDER BY pattern_date, score DESC
    """

    signals: Dict[date, List[EngulfingSignal]] = defaultdict(list)
    with conn.cursor(name="eng_cursor") as cur:
        cur.itersize = 20_000
        cur.execute(sql, params)
        for row in cur:
            sig = EngulfingSignal(
                symbol=row[0],
                pattern_date=row[1],
                engulfing_score=float(row[2]) if row[2] else 0,
                pattern_strength=row[3] or "unknown",
                volume_confirmed=bool(row[4]),
            )
            signals[sig.pattern_date].append(sig)

    return signals


def get_trading_days() -> List[str]:
    """Get sorted list of available trading days from Polygon files."""
    files = os.listdir(POLYGON_DIR)
    days = sorted([f.replace(".csv.gz", "") for f in files if f.endswith(".csv.gz")])
    return days


# ── Output ────────────────────────────────────────────────────────────
def print_results(result: ScenarioResult):
    """Print comprehensive results."""
    print(f"\n{'='*80}")
    print(f"RESULTS: {result.name}")
    print(f"{'='*80}")
    print(f"  Total signals:   {result.total_trades:>7,}")
    print(f"  Executed trades: {result.executed_trades:>7,}")
    print(f"  No data:         {result.no_data:>7,}")
    print(f"  No entry:        {result.no_entry:>7,}")
    print(f"  Trading days:    {result.trading_days:>7,}")
    print()
    print(f"  Win Rate:        {result.win_rate:>7.1%}")
    print(f"  Hard Stops:      {result.hard_stops:>7,} ({result.hard_stops/max(result.executed_trades,1):.1%})")
    print(f"  EOD Exits:       {result.eod_exits:>7,}")
    print()
    print(f"  Total P/L:       ${result.total_pnl:>+10,.0f}")
    print(f"  Avg P/L/trade:   ${result.avg_pnl:>+10,.2f}")
    print(f"  Gross Profit:    ${result.gross_profit:>+10,.0f}")
    print(f"  Gross Loss:      ${result.gross_loss:>+10,.0f}")
    print(f"  Profit Factor:   {result.profit_factor:>7.2f}")
    print(f"  Sharpe Ratio:    {result.sharpe:>7.2f}")
    print(f"  Max Drawdown:    {result.max_drawdown:>7.1%}")

    # Yearly
    print(f"\n  {'Year':<6} {'Trades':>7} {'WR%':>6} {'P/L':>11} {'Avg':>9} {'Stops':>6}")
    print(f"  {'-'*50}")
    for year in sorted(result.yearly_stats.keys()):
        ys = result.yearly_stats[year]
        wr = ys["wins"] / ys["trades"] if ys["trades"] > 0 else 0
        avg = ys["pnl"] / ys["trades"] if ys["trades"] > 0 else 0
        print(f"  {year:<6} {ys['trades']:>7,} {wr:>5.1%} ${ys['pnl']:>10,.0f} ${avg:>8,.2f} {ys['stops']:>6}")

    # Monthly
    print(f"\n  {'':>6}", end="")
    for m in range(1, 13):
        print(f"{'  ' + datetime(2000, m, 1).strftime('%b'):>8}", end="")
    print(f"{'  TOTAL':>10}")
    print(f"  {'-'*110}")

    by_year = defaultdict(dict)
    for mk, mv in result.monthly_pnl.items():
        y, m = mk.split("-")
        by_year[y][int(m)] = mv

    for year in sorted(by_year.keys()):
        yt = 0
        print(f"  {year:<6}", end="")
        for m in range(1, 13):
            val = by_year[year].get(m, 0)
            yt += val
            if val == 0:
                print(f"{'--':>8}", end="")
            else:
                print(f"  {val:>+6,.0f}", end="")
        print(f"  {yt:>+8,.0f}")


# ── Main Simulation Loop ─────────────────────────────────────────────
def run_scenario(
    scenario_name: str,
    signals_by_pattern_date: Dict[date, List[EngulfingSignal]],
    trading_days: List[str],
    entry_time: dt_time,
    max_trades: int,
) -> ScenarioResult:
    """Run full simulation for one scenario."""
    result = ScenarioResult(name=scenario_name)
    equity = ACCOUNT_SIZE
    peak = equity

    # Build lookup: for each trading day, what signals are eligible?
    # Signals from pattern_date -> trade on next trading day
    day_index = {d: i for i, d in enumerate(trading_days)}

    # Map pattern_date to next trading day
    trade_day_signals: Dict[str, List[EngulfingSignal]] = defaultdict(list)
    for pdate, sigs in signals_by_pattern_date.items():
        pdate_str = pdate.isoformat()
        # Find next trading day after pattern_date
        if pdate_str in day_index:
            idx = day_index[pdate_str] + 1
            if idx < len(trading_days):
                trade_day = trading_days[idx]
                trade_day_signals[trade_day].extend(sigs)
        else:
            # Pattern date not a trading day; find next available
            for td in trading_days:
                if td > pdate_str:
                    trade_day_signals[td].extend(sigs)
                    break

    total_days = len(trading_days)
    processed = 0

    for td_str in trading_days:
        if td_str not in trade_day_signals:
            continue

        candidates = trade_day_signals[td_str]
        # Sort by score DESC, take top N
        candidates.sort(key=lambda s: s.engulfing_score, reverse=True)
        selected = candidates[:max_trades]

        td = date.fromisoformat(td_str)
        symbols_needed = {s.symbol for s in selected}

        # Load bars for needed symbols
        bars_by_sym = load_polygon_day(td_str, symbols_needed)

        result.trading_days += 1
        day_pnl = 0.0
        day_trades = 0

        for sig in selected:
            sym_bars = bars_by_sym.get(sig.symbol, [])
            trade = simulate_trade(sig, sym_bars, td, entry_time)
            result.total_trades += 1

            if trade.exit_reason == "no_data":
                result.no_data += 1
                continue
            if trade.exit_reason in ("no_entry",):
                result.no_entry += 1
                continue

            result.executed_trades += 1
            day_pnl += trade.pnl_dollars
            day_trades += 1

            if trade.exit_reason == "hard_stop":
                result.hard_stops += 1
            else:
                result.eod_exits += 1

            if trade.pnl_dollars > 0:
                result.wins += 1
                result.gross_profit += trade.pnl_dollars
            else:
                result.losses += 1
                result.gross_loss += trade.pnl_dollars

            # Yearly
            year = str(td.year)
            if year not in result.yearly_stats:
                result.yearly_stats[year] = {"trades": 0, "wins": 0, "pnl": 0.0, "stops": 0, "days": 0}
            ys = result.yearly_stats[year]
            ys["trades"] += 1
            if trade.pnl_dollars > 0:
                ys["wins"] += 1
            if trade.exit_reason == "hard_stop":
                ys["stops"] += 1
            ys["pnl"] += trade.pnl_dollars

        if day_trades > 0:
            result.total_pnl += day_pnl
            equity += day_pnl
            result.daily_returns.append(day_pnl / ACCOUNT_SIZE)

            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak
            if dd < result.max_drawdown:
                result.max_drawdown = dd

            month_key = f"{td.year}-{td.month:02d}"
            result.monthly_pnl[month_key] = result.monthly_pnl.get(month_key, 0) + day_pnl

            year = str(td.year)
            result.yearly_stats[year]["days"] += 1

        processed += 1
        if processed % 50 == 0:
            pct = processed / len(trade_day_signals) * 100
            print(f"  [{pct:5.1f}%] {td_str}  equity=${equity:,.0f}  "
                  f"trades={result.executed_trades}  WR={result.win_rate:.1%}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Account B Intraday Backtest")
    parser.add_argument("--min-score", type=float, default=0.0,
                       help="Minimum engulfing score")
    parser.add_argument("--strength", type=str, default=None,
                       choices=["strong", "moderate", "weak"],
                       help="Filter by pattern strength")
    parser.add_argument("--entry-time", type=str, default="09:35",
                       help="Entry time ET (default: 09:35)")
    parser.add_argument("--max-trades", type=int, default=MAX_TRADES_PER_DAY,
                       help=f"Max trades per day (default: {MAX_TRADES_PER_DAY})")
    parser.add_argument("--no-hard-stop", action="store_true",
                       help="Disable hard stop")
    args = parser.parse_args()

    h, m = args.entry_time.split(":")
    entry_time = dt_time(int(h), int(m))

    # Build scenario name
    parts = ["intraday"]
    if args.min_score > 0:
        parts.append(f"score>={args.min_score}")
    if args.strength:
        parts.append(args.strength)
    parts.append(f"entry@{args.entry_time}")
    scenario_name = " | ".join(parts)

    print(f"Account B Intraday Backtest (1-min Polygon bars)")
    print(f"  Period:     Jan 2023 - Dec 2025")
    print(f"  Account:    ${ACCOUNT_SIZE:,}")
    print(f"  Position:   ${POSITION_SIZE:,} per trade")
    print(f"  Max/day:    {args.max_trades}")
    print(f"  Entry time: {args.entry_time} ET")
    print(f"  Hard stop:  {HARD_STOP_PCT:.0%}")
    print(f"  Slippage:   {SLIPPAGE_PCT:.1%} each way")
    if args.min_score > 0:
        print(f"  Min score:  {args.min_score}")
    if args.strength:
        print(f"  Strength:   {args.strength}")
    print()

    # Load signals
    print("Loading engulfing patterns from DB...")
    conn = psycopg2.connect(DATABASE_URL)
    signals = load_engulfing_signals(conn, min_score=args.min_score, strength=args.strength)
    conn.close()
    total_sigs = sum(len(v) for v in signals.values())
    print(f"  {total_sigs:,} patterns across {len(signals):,} days")

    # Get trading days
    trading_days = get_trading_days()
    print(f"  {len(trading_days)} trading days with Polygon data")
    print()

    # Run simulation
    print("Running simulation...")
    t0 = time.time()
    result = run_scenario(scenario_name, signals, trading_days, entry_time, args.max_trades)
    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.0f}s")

    # Output
    print_results(result)

    # Save
    os.makedirs(RESULTS_DIR, exist_ok=True)
    score_tag = f"_score{args.min_score}" if args.min_score > 0 else ""
    strength_tag = f"_{args.strength}" if args.strength else ""
    save_path = os.path.join(RESULTS_DIR, f"account_b_intraday{score_tag}{strength_tag}.json")

    output = {
        "scenario": scenario_name,
        "config": {
            "entry_time": args.entry_time,
            "hard_stop": HARD_STOP_PCT,
            "slippage": SLIPPAGE_PCT,
            "max_trades": args.max_trades,
            "min_score": args.min_score,
            "strength": args.strength,
        },
        "results": {
            "total_signals": result.total_trades,
            "executed_trades": result.executed_trades,
            "no_data": result.no_data,
            "no_entry": result.no_entry,
            "win_rate": round(result.win_rate, 4),
            "hard_stops": result.hard_stops,
            "hard_stop_pct": round(result.hard_stops / max(result.executed_trades, 1), 4),
            "total_pnl": round(result.total_pnl, 2),
            "avg_pnl": round(result.avg_pnl, 2),
            "sharpe": round(result.sharpe, 2),
            "profit_factor": round(result.profit_factor, 2) if result.profit_factor != float('inf') else None,
            "max_drawdown": round(result.max_drawdown, 4),
        },
        "yearly": result.yearly_stats,
        "monthly_pnl": {k: round(v, 2) for k, v in result.monthly_pnl.items()},
    }
    with open(save_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to: {save_path}")


if __name__ == "__main__":
    main()
