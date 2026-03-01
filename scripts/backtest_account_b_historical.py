"""
Account B Historical Backtest — Engulfing + Options Activity

Simulates Account B strategy over Jan 2023 - Dec 2025 (3 years):
  - Primary gate: Bullish engulfing (1D) from engulfing_scores
  - Optional UOA proxy: volume_zscore from orats_daily
  - Returns: r_p1 from orats_daily_returns (close-to-close next day)
  - Hard stop: -2% (losses capped)
  - Max 10 trades per day, fixed $10K per trade

Runs multiple scenarios in a single pass and outputs comparison.

Usage:
    python -m scripts.backtest_account_b_historical
    python -m scripts.backtest_account_b_historical --max-trades 5
    python -m scripts.backtest_account_b_historical --no-hard-stop
"""

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional, Dict, List, Tuple

import psycopg2

# ── DB connection ────────────────────────────────────────────────────
_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
DATABASE_URL = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL or "/cloudsql/" in DATABASE_URL:
    DATABASE_URL = _LOCAL_DB

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")

# ── Configuration ────────────────────────────────────────────────────
ACCOUNT_SIZE = 100_000
POSITION_SIZE = 10_000       # Fixed $10K per trade
MAX_TRADES_PER_DAY = 10
HARD_STOP_PCT = -0.02        # -2% hard stop
SLIPPAGE_RT = 0.002          # 0.2% round-trip (0.1% each way)

# ── SQL ──────────────────────────────────────────────────────────────
FETCH_SQL = """
SELECT
    e.symbol,
    e.pattern_date::date as pattern_date,
    e.score as engulfing_score,
    e.pattern_strength,
    e.volume_confirmed,
    r.r_p1,
    r.r_p3,
    r.r_p5,
    r.px as close_price,
    o.volume_zscore,
    o.iv_rank,
    o.total_volume
FROM engulfing_scores e
JOIN orats_daily_returns r
    ON r.ticker = e.symbol AND r.trade_date = e.pattern_date::date
LEFT JOIN orats_daily o
    ON o.symbol = e.symbol AND o.asof_date = e.pattern_date::date
WHERE e.timeframe = '1D'
  AND e.direction = 'bullish'
  AND e.pattern_date >= '2023-01-01'
  AND e.pattern_date < '2026-01-01'
  AND r.r_p1 IS NOT NULL
ORDER BY e.pattern_date, e.score DESC
"""


# ── Data Structures ──────────────────────────────────────────────────
@dataclass
class Trade:
    symbol: str
    pattern_date: date
    engulfing_score: float
    pattern_strength: str
    volume_confirmed: bool
    r_p1: float
    r_p3: Optional[float]
    r_p5: Optional[float]
    close_price: float
    volume_zscore: Optional[float]
    iv_rank: Optional[float]
    total_volume: Optional[int]


@dataclass
class ScenarioResult:
    name: str
    total_trades: int = 0
    trading_days: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    max_drawdown: float = 0.0
    equity_peak: float = 0.0
    daily_returns: list = field(default_factory=list)
    monthly_pnl: dict = field(default_factory=dict)
    yearly_stats: dict = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades > 0 else 0

    @property
    def avg_return(self) -> float:
        return self.total_pnl / self.total_trades if self.total_trades > 0 else 0

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

    @property
    def avg_trades_per_day(self) -> float:
        return self.total_trades / self.trading_days if self.trading_days > 0 else 0


# ── Scenario Definitions ─────────────────────────────────────────────
def make_scenarios():
    """Define all scenarios to test."""
    scenarios = {}

    # Baseline: all bullish engulfing
    scenarios["all_engulfing"] = {
        "desc": "All bullish engulfing (no filter)",
        "filter": lambda t: True,
    }

    # By engulfing score
    scenarios["score_055+"] = {
        "desc": "Engulfing score >= 0.55",
        "filter": lambda t: t.engulfing_score >= 0.55,
    }
    scenarios["score_065+"] = {
        "desc": "Engulfing score >= 0.65",
        "filter": lambda t: t.engulfing_score >= 0.65,
    }

    # By strength
    scenarios["strong_only"] = {
        "desc": "Strong patterns only",
        "filter": lambda t: t.pattern_strength == "strong",
    }
    scenarios["strong_055+"] = {
        "desc": "Strong + score >= 0.55",
        "filter": lambda t: t.pattern_strength == "strong" and t.engulfing_score >= 0.55,
    }
    scenarios["strong_065+"] = {
        "desc": "Strong + score >= 0.65",
        "filter": lambda t: t.pattern_strength == "strong" and t.engulfing_score >= 0.65,
    }

    # Volume zscore (UOA proxy)
    scenarios["zscore_2+"] = {
        "desc": "Volume zscore >= 2.0",
        "filter": lambda t: t.volume_zscore is not None and t.volume_zscore >= 2.0,
    }
    scenarios["zscore_3+"] = {
        "desc": "Volume zscore >= 3.0",
        "filter": lambda t: t.volume_zscore is not None and t.volume_zscore >= 3.0,
    }

    # Combined best candidates
    scenarios["score_055_zscore_2"] = {
        "desc": "Score >= 0.55 + zscore >= 2.0",
        "filter": lambda t: t.engulfing_score >= 0.55
                        and t.volume_zscore is not None
                        and t.volume_zscore >= 2.0,
    }
    scenarios["strong_055_zscore_2"] = {
        "desc": "Strong + score >= 0.55 + zscore >= 2.0",
        "filter": lambda t: t.pattern_strength == "strong"
                        and t.engulfing_score >= 0.55
                        and t.volume_zscore is not None
                        and t.volume_zscore >= 2.0,
    }

    # Volume confirmed
    scenarios["vol_confirmed_055+"] = {
        "desc": "Volume confirmed + score >= 0.55",
        "filter": lambda t: t.volume_confirmed and t.engulfing_score >= 0.55,
    }

    return scenarios


# ── Simulation Engine ─────────────────────────────────────────────────
def simulate(trades_by_date: Dict[date, List[Trade]],
             scenario_filter,
             max_trades: int,
             hard_stop: float,
             use_hard_stop: bool,
             slippage: float = 0.0) -> ScenarioResult:
    """Run portfolio simulation for one scenario."""
    result = ScenarioResult(name="")
    equity = ACCOUNT_SIZE
    peak = equity

    sorted_dates = sorted(trades_by_date.keys())

    for d in sorted_dates:
        candidates = [t for t in trades_by_date[d] if scenario_filter(t)]
        if not candidates:
            continue

        # Already sorted by engulfing_score DESC from SQL
        selected = candidates[:max_trades]
        result.trading_days += 1

        day_pnl = 0.0
        for t in selected:
            ret = t.r_p1 - slippage  # deduct round-trip slippage
            if use_hard_stop and ret < hard_stop:
                ret = hard_stop

            pnl = POSITION_SIZE * ret
            day_pnl += pnl
            result.total_trades += 1

            if pnl > 0:
                result.wins += 1
                result.gross_profit += pnl
            else:
                result.losses += 1
                result.gross_loss += pnl

        result.total_pnl += day_pnl
        equity += day_pnl

        # Daily return as pct of account
        daily_ret = day_pnl / ACCOUNT_SIZE
        result.daily_returns.append(daily_ret)

        # Drawdown
        if equity > peak:
            peak = equity
        dd = (equity - peak) / peak
        if dd < result.max_drawdown:
            result.max_drawdown = dd
        result.equity_peak = peak

        # Monthly P/L
        month_key = f"{d.year}-{d.month:02d}"
        result.monthly_pnl[month_key] = result.monthly_pnl.get(month_key, 0) + day_pnl

        # Yearly tracking
        year = str(d.year)
        if year not in result.yearly_stats:
            result.yearly_stats[year] = {"trades": 0, "wins": 0, "pnl": 0.0, "days": 0}
        ys = result.yearly_stats[year]
        ys["trades"] += len(selected)
        ys["wins"] += sum(1 for t in selected if (t.r_p1 if not use_hard_stop else max(t.r_p1, hard_stop)) > 0)
        ys["pnl"] += day_pnl
        ys["days"] += 1

    return result


# ── Output Formatting ────────────────────────────────────────────────
def print_summary_table(results: Dict[str, ScenarioResult], scenarios: dict):
    """Print comparison table of all scenarios."""
    print("\n" + "=" * 130)
    print(f"{'Scenario':<35} {'Trades':>7} {'Days':>5} {'Avg/Day':>7} {'WR%':>6} "
          f"{'Avg Ret':>8} {'Total P/L':>11} {'Sharpe':>7} {'PF':>6} {'MaxDD':>7}")
    print("-" * 130)

    for key, result in sorted(results.items(), key=lambda x: -x[1].total_pnl):
        desc = scenarios[key]["desc"]
        if len(desc) > 34:
            desc = desc[:31] + "..."
        print(f"{desc:<35} {result.total_trades:>7,} {result.trading_days:>5} "
              f"{result.avg_trades_per_day:>7.1f} {result.win_rate:>5.1%} "
              f"{result.avg_return:>8.2f} ${result.total_pnl:>10,.0f} "
              f"{result.sharpe:>7.2f} {result.profit_factor:>6.2f} {result.max_drawdown:>6.1%}")

    print("=" * 130)


def print_yearly_breakdown(results: Dict[str, ScenarioResult], scenarios: dict):
    """Print year-by-year breakdown for top scenarios."""
    # Pick top 5 by total P/L
    top = sorted(results.items(), key=lambda x: -x[1].total_pnl)[:5]

    print("\n\nYEARLY BREAKDOWN (Top 5 scenarios by P/L)")
    print("=" * 100)

    for key, result in top:
        desc = scenarios[key]["desc"]
        print(f"\n  {desc}")
        print(f"  {'Year':<6} {'Trades':>7} {'WR%':>6} {'P/L':>11} {'Avg/Trade':>10} {'Days':>5}")
        print(f"  {'-'*50}")

        for year in sorted(result.yearly_stats.keys()):
            ys = result.yearly_stats[year]
            wr = ys["wins"] / ys["trades"] if ys["trades"] > 0 else 0
            avg_t = ys["pnl"] / ys["trades"] if ys["trades"] > 0 else 0
            print(f"  {year:<6} {ys['trades']:>7,} {wr:>5.1%} ${ys['pnl']:>10,.0f} "
                  f"${avg_t:>9,.2f} {ys['days']:>5}")


def print_monthly_heatmap(result: ScenarioResult, name: str):
    """Print monthly P/L for a specific scenario."""
    print(f"\n\nMONTHLY P/L: {name}")
    print("=" * 80)

    months = sorted(result.monthly_pnl.keys())
    if not months:
        print("  No trades")
        return

    # Group by year
    by_year = defaultdict(dict)
    for m in months:
        year, month = m.split("-")
        by_year[year][int(month)] = result.monthly_pnl[m]

    print(f"  {'Year':<6}", end="")
    for m in range(1, 13):
        print(f"{'  ' + datetime(2000, m, 1).strftime('%b'):>8}", end="")
    print(f"{'  TOTAL':>10}")
    print(f"  {'-'*110}")

    for year in sorted(by_year.keys()):
        year_total = 0
        print(f"  {year:<6}", end="")
        for m in range(1, 13):
            val = by_year[year].get(m, 0)
            year_total += val
            if val == 0:
                print(f"{'--':>8}", end="")
            elif val > 0:
                print(f"  {val:>+6,.0f}", end="")
            else:
                print(f"  {val:>+6,.0f}", end="")
        print(f"  {year_total:>+8,.0f}")


def save_results(results: Dict[str, ScenarioResult], scenarios: dict, filepath: str):
    """Save results to JSON file."""
    output = {}
    for key, result in results.items():
        output[key] = {
            "description": scenarios[key]["desc"],
            "total_trades": result.total_trades,
            "trading_days": result.trading_days,
            "win_rate": round(result.win_rate, 4),
            "avg_return_per_trade": round(result.avg_return, 2),
            "total_pnl": round(result.total_pnl, 2),
            "sharpe": round(result.sharpe, 2),
            "profit_factor": round(result.profit_factor, 2) if result.profit_factor != float('inf') else None,
            "max_drawdown": round(result.max_drawdown, 4),
            "yearly": result.yearly_stats,
            "monthly_pnl": {k: round(v, 2) for k, v in result.monthly_pnl.items()},
        }

    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to: {filepath}")


# ── Main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Account B Historical Backtest")
    parser.add_argument("--max-trades", type=int, default=MAX_TRADES_PER_DAY,
                       help=f"Max trades per day (default: {MAX_TRADES_PER_DAY})")
    parser.add_argument("--no-hard-stop", action="store_true",
                       help="Disable -2%% hard stop (use raw r_p1)")
    parser.add_argument("--slippage", type=float, default=SLIPPAGE_RT,
                       help=f"Round-trip slippage (default: {SLIPPAGE_RT})")
    parser.add_argument("--scenario", type=str, default=None,
                       help="Run only a specific scenario (by key name)")
    args = parser.parse_args()

    use_hard_stop = not args.no_hard_stop
    max_trades = args.max_trades
    slippage = args.slippage

    print(f"Account B Historical Backtest")
    print(f"  Period:     Jan 2023 - Dec 2025 (3 years)")
    print(f"  Account:    ${ACCOUNT_SIZE:,}")
    print(f"  Position:   ${POSITION_SIZE:,} per trade")
    print(f"  Max/day:    {max_trades}")
    print(f"  Hard stop:  {HARD_STOP_PCT:.0%}" if use_hard_stop else "  Hard stop:  DISABLED")
    print(f"  Slippage:   {slippage:.1%} round-trip")
    print()

    # Fetch data
    print("Fetching data from DB...")
    t0 = time.time()
    conn = psycopg2.connect(DATABASE_URL)

    trades_by_date: Dict[date, List[Trade]] = defaultdict(list)
    total_rows = 0

    with conn.cursor(name="bt_cursor") as cur:
        cur.itersize = 20_000
        cur.execute(FETCH_SQL)

        for row in cur:
            (symbol, pattern_date, engulfing_score, pattern_strength,
             volume_confirmed, r_p1, r_p3, r_p5, close_price,
             volume_zscore, iv_rank, total_volume) = row

            trade = Trade(
                symbol=symbol,
                pattern_date=pattern_date,
                engulfing_score=float(engulfing_score) if engulfing_score else 0,
                pattern_strength=pattern_strength or "unknown",
                volume_confirmed=bool(volume_confirmed),
                r_p1=float(r_p1),
                r_p3=float(r_p3) if r_p3 is not None else None,
                r_p5=float(r_p5) if r_p5 is not None else None,
                close_price=float(close_price) if close_price else 0,
                volume_zscore=float(volume_zscore) if volume_zscore is not None else None,
                iv_rank=float(iv_rank) if iv_rank is not None else None,
                total_volume=int(total_volume) if total_volume is not None else None,
            )
            trades_by_date[pattern_date].append(trade)
            total_rows += 1

    conn.close()
    elapsed = time.time() - t0
    print(f"  Loaded {total_rows:,} signals across {len(trades_by_date):,} trading days ({elapsed:.1f}s)")

    # Build scenarios
    scenarios = make_scenarios()
    if args.scenario:
        if args.scenario not in scenarios:
            print(f"ERROR: Unknown scenario '{args.scenario}'")
            print(f"Available: {', '.join(scenarios.keys())}")
            sys.exit(1)
        scenarios = {args.scenario: scenarios[args.scenario]}

    # Run all scenarios
    print(f"\nRunning {len(scenarios)} scenarios...")
    results: Dict[str, ScenarioResult] = {}

    for key, scenario in scenarios.items():
        result = simulate(
            trades_by_date=trades_by_date,
            scenario_filter=scenario["filter"],
            max_trades=max_trades,
            hard_stop=HARD_STOP_PCT,
            use_hard_stop=use_hard_stop,
            slippage=slippage,
        )
        result.name = scenario["desc"]
        results[key] = result
        print(f"  {scenario['desc']:<40} {result.total_trades:>7,} trades  "
              f"WR={result.win_rate:.1%}  P/L=${result.total_pnl:>+10,.0f}  "
              f"Sharpe={result.sharpe:.2f}")

    # Output
    print_summary_table(results, scenarios)
    print_yearly_breakdown(results, scenarios)

    # Monthly heatmap for best scenario
    best_key = max(results, key=lambda k: results[k].sharpe)
    print_monthly_heatmap(results[best_key], scenarios[best_key]["desc"])

    # Also show the "all_engulfing" baseline monthly
    if "all_engulfing" in results and best_key != "all_engulfing":
        print_monthly_heatmap(results["all_engulfing"], scenarios["all_engulfing"]["desc"])

    # Save
    stop_label = "hardstop" if use_hard_stop else "nostop"
    save_path = os.path.join(RESULTS_DIR, f"account_b_historical_{stop_label}.json")
    save_results(results, scenarios, save_path)


if __name__ == "__main__":
    main()
