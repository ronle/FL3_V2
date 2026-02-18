#!/usr/bin/env python3
"""
Volume Ratio Filter Discovery

Tests whether options volume relative to its own moving average adds signal
on top of scored UOA signals (score >= 10). Cross-tabs volume buckets with
engulfing confirmation to find the best combined filters.

Data sources:
  - e2e_backtest_v2_strikes_sweeps_price_scored.json: scored signals
  - orats_daily (DB): total_volume, volume_ema_7d, volume_ema_30d
  - orats_daily_returns (DB): forward returns (r_p1, r_p3, r_p5, r_p10)
  - engulfing_scores (DB): bullish engulfing patterns

Usage:
    python scripts/discover_volume_filters.py
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

import psycopg2


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_LOCAL_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"

SIGNAL_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "polygon_data", "backtest_results",
    "e2e_backtest_v2_strikes_sweeps_price_scored.json",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db_connection():
    db_url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "")
    if "/cloudsql/" in db_url or not db_url:
        db_url = _LOCAL_DB
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    return conn


def to_float(v):
    if v is None:
        return None
    return float(v)


def fmt_pct(v):
    return f"{v * 100:+.2f}%" if v is not None else "  N/A "


def fmt_rate(v):
    return f"{v * 100:.1f}%" if v is not None else " N/A "


def percentile(values, p):
    """Compute p-th percentile (0-100) of a sorted list."""
    if not values:
        return None
    k = (len(values) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(values):
        return values[f]
    return values[f] + (k - f) * (values[c] - values[f])


# ---------------------------------------------------------------------------
# Volume filter buckets
# ---------------------------------------------------------------------------

VOLUME_FILTERS = {
    "vol_below_normal":        lambda v: v < 0.8,
    "vol_normal_range":        lambda v: 0.8 <= v <= 1.1,
    "vol_slightly_elevated":   lambda v: 1.1 < v <= 1.5,
    "vol_moderately_elevated": lambda v: 1.5 < v <= 2.0,
    "vol_very_elevated":       lambda v: v > 2.0,
}

BUCKET_ORDER = [
    "vol_below_normal",
    "vol_normal_range",
    "vol_slightly_elevated",
    "vol_moderately_elevated",
    "vol_very_elevated",
]


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------

def compute_stats(records):
    """Compute summary stats for a group of signal records."""
    labeled = [r for r in records if r["r_p1"] is not None]
    n = len(records)
    n_lab = len(labeled)

    if not labeled:
        return {
            "n": n, "n_labeled": n_lab,
            "avg_1d": None, "avg_3d": None, "avg_5d": None, "avg_10d": None,
            "win_1d": None, "win_5d": None,
        }

    avg_1d = sum(r["r_p1"] for r in labeled) / n_lab

    r3 = [r for r in labeled if r["r_p3"] is not None]
    avg_3d = sum(r["r_p3"] for r in r3) / len(r3) if r3 else None

    r5 = [r for r in labeled if r["r_p5"] is not None]
    avg_5d = sum(r["r_p5"] for r in r5) / len(r5) if r5 else None

    r10 = [r for r in labeled if r["r_p10"] is not None]
    avg_10d = sum(r["r_p10"] for r in r10) / len(r10) if r10 else None

    win_1d = sum(1 for r in labeled if r["r_p1"] > 0) / n_lab
    win_5d = sum(1 for r in r5 if r["r_p5"] > 0) / len(r5) if r5 else None

    return {
        "n": n, "n_labeled": n_lab,
        "avg_1d": avg_1d, "avg_3d": avg_3d, "avg_5d": avg_5d, "avg_10d": avg_10d,
        "win_1d": win_1d, "win_5d": win_5d,
    }


def classify_bucket(ratio):
    """Return the bucket name for a given volume ratio."""
    for name in BUCKET_ORDER:
        if VOLUME_FILTERS[name](ratio):
            return name
    return None  # should not happen


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # ── Load scored signals ───────────────────────────────────────
    print(f"Loading signals from {os.path.basename(SIGNAL_FILE)}...")
    with open(SIGNAL_FILE) as f:
        data = json.load(f)

    all_raw = data["signals"] if isinstance(data, dict) else data
    high_score = [s for s in all_raw if s.get("score", 0) >= 10]
    print(f"Total signals: {len(all_raw):,}, score >= 10: {len(high_score):,}")

    # Get date range
    signal_dates = sorted(set(s["detection_time"][:10] for s in high_score))
    min_date = date.fromisoformat(signal_dates[0])
    max_date = date.fromisoformat(signal_dates[-1])
    print(f"Date range: {min_date} to {max_date}")

    # ── Bulk load from DB ─────────────────────────────────────────
    conn = get_db_connection()
    cur = conn.cursor()

    print("\nLoading orats_daily volume data...")
    cur.execute("""
        SELECT symbol, asof_date, total_volume, volume_ema_7d, volume_ema_30d
        FROM orats_daily
        WHERE asof_date >= %s AND asof_date <= %s
          AND total_volume IS NOT NULL
    """, (min_date, max_date))
    orats_lookup = {}
    for row in cur.fetchall():
        orats_lookup[(row[0], row[1])] = {
            "total_volume": int(row[2]) if row[2] is not None else None,
            "volume_ema_7d": int(row[3]) if row[3] is not None else None,
            "volume_ema_30d": int(row[4]) if row[4] is not None else None,
        }
    print(f"  Loaded {len(orats_lookup):,} orats volume rows")

    print("Loading orats_daily_returns...")
    cur.execute("""
        SELECT ticker, trade_date, r_p1, r_p3, r_p5, r_p10
        FROM orats_daily_returns
        WHERE trade_date >= %s AND trade_date <= %s
    """, (min_date, max_date))
    returns_lookup = {}
    for row in cur.fetchall():
        returns_lookup[(row[0], row[1])] = {
            "r_p1": to_float(row[2]),
            "r_p3": to_float(row[3]),
            "r_p5": to_float(row[4]),
            "r_p10": to_float(row[5]),
        }
    print(f"  Loaded {len(returns_lookup):,} forward return rows")

    print("Loading engulfing_scores (bullish daily + 5min)...")
    cur.execute("""
        SELECT symbol, pattern_date::date as pdate
        FROM engulfing_scores
        WHERE direction = 'bullish' AND timeframe = '1D'
    """)
    daily_eng_set = {(r[0], r[1]) for r in cur.fetchall()}
    cur.execute("""
        SELECT symbol, pattern_date::date as pdate
        FROM engulfing_scores
        WHERE direction = 'bullish' AND timeframe = '5min'
    """)
    fivemin_eng_set = {(r[0], r[1]) for r in cur.fetchall()}
    print(f"  Loaded {len(daily_eng_set):,} daily + {len(fivemin_eng_set):,} 5min bullish engulfing patterns")

    cur.close()
    conn.close()

    # ── Join signals with DB data ─────────────────────────────────
    records = []
    n_missing_vol = 0
    n_missing_ret = 0

    for s in high_score:
        signal_date = date.fromisoformat(s["detection_time"][:10])
        symbol = s["symbol"]

        # Volume ratios
        orats = orats_lookup.get((symbol, signal_date), {})
        vol = orats.get("total_volume")
        ema7 = orats.get("volume_ema_7d")
        ema30 = orats.get("volume_ema_30d")

        vol_vs_ema7 = (vol / ema7) if (vol is not None and ema7 and ema7 > 0) else None
        vol_vs_ema30 = (vol / ema30) if (vol is not None and ema30 and ema30 > 0) else None

        if vol_vs_ema7 is None:
            n_missing_vol += 1

        # Forward returns
        ret = returns_lookup.get((symbol, signal_date), {})
        if not ret:
            n_missing_ret += 1

        # Engulfing — match Account B logic exactly:
        #   daily: same date or up to 3 days prior (handles weekends)
        #   5min: same date only
        has_daily = False
        for offset in range(0, 4):
            if (symbol, signal_date - timedelta(days=offset)) in daily_eng_set:
                has_daily = True
                break
        has_5min = (symbol, signal_date) in fivemin_eng_set if not has_daily else False
        has_engulfing = has_daily or has_5min

        records.append({
            "symbol": symbol,
            "date": signal_date,
            "score": s.get("score", 0),
            "vol_vs_ema7": vol_vs_ema7,
            "vol_vs_ema30": vol_vs_ema30,
            "has_engulfing": has_engulfing,
            "r_p1": ret.get("r_p1"),
            "r_p3": ret.get("r_p3"),
            "r_p5": ret.get("r_p5"),
            "r_p10": ret.get("r_p10"),
        })

    print(f"\nJoined {len(records):,} signals")
    print(f"  Missing volume data: {n_missing_vol:,}")
    print(f"  Missing return data: {n_missing_ret:,}")

    # ══════════════════════════════════════════════════════════════
    # Section 1: Distribution & Data-Driven Breakpoints
    # ══════════════════════════════════════════════════════════════

    for ratio_key, label in [("vol_vs_ema7", "volume_vs_ema7"), ("vol_vs_ema30", "volume_vs_ema30")]:
        vals = sorted([r[ratio_key] for r in records if r[ratio_key] is not None])
        n_with = len(vals)

        print()
        print("=" * 70)
        print(f"  SECTION 1: Distribution — {label}")
        print("=" * 70)
        print(f"  Signals with data:  {n_with:,} / {len(records):,} ({n_with / len(records) * 100:.1f}%)")
        if not vals:
            print("  No data available.")
            continue

        print(f"  Min:    {vals[0]:.2f}    P10:  {percentile(vals, 10):.2f}    P25:  {percentile(vals, 25):.2f}")
        print(f"  Median: {percentile(vals, 50):.2f}    P75:  {percentile(vals, 75):.2f}    P90:  {percentile(vals, 90):.2f}    Max: {vals[-1]:.2f}")

        # Quartile analysis
        p25 = percentile(vals, 25)
        p50 = percentile(vals, 50)
        p75 = percentile(vals, 75)
        quartile_bounds = [
            (f"Q1 (< P25={p25:.2f})", lambda r, p25=p25: r[ratio_key] is not None and r[ratio_key] < p25),
            (f"Q2 (P25 - P50)", lambda r, p25=p25, p50=p50: r[ratio_key] is not None and p25 <= r[ratio_key] < p50),
            (f"Q3 (P50 - P75)", lambda r, p50=p50, p75=p75: r[ratio_key] is not None and p50 <= r[ratio_key] < p75),
            (f"Q4 (> P75={p75:.2f})", lambda r, p75=p75: r[ratio_key] is not None and r[ratio_key] >= p75),
        ]

        print(f"\n--- Quartile Analysis ({label}) ---")
        for q_label, q_filter in quartile_bounds:
            group = [r for r in records if q_filter(r)]
            st = compute_stats(group)
            print(f"  {q_label:25s}  {st['n']:>4} signals   avg 5d: {fmt_pct(st['avg_5d'])}   win rate: {fmt_rate(st['win_5d'])}")

    # ══════════════════════════════════════════════════════════════
    # Section 2: Fixed Bucket Analysis
    # ══════════════════════════════════════════════════════════════

    for ratio_key, label in [("vol_vs_ema7", "volume_vs_ema7"), ("vol_vs_ema30", "volume_vs_ema30")]:
        print()
        print("=" * 70)
        print(f"  SECTION 2: Fixed Bucket Analysis — {label}")
        print("=" * 70)
        print(f"  {'Bucket':<28s} {'N':>5}  {'avg 1d':>8}  {'avg 5d':>8}  {'avg 10d':>8}  {'win 5d':>7}")
        print("  " + "-" * 66)

        for bucket in BUCKET_ORDER:
            filt = VOLUME_FILTERS[bucket]
            group = [r for r in records if r[ratio_key] is not None and filt(r[ratio_key])]
            st = compute_stats(group)
            low_n = " [low-N]" if st["n"] < 20 else ""
            print(f"  {bucket:<28s} {st['n']:>5}  {fmt_pct(st['avg_1d'])}  {fmt_pct(st['avg_5d'])}  {fmt_pct(st['avg_10d'])}  {fmt_rate(st['win_5d'])}{low_n}")

    # ══════════════════════════════════════════════════════════════
    # Section 3: Cross-Tab (Volume × Engulfing)
    # ══════════════════════════════════════════════════════════════

    print()
    print("=" * 70)
    print("  SECTION 3: Cross-Tab — Volume (ema7) × Engulfing (5d return)")
    print("=" * 70)

    ratio_key = "vol_vs_ema7"
    header = f"  {'':28s} | {'No Engulfing':>18} | {'With Engulfing':>18} | {'Engulfing Lift':>14}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    # ALL signals row
    no_eng = [r for r in records if not r["has_engulfing"] and r[ratio_key] is not None]
    with_eng = [r for r in records if r["has_engulfing"] and r[ratio_key] is not None]
    st_no = compute_stats(no_eng)
    st_yes = compute_stats(with_eng)
    lift = None
    if st_no["avg_5d"] is not None and st_yes["avg_5d"] is not None:
        lift = st_yes["avg_5d"] - st_no["avg_5d"]

    def fmt_cell(st):
        if st["avg_5d"] is None:
            return f"{'N/A':>8} (n={st['n']:>3})"
        return f"{st['avg_5d'] * 100:+.2f}% (n={st['n']:>3})"

    def fmt_lift(v, n_no, n_yes):
        low = " [low-N]" if n_no < 20 or n_yes < 20 else ""
        if v is None:
            return f"{'N/A':>8}{low}"
        return f"{v * 100:+.2f} pp{low}"

    print(f"  {'ALL signals':<28s} | {fmt_cell(st_no):>18} | {fmt_cell(st_yes):>18} | {fmt_lift(lift, st_no['n'], st_yes['n']):>14}")

    for bucket in BUCKET_ORDER:
        filt = VOLUME_FILTERS[bucket]
        no_eng_b = [r for r in records if r[ratio_key] is not None and filt(r[ratio_key]) and not r["has_engulfing"]]
        with_eng_b = [r for r in records if r[ratio_key] is not None and filt(r[ratio_key]) and r["has_engulfing"]]
        st_no_b = compute_stats(no_eng_b)
        st_yes_b = compute_stats(with_eng_b)
        lift_b = None
        if st_no_b["avg_5d"] is not None and st_yes_b["avg_5d"] is not None:
            lift_b = st_yes_b["avg_5d"] - st_no_b["avg_5d"]
        print(f"  {bucket:<28s} | {fmt_cell(st_no_b):>18} | {fmt_cell(st_yes_b):>18} | {fmt_lift(lift_b, st_no_b['n'], st_yes_b['n']):>14}")

    # ══════════════════════════════════════════════════════════════
    # Section 4: Best Combined Filter Candidates
    # ══════════════════════════════════════════════════════════════

    print()
    print("=" * 70)
    print("  SECTION 4: Best Combined Filter Candidates (min N >= 20, by avg 5d)")
    print("=" * 70)

    candidates = []

    # Engulfing alone (baseline)
    eng_only = [r for r in records if r["has_engulfing"]]
    st_eng = compute_stats(eng_only)
    if st_eng["n"] >= 20 and st_eng["avg_5d"] is not None:
        candidates.append({
            "label": "engulfing only",
            "n": st_eng["n"],
            "avg_5d": st_eng["avg_5d"],
            "win_5d": st_eng["win_5d"],
            "avg_1d": st_eng["avg_1d"],
        })

    for ratio_key, ema_label in [("vol_vs_ema7", "ema7"), ("vol_vs_ema30", "ema30")]:
        for bucket in BUCKET_ORDER:
            filt = VOLUME_FILTERS[bucket]

            # Volume bucket alone
            vol_only = [r for r in records if r[ratio_key] is not None and filt(r[ratio_key])]
            st_v = compute_stats(vol_only)
            if st_v["n"] >= 20 and st_v["avg_5d"] is not None:
                candidates.append({
                    "label": f"{bucket} ({ema_label})",
                    "n": st_v["n"],
                    "avg_5d": st_v["avg_5d"],
                    "win_5d": st_v["win_5d"],
                    "avg_1d": st_v["avg_1d"],
                })

            # Engulfing + volume bucket
            eng_vol = [r for r in records if r[ratio_key] is not None and filt(r[ratio_key]) and r["has_engulfing"]]
            st_ev = compute_stats(eng_vol)
            if st_ev["n"] >= 20 and st_ev["avg_5d"] is not None:
                candidates.append({
                    "label": f"engulfing + {bucket} ({ema_label})",
                    "n": st_ev["n"],
                    "avg_5d": st_ev["avg_5d"],
                    "win_5d": st_ev["win_5d"],
                    "avg_1d": st_ev["avg_1d"],
                })

    # Sort by avg 5d descending
    candidates.sort(key=lambda x: x["avg_5d"], reverse=True)

    print(f"  {'Filter':<50s} {'N':>5}  {'avg 1d':>8}  {'avg 5d':>8}  {'win 5d':>7}")
    print("  " + "-" * 78)
    for c in candidates:
        print(f"  {c['label']:<50s} {c['n']:>5}  {fmt_pct(c['avg_1d'])}  {fmt_pct(c['avg_5d'])}  {fmt_rate(c['win_5d'])}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
