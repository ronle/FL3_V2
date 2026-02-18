"""
Account B Backtest — Phase 1: Signal Overlap Analysis

Measures how often scored UOA signals (score >= 10) overlap with bullish
engulfing patterns, and compares forward returns between engulfing-confirmed vs
unconfirmed signals.

Data sources:
  - e2e_backtest_v2_strikes_sweeps_price_scored.json: 455K scored signals (Jul 2025 - Jan 2026)
  - engulfing_scores (DB): bullish engulfing patterns (pattern_date 2023-2025)
  - orats_daily_returns (DB): forward returns (r_p1..r_p10, 2024 - Jan 2026)
  - paper_trades_log (DB): Account A live trades (for comparison)

Groups:
  A = "Account B signals" (score >= 10 + engulfing on same date or prior day)
  B = "Account A signals" (from paper_trades_log — live trades)
  C = "All signals"       (score >= 10, no filter)
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, date
from collections import defaultdict
from decimal import Decimal

import asyncpg


_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
DATABASE_URL = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL or "/cloudsql/" in DATABASE_URL:
    DATABASE_URL = _LOCAL_DB

SIGNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "polygon_data", "backtest_results",
    "e2e_backtest_v2_strikes_sweeps_price_scored.json",
)


def to_float(v):
    if v is None:
        return None
    return float(v)


async def main():
    # ── Load scored signals from JSON ─────────────────────────────
    print(f"Loading signals from {os.path.basename(SIGNAL_FILE)}...")
    with open(SIGNAL_FILE) as f:
        data = json.load(f)

    all_raw = data["signals"]
    high_score = [s for s in all_raw if s.get("score", 0) >= 10]
    print(f"Total signals: {len(all_raw)}, score >= 10: {len(high_score)}")

    # Get unique dates and symbols for DB queries
    signal_dates = set()
    signal_symbols = set()
    for s in high_score:
        d = s["detection_time"][:10]
        signal_dates.add(d)
        signal_symbols.add(s["symbol"])

    print(f"Unique dates: {len(signal_dates)}, unique symbols: {len(signal_symbols)}")

    # ── DB queries: engulfing + forward returns + Account A ───────
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3, command_timeout=120)

    async with pool.acquire(timeout=30) as conn:
        # Engulfing patterns — all bullish, match on pattern_date
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
        print(f"Loaded {len(engulfing_daily)} daily + {len(engulfing_5min)} 5min engulfing patterns")

        # Forward returns for signal dates
        returns = await conn.fetch("""
            SELECT ticker, trade_date, r_p1, r_p3, r_p5, r_p10
            FROM   orats_daily_returns
            WHERE  trade_date >= $1 AND trade_date <= $2
        """, date.fromisoformat(min(signal_dates)), date.fromisoformat(max(signal_dates)))
        print(f"Loaded {len(returns)} forward return rows")

        # Account A live trades
        acct_a_trades = await conn.fetch("""
            SELECT symbol, entry_time, pnl_pct, exit_reason, signal_score
            FROM   paper_trades_log
            ORDER BY entry_time
        """)
        print(f"Loaded {len(acct_a_trades)} Account A live trades")

    await pool.close()

    # ── Build lookup indices ──────────────────────────────────────
    daily_eng_set = {(r["symbol"], r["pdate"]) for r in engulfing_daily}
    fivemin_eng_set = {(r["symbol"], r["pdate"]) for r in engulfing_5min}
    print(f"Unique daily engulfing pairs: {len(daily_eng_set)}, 5min: {len(fivemin_eng_set)}")

    # Returns: (ticker, date) → dict
    returns_lookup = {}
    for r in returns:
        returns_lookup[(r["ticker"], r["trade_date"])] = {
            "r_p1": to_float(r["r_p1"]),
            "r_p3": to_float(r["r_p3"]),
            "r_p5": to_float(r["r_p5"]),
            "r_p10": to_float(r["r_p10"]),
        }

    # ── Match signals with engulfing + forward returns ────────────
    group_a = []   # Account B: score >= 10 + engulfing
    group_c = []   # All: score >= 10
    no_engulfing = []

    for s in high_score:
        sym = s["symbol"]
        sig_date = date.fromisoformat(s["detection_time"][:10])

        # Check daily engulfing (same date or up to 3 days prior for weekends)
        has_daily = False
        for offset in range(0, 4):
            if (sym, sig_date - timedelta(days=offset)) in daily_eng_set:
                has_daily = True
                break

        # Check 5min engulfing (same date only)
        has_5min = (sym, sig_date) in fivemin_eng_set if not has_daily else False
        has_engulfing = has_daily or has_5min

        # Attach forward returns
        ret = returns_lookup.get((sym, sig_date), {})

        record = {
            "symbol": sym,
            "detection_time": s["detection_time"],
            "date": sig_date,
            "score": s["score"],
            "notional": s.get("notional"),
            "ratio": s.get("ratio"),
            "trend": s.get("trend"),
            "has_engulfing": has_engulfing,
            "engulfing_type": "daily" if has_daily else ("5min" if has_5min else None),
            "r_p1": ret.get("r_p1"),
            "r_p3": ret.get("r_p3"),
            "r_p5": ret.get("r_p5"),
            "r_p10": ret.get("r_p10"),
        }

        group_c.append(record)
        if has_engulfing:
            group_a.append(record)
        else:
            no_engulfing.append(record)

    # ── Date range ────────────────────────────────────────────────
    dates = [r["date"] for r in group_c]
    date_min = min(dates).isoformat() if dates else "N/A"
    date_max = max(dates).isoformat() if dates else "N/A"

    # ── Statistics helpers ────────────────────────────────────────
    def stats(group, label):
        labeled = [r for r in group if r["r_p1"] is not None]
        n_total = len(group)
        n_labeled = len(labeled)

        if not labeled:
            return {
                "label": label, "n_total": n_total, "n_labeled": 0,
                "avg_1d": None, "avg_5d": None, "avg_10d": None,
                "success_5d": None, "median_max": None,
            }

        avg_1d = sum(r["r_p1"] for r in labeled) / n_labeled

        r5 = [r for r in labeled if r["r_p5"] is not None]
        avg_5d = sum(r["r_p5"] for r in r5) / len(r5) if r5 else None

        r10 = [r for r in labeled if r["r_p10"] is not None]
        avg_10d = sum(r["r_p10"] for r in r10) / len(r10) if r10 else None

        # Success: max forward return within 5d >= 5%
        success = 0
        for r in labeled:
            max_5d = max(
                r["r_p1"] if r["r_p1"] is not None else -999,
                r["r_p3"] if r["r_p3"] is not None else -999,
                r["r_p5"] if r["r_p5"] is not None else -999,
            )
            if max_5d >= 0.05:
                success += 1
        success_rate = success / n_labeled * 100

        # Median max return (of 1d/3d/5d/10d)
        max_returns = []
        for r in labeled:
            vals = [v for v in [r["r_p1"], r["r_p3"], r["r_p5"], r["r_p10"]] if v is not None]
            if vals:
                max_returns.append(max(vals))
        max_returns.sort()
        median_max = max_returns[len(max_returns) // 2] if max_returns else None

        return {
            "label": label, "n_total": n_total, "n_labeled": n_labeled,
            "avg_1d": avg_1d, "avg_5d": avg_5d, "avg_10d": avg_10d,
            "success_5d": success_rate, "median_max": median_max,
        }

    s_a = stats(group_a, "Account B (engulfing)")
    s_no = stats(no_engulfing, "No engulfing")
    s_c = stats(group_c, "All signals")

    # Account A stats
    acct_a_n = len(acct_a_trades)
    acct_a_pnl_vals = [float(t["pnl_pct"]) for t in acct_a_trades if t["pnl_pct"] is not None]
    acct_a_wins = sum(1 for v in acct_a_pnl_vals if v > 0)
    acct_a_avg_pnl = sum(acct_a_pnl_vals) / len(acct_a_pnl_vals) if acct_a_pnl_vals else 0

    # ── Engulfing type breakdown ──────────────────────────────────
    n_daily = sum(1 for r in group_a if r["engulfing_type"] == "daily")
    n_5min = sum(1 for r in group_a if r["engulfing_type"] == "5min")

    # ── Print report ──────────────────────────────────────────────
    def fmt_pct(v):
        return f"{v*100:+.2f}%" if v is not None else "N/A"

    def fmt_rate(v):
        return f"{v:.1f}%" if v is not None else "N/A"

    print()
    print("=" * 60)
    print("  Account B Backtest: Signal Overlap Analysis")
    print("=" * 60)
    print(f"Date range: {date_min} to {date_max}")
    print()

    if not group_c:
        print("No signals found!")
        return

    print(f"Total signals (score >= 10):             {len(group_c):>5}")
    print(f"  With engulfing (Account B eligible):   {len(group_a):>5} ({len(group_a)/len(group_c)*100:.1f}%)")
    print(f"    - Daily pattern:                     {n_daily:>5}")
    print(f"    - 5min pattern only:                 {n_5min:>5}")
    print(f"  Without engulfing:                     {len(no_engulfing):>5} ({len(no_engulfing)/len(group_c)*100:.1f}%)")
    print()

    for s in [s_a, s_no, s_c]:
        print(f"--- {s['label']} (n={s['n_total']}, labeled={s['n_labeled']}) ---")
        print(f"  Avg 1-day return:           {fmt_pct(s['avg_1d'])}")
        print(f"  Avg 5-day return:           {fmt_pct(s['avg_5d'])}")
        print(f"  Avg 10-day return:          {fmt_pct(s['avg_10d'])}")
        print(f"  Median max return:          {fmt_pct(s['median_max'])}")
        print(f"  Success rate (>5% in 5d):   {fmt_rate(s['success_5d'])}")
        print()

    # Edge
    if s_a["success_5d"] is not None and s_c["success_5d"] is not None:
        edge = s_a["success_5d"] - s_c["success_5d"]
        print(f"--- Edge ---")
        print(f"  Engulfing filter edge (5d success): {edge:+.1f} pp")
        if s_a["avg_5d"] is not None and s_c["avg_5d"] is not None:
            ret_edge = (s_a["avg_5d"] - s_c["avg_5d"]) * 100
            print(f"  Engulfing filter edge (5d return):  {ret_edge:+.2f} pp")
        if s_a["avg_1d"] is not None and s_c["avg_1d"] is not None:
            ret_edge_1d = (s_a["avg_1d"] - s_c["avg_1d"]) * 100
            print(f"  Engulfing filter edge (1d return):  {ret_edge_1d:+.2f} pp")
    print()

    # Account A comparison
    print(f"--- Account A (live paper trades, n={acct_a_n}) ---")
    if acct_a_pnl_vals:
        print(f"  Win rate:                   {acct_a_wins/len(acct_a_pnl_vals)*100:.1f}%")
        print(f"  Avg trade P&L:              {acct_a_avg_pnl*100:+.2f}%")
    else:
        print("  No trades")
    print()

    # Score distribution within engulfing-confirmed
    if group_a:
        score_buckets = defaultdict(list)
        for r in group_a:
            score_buckets[r["score"]].append(r)
        print("--- Score Distribution (Account B signals) ---")
        for sc in sorted(score_buckets.keys()):
            items = score_buckets[sc]
            labeled = [r for r in items if r["r_p1"] is not None]
            avg_1d = sum(r["r_p1"] for r in labeled) / len(labeled) if labeled else None
            print(f"  Score {sc:>2}: {len(items):>4} signals  (labeled={len(labeled)})  avg 1d: {fmt_pct(avg_1d)}")
        print()

    # Monthly distribution
    if group_a:
        monthly = defaultdict(int)
        for r in group_a:
            monthly[r["date"].strftime("%Y-%m")] += 1
        print("--- Monthly Signal Frequency (Account B) ---")
        for month in sorted(monthly.keys()):
            bar = "#" * min(monthly[month], 50)
            print(f"  {month}: {monthly[month]:>4}  {bar}")
        print()

    # Top symbols
    if group_a:
        sym_counts = defaultdict(int)
        for r in group_a:
            sym_counts[r["symbol"]] += 1
        top_syms = sorted(sym_counts.items(), key=lambda x: -x[1])[:15]
        print("--- Top Symbols (Account B signals) ---")
        for sym, cnt in top_syms:
            print(f"  {sym:<8} {cnt:>4} signals")
        print()


if __name__ == "__main__":
    asyncio.run(main())
