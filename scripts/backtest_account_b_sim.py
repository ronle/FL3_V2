"""
Account B Backtest — Phase 2: Realistic Day-Trade Simulation

Uses 1-minute Polygon bars to simulate Account B trades with:
  - Entry: detection_time + 5 min, HIGH of bar x 1.001 (slippage)
  - Exit: EOD at 3:55 PM ET, LOW of bar x 0.999 (slippage)
  - Hard stop: -2% intraday, LOW of first crossing bar x 0.999
  - Position sizing: 10% of account per trade (fixed)
  - Max 10 concurrent positions

Data sources:
  - e2e_backtest_v2_strikes_sweeps_price_scored.json: scored signals (Jul 2025 - Jan 2026)
  - engulfing_scores (DB): bullish engulfing patterns (pattern_date)
  - polygon_data/stocks: 1-min OHLCV bars (YYYY-MM-DD.csv.gz)
"""

import asyncio
import csv
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, time as dt_time, timedelta
from typing import Optional, Dict, List

import asyncpg


# ── Configuration ─────────────────────────────────────────────────────
_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
DATABASE_URL = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL or "/cloudsql/" in DATABASE_URL:
    DATABASE_URL = _LOCAL_DB

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
POLYGON_DIR = os.path.join(BASE_DIR, "polygon_data", "stocks")
RESULTS_DIR = os.path.join(BASE_DIR, "backtest_results")
SIGNAL_FILE = os.environ.get("SIGNAL_FILE", os.path.join(
    BASE_DIR, "polygon_data", "backtest_results",
    "e2e_backtest_v2_strikes_sweeps_price_scored.json",
))

ACCOUNT_SIZE = 100_000
POSITION_PCT = 0.10        # 10% of account per trade
MIN_POSITION = 500         # Minimum $500 per trade
MAX_CONCURRENT = 10        # Max concurrent positions
SLIPPAGE_PCT = 0.001       # 0.1% each way
ENTRY_DELAY_MIN = 5        # Minutes after signal to enter
HARD_STOP_PCT = -0.02      # -2% hard stop
EXIT_TIME = dt_time(15, 55)  # 3:55 PM ET


@dataclass
class Trade:
    symbol: str
    score: int
    trigger_ts: datetime
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    shares: int = 0
    pnl_dollars: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""   # 'eod', 'hard_stop', 'no_data', 'no_entry'
    engulfing_type: str = ""


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

            ts = int(parts[idx["window_start"]]) // 1_000_000_000
            dt = datetime.fromtimestamp(ts)

            bars[sym].append({
                "time": dt,
                "open": float(parts[idx["open"]]),
                "high": float(parts[idx["high"]]),
                "low": float(parts[idx["low"]]),
                "close": float(parts[idx["close"]]),
                "volume": int(parts[idx["volume"]]),
            })

    for sym in bars:
        bars[sym].sort(key=lambda b: b["time"])

    return bars


def get_bar_at_or_after(bars: List[dict], target: datetime) -> Optional[dict]:
    for bar in bars:
        if bar["time"] >= target:
            return bar
    return None


def get_bar_at_or_before(bars: List[dict], target: datetime) -> Optional[dict]:
    result = None
    for bar in bars:
        if bar["time"] <= target:
            result = bar
        else:
            break
    return result


def get_bars_between(bars: List[dict], start: datetime, end: datetime) -> List[dict]:
    return [b for b in bars if start <= b["time"] <= end]


# ── Signal Loading ───────────────────────────────────────────────────
async def load_account_b_signals() -> List[dict]:
    """Load scored signals from JSON + match with engulfing from DB."""
    print(f"Loading signals from {os.path.basename(SIGNAL_FILE)}...")
    with open(SIGNAL_FILE) as f:
        data = json.load(f)

    all_raw = data["signals"]
    high_score = [s for s in all_raw if s.get("score", 0) >= 10]
    print(f"Total signals: {len(all_raw)}, score >= 10: {len(high_score)}")

    # Load engulfing from DB
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3, command_timeout=60)
    async with pool.acquire(timeout=30) as conn:
        engulfing_daily = await conn.fetch("""
            SELECT symbol, pattern_date::date as pdate
            FROM   engulfing_scores
            WHERE  direction = 'bullish' AND timeframe = '1D'
        """)
        engulfing_5min = await conn.fetch("""
            SELECT symbol, pattern_date::date as pdate
            FROM   engulfing_scores
            WHERE  direction = 'bullish' AND timeframe = '5min'
        """)
    await pool.close()

    daily_eng_set = {(r["symbol"], r["pdate"]) for r in engulfing_daily}
    fivemin_eng_set = {(r["symbol"], r["pdate"]) for r in engulfing_5min}
    print(f"Engulfing: {len(daily_eng_set)} daily + {len(fivemin_eng_set)} 5min pairs")

    # Match signals with engulfing
    matched = []
    for s in high_score:
        sym = s["symbol"]
        sig_date = date.fromisoformat(s["detection_time"][:10])

        eng_type = None
        for offset in range(0, 4):
            if (sym, sig_date - timedelta(days=offset)) in daily_eng_set:
                eng_type = "daily"
                break
        if not eng_type and (sym, sig_date) in fivemin_eng_set:
            eng_type = "5min"

        if eng_type:
            # Parse detection_time to datetime
            dt_str = s["detection_time"]
            # Format: "2025-10-15T14:30:00" or similar
            try:
                trigger_dt = datetime.fromisoformat(dt_str)
            except ValueError:
                trigger_dt = datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S")

            matched.append({
                "symbol": sym,
                "trigger_ts": trigger_dt,
                "score": s["score"],
                "engulfing_type": eng_type,
            })

    return matched


# ── Simulation ───────────────────────────────────────────────────────
def simulate_trade(
    signal: dict,
    bars: List[dict],
    account_value: float,
) -> Trade:
    """Simulate a single day trade with entry delay, hard stop, and EOD exit."""
    trade = Trade(
        symbol=signal["symbol"],
        score=signal["score"],
        trigger_ts=signal["trigger_ts"],
        engulfing_type=signal["engulfing_type"],
    )

    if not bars:
        trade.exit_reason = "no_data"
        return trade

    # ── Entry ─────────────────────────────────────────────────────
    entry_target = signal["trigger_ts"] + timedelta(minutes=ENTRY_DELAY_MIN)
    entry_bar = get_bar_at_or_after(bars, entry_target)

    if entry_bar is None:
        trade.exit_reason = "no_entry"
        return trade

    # Don't enter after 3:45 PM
    if entry_bar["time"].time() >= dt_time(15, 45):
        trade.exit_reason = "no_entry"
        return trade

    entry_price = entry_bar["high"] * (1 + SLIPPAGE_PCT)
    trade.entry_time = entry_bar["time"]
    trade.entry_price = entry_price

    # Position sizing: 10% of account
    dollar_amount = max(account_value * POSITION_PCT, MIN_POSITION)
    trade.shares = max(int(dollar_amount / entry_price), 1)

    # ── Exit scan: hard stop or EOD ───────────────────────────────
    exit_target = datetime.combine(entry_bar["time"].date(), EXIT_TIME)
    scan_bars = get_bars_between(bars, entry_bar["time"] + timedelta(minutes=1), exit_target)

    for bar in scan_bars:
        worst_price = bar["low"] * (1 - SLIPPAGE_PCT)
        pnl_pct = (worst_price - entry_price) / entry_price
        if pnl_pct <= HARD_STOP_PCT:
            stop_price = entry_price * (1 + HARD_STOP_PCT) * (1 - SLIPPAGE_PCT)
            trade.exit_time = bar["time"]
            trade.exit_price = stop_price
            trade.exit_reason = "hard_stop"
            break
    else:
        exit_bar = get_bar_at_or_before(bars, exit_target)
        if exit_bar and exit_bar["time"] > entry_bar["time"]:
            trade.exit_time = exit_bar["time"]
            trade.exit_price = exit_bar["low"] * (1 - SLIPPAGE_PCT)
            trade.exit_reason = "eod"
        else:
            trade.exit_time = entry_bar["time"]
            trade.exit_price = entry_bar["close"] * (1 - SLIPPAGE_PCT)
            trade.exit_reason = "eod_fallback"

    # ── P&L ───────────────────────────────────────────────────────
    if trade.exit_price and trade.entry_price:
        trade.pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price
        trade.pnl_dollars = trade.shares * trade.entry_price * trade.pnl_pct

    return trade


async def main():
    signals = await load_account_b_signals()

    if not signals:
        print("No Account B signals found!")
        return

    print(f"Found {len(signals)} Account B signals")

    # Group signals by date
    by_date: Dict[str, List[dict]] = defaultdict(list)
    for s in signals:
        by_date[s["trigger_ts"].strftime("%Y-%m-%d")].append(s)

    print(f"Spanning {len(by_date)} trading days")
    print(f"Polygon data dir: {POLYGON_DIR}")
    print()

    # ── Run simulation ────────────────────────────────────────────
    account_value = ACCOUNT_SIZE
    all_trades: List[Trade] = []
    daily_pnl = []
    peak = ACCOUNT_SIZE
    max_dd = 0.0
    dates_with_data = 0
    dates_without_data = 0

    sorted_dates = sorted(by_date.keys())
    for i, trade_date in enumerate(sorted_dates):
        day_signals = by_date[trade_date]
        symbols_needed = {s["symbol"] for s in day_signals}

        bars_by_symbol = load_polygon_day(trade_date, symbols_needed)
        if not bars_by_symbol:
            dates_without_data += 1
            for s in day_signals:
                t = Trade(
                    symbol=s["symbol"], score=s["score"],
                    trigger_ts=s["trigger_ts"], exit_reason="no_data",
                    engulfing_type=s["engulfing_type"],
                )
                all_trades.append(t)
            continue

        dates_with_data += 1
        day_pnl = 0.0
        day_trades = 0

        day_signals.sort(key=lambda s: s["trigger_ts"])

        for signal in day_signals:
            if day_trades >= MAX_CONCURRENT:
                t = Trade(
                    symbol=signal["symbol"], score=signal["score"],
                    trigger_ts=signal["trigger_ts"], exit_reason="max_positions",
                    engulfing_type=signal["engulfing_type"],
                )
                all_trades.append(t)
                continue

            sym_bars = bars_by_symbol.get(signal["symbol"], [])
            trade = simulate_trade(signal, sym_bars, account_value)
            all_trades.append(trade)

            if trade.exit_reason in ("eod", "hard_stop", "eod_fallback"):
                day_pnl += trade.pnl_dollars
                day_trades += 1

        account_value += day_pnl

        if account_value > peak:
            peak = account_value
        dd = (peak - account_value) / peak * 100
        max_dd = max(max_dd, dd)

        daily_pnl.append({
            "date": trade_date,
            "trades": day_trades,
            "pnl": day_pnl,
            "account": account_value,
        })

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(sorted_dates)} days... account=${account_value:,.0f}")

    # ── Results ───────────────────────────────────────────────────
    executed = [t for t in all_trades if t.exit_reason in ("eod", "hard_stop", "eod_fallback")]
    skipped = [t for t in all_trades if t.exit_reason in ("no_data", "no_entry", "max_positions")]

    wins = [t for t in executed if t.pnl_pct > 0]
    losses = [t for t in executed if t.pnl_pct <= 0]

    eod_exits = [t for t in executed if t.exit_reason in ("eod", "eod_fallback")]
    stop_exits = [t for t in executed if t.exit_reason == "hard_stop"]

    total_return = (account_value - ACCOUNT_SIZE) / ACCOUNT_SIZE * 100

    # Sharpe ratio
    daily_returns = []
    for day in daily_pnl:
        prev_val = day["account"] - day["pnl"]
        if prev_val > 0:
            daily_returns.append(day["pnl"] / prev_val * 100)

    if daily_returns and len(daily_returns) > 1:
        avg_daily = sum(daily_returns) / len(daily_returns)
        std_daily = math.sqrt(sum((r - avg_daily) ** 2 for r in daily_returns) / (len(daily_returns) - 1))
        sharpe = (avg_daily / std_daily) * math.sqrt(252) if std_daily > 0 else 0
    else:
        sharpe = 0

    # Profit factor
    gross_profit = sum(t.pnl_dollars for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_dollars for t in losses)) if losses else 1
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # ── Print Report ──────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Account B Backtest: Realistic Simulation")
    print("=" * 60)
    date_min = sorted_dates[0] if sorted_dates else "N/A"
    date_max = sorted_dates[-1] if sorted_dates else "N/A"
    print(f"Date range: {date_min} to {date_max}")
    print(f"Starting capital: ${ACCOUNT_SIZE:,}")
    print(f"Days with data: {dates_with_data}  |  Days without: {dates_without_data}")
    print()
    print(f"Total signals:    {len(all_trades)}")
    print(f"Executed trades:  {len(executed)}")
    print(f"Skipped:          {len(skipped)} (no_data={sum(1 for t in skipped if t.exit_reason=='no_data')}, "
          f"no_entry={sum(1 for t in skipped if t.exit_reason=='no_entry')}, "
          f"max_pos={sum(1 for t in skipped if t.exit_reason=='max_positions')})")
    print()

    if executed:
        print(f"Win rate:         {len(wins)/len(executed)*100:.1f}%")
        print(f"Avg win:          {sum(t.pnl_pct for t in wins)/len(wins)*100:+.2f}%" if wins else "Avg win:          N/A")
        print(f"Avg loss:         {sum(t.pnl_pct for t in losses)/len(losses)*100:+.2f}%" if losses else "Avg loss:         N/A")
        print(f"Avg trade:        {sum(t.pnl_pct for t in executed)/len(executed)*100:+.2f}%")
        print(f"Profit factor:    {profit_factor:.2f}")
        print()
        print(f"Exit breakdown:")
        print(f"  EOD (3:55 PM):  {len(eod_exits)} ({len(eod_exits)/len(executed)*100:.0f}%)")
        print(f"  Hard stop (-2%): {len(stop_exits)} ({len(stop_exits)/len(executed)*100:.0f}%)")
        print()

    print(f"Final equity:     ${account_value:,.0f} ({total_return:+.1f}%)")
    print(f"Max drawdown:     {max_dd:.1f}%")
    print(f"Sharpe ratio:     {sharpe:.2f}")
    print()

    # Monthly breakdown
    if executed:
        monthly = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
        for t in executed:
            m = t.trigger_ts.strftime("%Y-%m")
            monthly[m]["trades"] += 1
            monthly[m]["pnl"] += t.pnl_dollars
            if t.pnl_pct > 0:
                monthly[m]["wins"] += 1

        print("--- Monthly Breakdown ---")
        print(f"  {'Month':<10} {'Trades':>7} {'Win%':>7} {'P&L':>12}")
        for month in sorted(monthly.keys()):
            d = monthly[month]
            wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
            print(f"  {month:<10} {d['trades']:>7} {wr:>6.1f}% ${d['pnl']:>+10,.0f}")
        print()

    # Engulfing type breakdown
    if executed:
        daily_eng = [t for t in executed if t.engulfing_type == "daily"]
        fivemin_eng = [t for t in executed if t.engulfing_type == "5min"]
        print("--- By Engulfing Type ---")
        for label, group in [("Daily", daily_eng), ("5min", fivemin_eng)]:
            if group:
                g_wins = sum(1 for t in group if t.pnl_pct > 0)
                g_avg = sum(t.pnl_pct for t in group) / len(group) * 100
                print(f"  {label:<8} n={len(group):>4}  win={g_wins/len(group)*100:.1f}%  avg={g_avg:+.2f}%")
        print()

    # ── Save results ──────────────────────────────────────────────
    os.makedirs(RESULTS_DIR, exist_ok=True)

    trades_path = os.path.join(RESULTS_DIR, "account_b_trades.csv")
    with open(trades_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "score", "trigger_ts", "entry_time", "entry_price",
            "exit_time", "exit_price", "shares", "pnl_dollars", "pnl_pct",
            "exit_reason", "engulfing_type",
        ])
        for t in all_trades:
            writer.writerow([
                t.symbol, t.score, t.trigger_ts,
                t.entry_time or "", t.entry_price or "",
                t.exit_time or "", t.exit_price or "",
                t.shares, f"{t.pnl_dollars:.2f}", f"{t.pnl_pct:.6f}",
                t.exit_reason, t.engulfing_type,
            ])
    print(f"Trade log saved to: {trades_path}")

    equity_path = os.path.join(RESULTS_DIR, "account_b_equity.csv")
    with open(equity_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "trades", "pnl", "account"])
        for day in daily_pnl:
            writer.writerow([day["date"], day["trades"], f"{day['pnl']:.2f}", f"{day['account']:.2f}"])
    print(f"Equity curve saved to: {equity_path}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
