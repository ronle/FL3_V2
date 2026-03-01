"""
Cameron Backtest Engine — Phase 1.3

Tests Cameron-filtered stocks across 4 execution strategies and a parameter sweep.
Uses DuckDB for fast vectorized computation over the cameron_daily_universe.parquet.

Strategies:
  A) GAP_AND_GO_INTRADAY  — Entry: open, Exit: close, Stop: -X% intraday
  B) EOD_ENTRY_OVERNIGHT   — Entry: close, Exit: D+1 close, Stop: -X% on D+1
  C) NEXT_DAY_OPEN         — Entry: D+1 open, Exit: D+1 close, Stop: -X% on D+1
  D) CAMERON_PLUS_MOMENTUM — Cameron + momentum < -10%, Entry: close, Exit: D+1 close

Usage:
    python -m scripts.backtest_cameron
    python -m scripts.backtest_cameron --strategy GAP_AND_GO
    python -m scripts.backtest_cameron --quick   (single baseline config only)
"""

import argparse
import csv
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import List, Optional

import duckdb
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

UNIVERSE_PATH = "E:/backtest_cache/cameron_daily_universe.parquet"
RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backtest_results")


# ---------------------------------------------------------------------------
# Enums and Config
# ---------------------------------------------------------------------------

class Strategy(str, Enum):
    GAP_AND_GO = "GAP_AND_GO"
    EOD_OVERNIGHT = "EOD_OVERNIGHT"
    NEXT_DAY_OPEN = "NEXT_DAY_OPEN"
    CAMERON_PLUS_MOMENTUM = "CAMERON_PLUS_MOMENTUM"


@dataclass
class BacktestConfig:
    strategy: Strategy = Strategy.EOD_OVERNIGHT
    gap_pct_min: float = 0.04
    rvol_min: float = 5.0
    price_min: float = 1.0
    price_max: float = 20.0
    stop_pct: float = -0.03         # -3% hard stop
    slippage_pct: float = 0.001     # 0.1% per side
    max_positions: int = 10         # max concurrent positions per day
    position_size: float = 10_000   # $10K per position
    sort_by: str = "gap_pct"        # rank signals: gap_pct, rvol, or gap_pct*rvol
    mcap_max: Optional[float] = None
    momentum_max: float = -0.10     # only used for CAMERON_PLUS_MOMENTUM

    def label(self) -> str:
        parts = [
            self.strategy.value,
            f"gap>={self.gap_pct_min:.0%}",
            f"rvol>={self.rvol_min:.0f}x",
            f"${self.price_min:.0f}-${self.price_max:.0f}",
            f"stop={self.stop_pct:.0%}",
        ]
        if self.mcap_max:
            parts.append(f"mcap<{self.mcap_max/1e6:.0f}M")
        return " | ".join(parts)


@dataclass
class TradeResult:
    trade_date: str
    symbol: str
    strategy: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_dollar: float
    stopped_out: bool
    gap_pct: float
    rvol: float
    grade: str


@dataclass
class BacktestMetrics:
    config_label: str
    strategy: str
    total_signals: int
    total_trades: int
    skipped_no_data: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    median_pnl_pct: float
    total_pnl_pct: float
    total_pnl_dollar: float
    sharpe: float
    max_drawdown_pct: float
    profit_factor: float
    avg_gap_pct: float
    avg_rvol: float
    stop_out_rate: float
    signals_per_day: float
    trades_per_day: float
    # Per-year breakdown
    yearly: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core Backtest Logic
# ---------------------------------------------------------------------------

def compute_trade(row: dict, cfg: BacktestConfig) -> Optional[TradeResult]:
    """Simulate a single trade based on strategy and return the result."""
    strat = cfg.strategy
    slip = cfg.slippage_pct
    stop = cfg.stop_pct

    if strat == Strategy.GAP_AND_GO:
        entry_raw = row["open_price"]
        exit_raw = row["close_price"]
        intra_low = row["intraday_low"]
        if entry_raw is None or exit_raw is None or entry_raw <= 0:
            return None
        entry = entry_raw * (1 + slip)  # buy at open + slippage
        # Check stop: did intraday low breach stop level?
        stop_price = entry * (1 + stop)
        if intra_low is not None and intra_low <= stop_price:
            exit_price = stop_price  # stopped out
            stopped = True
        else:
            exit_price = exit_raw * (1 - slip)  # sell at close - slippage
            stopped = False

    elif strat == Strategy.EOD_OVERNIGHT:
        entry_raw = row["close_price"]
        exit_raw = row["next_day_close"]
        nd_low = row.get("next_day_low")
        if entry_raw is None or exit_raw is None or entry_raw <= 0:
            return None
        entry = entry_raw * (1 + slip)
        stop_price = entry * (1 + stop)
        if nd_low is not None and nd_low <= stop_price:
            exit_price = stop_price
            stopped = True
        else:
            exit_price = exit_raw * (1 - slip)
            stopped = False

    elif strat == Strategy.NEXT_DAY_OPEN:
        entry_raw = row.get("next_day_open")
        exit_raw = row.get("next_day_close")
        nd_low = row.get("next_day_low")
        if entry_raw is None or exit_raw is None or entry_raw <= 0:
            return None
        entry = entry_raw * (1 + slip)
        stop_price = entry * (1 + stop)
        if nd_low is not None and nd_low <= stop_price:
            exit_price = stop_price
            stopped = True
        else:
            exit_price = exit_raw * (1 - slip)
            stopped = False

    elif strat == Strategy.CAMERON_PLUS_MOMENTUM:
        # Same as EOD_OVERNIGHT but with momentum filter applied upstream
        entry_raw = row["close_price"]
        exit_raw = row["next_day_close"]
        nd_low = row.get("next_day_low")
        if entry_raw is None or exit_raw is None or entry_raw <= 0:
            return None
        entry = entry_raw * (1 + slip)
        stop_price = entry * (1 + stop)
        if nd_low is not None and nd_low <= stop_price:
            exit_price = stop_price
            stopped = True
        else:
            exit_price = exit_raw * (1 - slip)
            stopped = False
    else:
        return None

    pnl_pct = (exit_price - entry) / entry
    shares = max(1, int(cfg.position_size / entry))
    pnl_dollar = (exit_price - entry) * shares

    return TradeResult(
        trade_date=str(row["trade_date"]),
        symbol=row["symbol"],
        strategy=strat.value,
        entry_price=round(entry, 4),
        exit_price=round(exit_price, 4),
        pnl_pct=round(pnl_pct, 6),
        pnl_dollar=round(pnl_dollar, 2),
        stopped_out=stopped,
        gap_pct=row.get("gap_pct", 0),
        rvol=row.get("rvol", 0),
        grade=row.get("grade", ""),
    )


def compute_metrics(
    trades: List[TradeResult],
    total_signals: int,
    skipped: int,
    cfg: BacktestConfig,
    trading_days: int,
) -> BacktestMetrics:
    """Compute aggregate metrics from a list of trade results."""
    if not trades:
        return BacktestMetrics(
            config_label=cfg.label(), strategy=cfg.strategy.value,
            total_signals=total_signals, total_trades=0, skipped_no_data=skipped,
            wins=0, losses=0, win_rate=0, avg_pnl_pct=0, median_pnl_pct=0,
            total_pnl_pct=0, total_pnl_dollar=0, sharpe=0, max_drawdown_pct=0,
            profit_factor=0, avg_gap_pct=0, avg_rvol=0, stop_out_rate=0,
            signals_per_day=0, trades_per_day=0,
        )

    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n = len(pnls)

    avg_pnl = sum(pnls) / n
    sorted_pnls = sorted(pnls)
    median_pnl = sorted_pnls[n // 2] if n % 2 == 1 else (sorted_pnls[n // 2 - 1] + sorted_pnls[n // 2]) / 2

    # Sharpe: annualized from daily mean trade return
    std = (sum((p - avg_pnl) ** 2 for p in pnls) / n) ** 0.5 if n > 1 else 0
    # Trades per day average
    tpd = n / max(trading_days, 1)
    # Annualize: assume ~252 trading days, scale by trades_per_day
    sharpe = (avg_pnl / std) * math.sqrt(252 * max(tpd, 0.01)) if std > 0 else 0

    # Max drawdown (cumulative equity curve)
    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Profit factor
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

    # Per-year breakdown
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
        y["total_pnl_pct"] = round(y["total_pnl_pct"], 4)
        y["total_pnl_dollar"] = round(y["total_pnl_dollar"], 2)

    return BacktestMetrics(
        config_label=cfg.label(),
        strategy=cfg.strategy.value,
        total_signals=total_signals,
        total_trades=n,
        skipped_no_data=skipped,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n, 4),
        avg_pnl_pct=round(avg_pnl, 6),
        median_pnl_pct=round(median_pnl, 6),
        total_pnl_pct=round(sum(pnls), 4),
        total_pnl_dollar=round(sum(t.pnl_dollar for t in trades), 2),
        sharpe=round(sharpe, 2),
        max_drawdown_pct=round(max_dd, 4),
        profit_factor=round(pf, 2),
        avg_gap_pct=round(sum(t.gap_pct for t in trades) / n, 4),
        avg_rvol=round(sum(t.rvol for t in trades) / n, 2),
        stop_out_rate=round(sum(1 for t in trades if t.stopped_out) / n, 4),
        signals_per_day=round(total_signals / max(trading_days, 1), 2),
        trades_per_day=round(tpd, 2),
        yearly=yearly,
    )


# ---------------------------------------------------------------------------
# DuckDB-Based Signal Extraction
# ---------------------------------------------------------------------------

def load_filtered_signals(duck: duckdb.DuckDBPyConnection, cfg: BacktestConfig) -> pd.DataFrame:
    """
    Use DuckDB to filter the universe parquet and return matching rows.
    Much faster than Python-level iteration over 8M rows.
    """
    conditions = [
        f"gap_pct >= {cfg.gap_pct_min}",
        f"rvol >= {cfg.rvol_min}",
        "rvol < 1000000",  # sanity cap
        f"close_price >= {cfg.price_min}",
        f"close_price <= {cfg.price_max}",
        "gap_pct IS NOT NULL",
        "rvol IS NOT NULL",
        "open_price > 0",
        "close_price > 0",
    ]
    if cfg.mcap_max:
        conditions.append(f"(market_cap IS NULL OR market_cap <= {cfg.mcap_max})")

    # Strategy-specific: need next_day data for overnight/next-day strategies
    if cfg.strategy in (Strategy.EOD_OVERNIGHT, Strategy.CAMERON_PLUS_MOMENTUM):
        conditions.append("next_day_close IS NOT NULL")
    elif cfg.strategy == Strategy.NEXT_DAY_OPEN:
        conditions.append("next_day_open IS NOT NULL")
        conditions.append("next_day_close IS NOT NULL")

    # Momentum overlay for Strategy D
    momentum_col = ""
    momentum_join = ""
    if cfg.strategy == Strategy.CAMERON_PLUS_MOMENTUM:
        # Compute 20-day price momentum inline via window function
        # We'll do this as a CTE so DuckDB handles it efficiently
        momentum_col = ", momentum_20d"
        where = " AND ".join(conditions)
        query = f"""
            WITH base AS (
                SELECT *,
                    (close_price - LAG(close_price, 20) OVER (
                        PARTITION BY symbol ORDER BY trade_date
                    )) / NULLIF(LAG(close_price, 20) OVER (
                        PARTITION BY symbol ORDER BY trade_date
                    ), 0) AS momentum_20d
                FROM read_parquet('{UNIVERSE_PATH}')
            )
            SELECT *
            FROM base
            WHERE {where}
              AND momentum_20d IS NOT NULL
              AND momentum_20d <= {cfg.momentum_max}
            ORDER BY trade_date, gap_pct DESC
        """
    else:
        where = " AND ".join(conditions)
        query = f"""
            SELECT *
            FROM read_parquet('{UNIVERSE_PATH}')
            WHERE {where}
            ORDER BY trade_date, gap_pct DESC
        """

    df = duck.execute(query).fetchdf()
    return df


def run_single_config(duck: duckdb.DuckDBPyConnection, cfg: BacktestConfig) -> BacktestMetrics:
    """Run backtest for a single configuration."""
    t0 = time.time()

    df = load_filtered_signals(duck, cfg)
    total_signals = len(df)

    if total_signals == 0:
        log.info(f"  {cfg.label()} — 0 signals, skipping")
        return compute_metrics([], 0, 0, cfg, 0)

    # Grade each signal
    df["grade"] = "C"
    df.loc[(df["gap_pct"] >= 0.10) & (df["rvol"] >= 5.0), "grade"] = "A"
    mask_mcap = df["market_cap"].notna() & (df["market_cap"] < 100_000_000)
    df.loc[(df["gap_pct"] >= 0.10) & (df["rvol"] >= 5.0) & mask_mcap, "grade"] = "A+"
    df.loc[
        (df["grade"] == "C") & (df["gap_pct"] >= 0.04) & (df["rvol"] >= 3.0),
        "grade",
    ] = "B"

    # Apply max_positions cap per day (take top N by sort_by)
    trades = []
    skipped = 0
    for _, day_df in df.groupby("trade_date"):
        day_sorted = day_df.head(cfg.max_positions)
        for _, row in day_sorted.iterrows():
            result = compute_trade(row.to_dict(), cfg)
            if result:
                trades.append(result)
            else:
                skipped += 1

    trading_days = df["trade_date"].nunique()
    metrics = compute_metrics(trades, total_signals, skipped, cfg, trading_days)

    elapsed = time.time() - t0
    log.info(
        f"  {cfg.label()} — {metrics.total_trades} trades, "
        f"WR={metrics.win_rate:.1%}, avg={metrics.avg_pnl_pct:.2%}, "
        f"Sharpe={metrics.sharpe:.2f}, PF={metrics.profit_factor:.2f}, "
        f"MDD={metrics.max_drawdown_pct:.2%}, stop={metrics.stop_out_rate:.1%} [{elapsed:.1f}s]"
    )
    return metrics


# ---------------------------------------------------------------------------
# Configuration Sweep
# ---------------------------------------------------------------------------

def build_sweep_configs(strategy_filter: Optional[str] = None) -> List[BacktestConfig]:
    """Build the parameter sweep grid."""
    configs = []

    strategies = [Strategy.GAP_AND_GO, Strategy.EOD_OVERNIGHT, Strategy.NEXT_DAY_OPEN, Strategy.CAMERON_PLUS_MOMENTUM]
    if strategy_filter:
        strategies = [s for s in strategies if s.value == strategy_filter]

    gap_values = [0.04, 0.10, 0.20]
    rvol_values = [3.0, 5.0, 10.0]
    price_ranges = [(1.0, 20.0), (1.0, 10.0), (1.0, 50.0)]

    for strat in strategies:
        # Vary gap with default rvol=5, price $1-$20
        for gap in gap_values:
            configs.append(BacktestConfig(
                strategy=strat, gap_pct_min=gap, rvol_min=5.0,
                price_min=1.0, price_max=20.0,
            ))
        # Vary rvol with default gap=4%, price $1-$20 (skip rvol=5 already covered)
        for rv in rvol_values:
            if rv == 5.0:
                continue
            configs.append(BacktestConfig(
                strategy=strat, gap_pct_min=0.04, rvol_min=rv,
                price_min=1.0, price_max=20.0,
            ))
        # Vary price range with default gap=4%, rvol=5 (skip $1-$20 already covered)
        for pmin, pmax in price_ranges:
            if (pmin, pmax) == (1.0, 20.0):
                continue
            configs.append(BacktestConfig(
                strategy=strat, gap_pct_min=0.04, rvol_min=5.0,
                price_min=pmin, price_max=pmax,
            ))
        # Tighter stop (-2%) on baseline
        configs.append(BacktestConfig(
            strategy=strat, gap_pct_min=0.04, rvol_min=5.0,
            price_min=1.0, price_max=20.0, stop_pct=-0.02,
        ))
        # Wider stop (-5%) on baseline
        configs.append(BacktestConfig(
            strategy=strat, gap_pct_min=0.04, rvol_min=5.0,
            price_min=1.0, price_max=20.0, stop_pct=-0.05,
        ))

    # Deduplicate (some combos may overlap)
    seen = set()
    unique = []
    for c in configs:
        key = c.label()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_results(all_metrics: List[BacktestMetrics], all_trades: List[TradeResult]):
    """Save results to CSV and JSON."""
    os.makedirs(RESULTS_DIR, exist_ok=True)

    # Summary CSV (one row per config)
    csv_path = os.path.join(RESULTS_DIR, "cameron_summary.csv")
    fieldnames = [
        "config_label", "strategy", "total_signals", "total_trades", "skipped_no_data",
        "wins", "losses", "win_rate", "avg_pnl_pct", "median_pnl_pct",
        "total_pnl_pct", "total_pnl_dollar", "sharpe", "max_drawdown_pct",
        "profit_factor", "avg_gap_pct", "avg_rvol", "stop_out_rate",
        "signals_per_day", "trades_per_day",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in all_metrics:
            row = {k: getattr(m, k) for k in fieldnames}
            writer.writerow(row)
    log.info(f"Summary CSV: {csv_path} ({len(all_metrics)} configs)")

    # Full JSON with yearly breakdown
    json_path = os.path.join(RESULTS_DIR, "cameron_backtest_results.json")
    results_json = []
    for m in all_metrics:
        d = asdict(m)
        results_json.append(d)
    with open(json_path, "w") as f:
        json.dump(results_json, f, indent=2, default=str)
    log.info(f"Full JSON: {json_path}")

    # Trade-level CSV for deep analysis
    trades_csv_path = os.path.join(RESULTS_DIR, "cameron_trades.csv")
    if all_trades:
        trade_fields = [
            "trade_date", "symbol", "strategy", "entry_price", "exit_price",
            "pnl_pct", "pnl_dollar", "stopped_out", "gap_pct", "rvol", "grade",
        ]
        with open(trades_csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade_fields)
            writer.writeheader()
            for t in all_trades:
                writer.writerow(asdict(t))
        log.info(f"Trades CSV: {trades_csv_path} ({len(all_trades)} trades)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Cameron Scanner Backtest")
    parser.add_argument("--strategy", choices=[s.value for s in Strategy], help="Run single strategy only")
    parser.add_argument("--quick", action="store_true", help="Run baseline config only (fast)")
    args = parser.parse_args()

    if not os.path.exists(UNIVERSE_PATH):
        log.error(f"Missing universe: {UNIVERSE_PATH}")
        log.error("Run: python -m scripts.build_daily_universe --no-db")
        return

    duck = duckdb.connect()
    duck.execute("SET memory_limit = '8GB'")
    duck.execute("SET threads TO 8")

    # Quick row count
    total_rows = duck.execute(f"SELECT COUNT(*) FROM read_parquet('{UNIVERSE_PATH}')").fetchone()[0]
    log.info(f"Universe loaded: {total_rows:,} rows from {UNIVERSE_PATH}")

    if args.quick:
        # Just run the 4 strategies with baseline params
        configs = [
            BacktestConfig(strategy=Strategy.GAP_AND_GO),
            BacktestConfig(strategy=Strategy.EOD_OVERNIGHT),
            BacktestConfig(strategy=Strategy.NEXT_DAY_OPEN),
            BacktestConfig(strategy=Strategy.CAMERON_PLUS_MOMENTUM),
        ]
    else:
        configs = build_sweep_configs(strategy_filter=args.strategy)

    log.info(f"Running {len(configs)} configurations...")
    log.info("=" * 80)

    all_metrics = []
    all_trades = []
    t_total = time.time()

    for i, cfg in enumerate(configs, 1):
        log.info(f"[{i}/{len(configs)}] {cfg.label()}")

        # Collect trades for the baseline configs (first 4)
        metrics = run_single_config(duck, cfg)
        all_metrics.append(metrics)

        # For trade-level output, re-run baseline configs and collect trades
        if args.quick or i <= 4:
            df = load_filtered_signals(duck, cfg)
            if len(df) > 0:
                df["grade"] = "C"
                df.loc[(df["gap_pct"] >= 0.10) & (df["rvol"] >= 5.0), "grade"] = "A"
                for _, day_df in df.groupby("trade_date"):
                    for _, row in day_df.head(cfg.max_positions).iterrows():
                        result = compute_trade(row.to_dict(), cfg)
                        if result:
                            all_trades.append(result)

    elapsed_total = time.time() - t_total

    # Print top 10 by Sharpe
    log.info("=" * 80)
    log.info(f"SWEEP COMPLETE — {len(configs)} configs in {elapsed_total:.1f}s")
    log.info("")
    log.info("TOP 10 BY SHARPE:")
    log.info(f"{'Config':<65} {'Trades':>7} {'WR':>7} {'Avg%':>8} {'Sharpe':>7} {'PF':>6} {'MDD':>7}")
    log.info("-" * 110)
    sorted_metrics = sorted(all_metrics, key=lambda m: m.sharpe, reverse=True)
    for m in sorted_metrics[:10]:
        log.info(
            f"{m.config_label:<65} {m.total_trades:>7} {m.win_rate:>6.1%} "
            f"{m.avg_pnl_pct:>7.2%} {m.sharpe:>7.2f} {m.profit_factor:>5.2f} {m.max_drawdown_pct:>6.2%}"
        )

    # Per-year summary for top config
    if sorted_metrics and sorted_metrics[0].yearly:
        log.info("")
        log.info(f"TOP CONFIG YEARLY BREAKDOWN: {sorted_metrics[0].config_label}")
        for yr, y in sorted(sorted_metrics[0].yearly.items()):
            log.info(
                f"  {yr}: {y['trades']} trades, WR={y['win_rate']:.1%}, "
                f"avg={y['avg_pnl_pct']:.2%}, total=${y['total_pnl_dollar']:,.0f}"
            )

    save_results(all_metrics, all_trades)
    duck.close()


if __name__ == "__main__":
    main()
