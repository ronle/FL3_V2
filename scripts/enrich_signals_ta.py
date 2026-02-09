#!/usr/bin/env python3
"""
TA Enrichment of Historical Signals

Joins raw scored signals from e2e_backtest with ta_daily_close, master_tickers,
earnings_calendar, and sentiment data to reconstruct V28 filter verdicts.

Usage:
    python -m scripts.enrich_signals_ta
    python -m scripts.enrich_signals_ta --stats-only
    python -m scripts.enrich_signals_ta --input path/to/signals.json --output path/to/enriched.json
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# ETF exclusion set (copied from paper_trading/signal_filter.py)
# ---------------------------------------------------------------------------

ETF_EXCLUSIONS = {
    'SPY', 'QQQ', 'IWM', 'DIA', 'RSP', 'MDY', 'IJR', 'IJH',
    'XLE', 'XLF', 'XLK', 'XLV', 'XLI', 'XLU', 'XLP', 'XLY', 'XLB', 'XLRE', 'XLC',
    'ITB', 'XHB', 'XOP', 'XBI', 'XRT', 'XME', 'KWEB', 'MCHI', 'FXI',
    'SOXX', 'SMH', 'HACK', 'BOTZ', 'ROBO', 'IBB',
    'IYR', 'VNQ', 'GDXJ', 'GDX', 'JETS', 'KRE', 'KBE',
    'VTI', 'VOO', 'VXX', 'UVXY', 'SQQQ', 'TQQQ', 'SPXU', 'SPXS',
    'UPRO', 'LABU', 'LABD', 'SOXL', 'SOXS', 'TNA', 'TZA',
    'GLD', 'SLV', 'USO', 'UNG', 'WEAT', 'DBA', 'DBC',
    'TLT', 'HYG', 'LQD', 'JNK', 'AGG', 'BND', 'SHY', 'IEF',
    'EEM', 'EFA', 'VWO', 'IEMG',
    'ARKK', 'ARKG', 'ARKW', 'ARKF', 'ARKQ', 'ARKX',
    'IBIT', 'BITO', 'GBTC', 'ETHE', 'FBTC', 'BITB',
}

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def get_db_connection():
    import psycopg2
    db_url = os.environ.get("DATABASE_URL_LOCAL") or os.environ.get("DATABASE_URL", "")
    if "/cloudsql/" in db_url or not db_url:
        db_url = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Bulk data loaders
# ---------------------------------------------------------------------------

def load_ta_daily(conn, from_date: str, to_date: str) -> Dict[Tuple[str, date], dict]:
    """Load ta_daily_close into {(symbol, trade_date): {rsi_14, sma_20, sma_50, close_price}}."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, trade_date, rsi_14, sma_20, sma_50, close_price
        FROM ta_daily_close
        WHERE trade_date >= %s AND trade_date <= %s
    """, (from_date, to_date))

    ta_data = {}
    for row in cur.fetchall():
        ta_data[(row[0], row[1])] = {
            "rsi_14": float(row[2]) if row[2] is not None else None,
            "sma_20": float(row[3]) if row[3] is not None else None,
            "sma_50": float(row[4]) if row[4] is not None else None,
            "close_price": float(row[5]) if row[5] is not None else None,
        }
    cur.close()
    print(f"  Loaded {len(ta_data)} ta_daily_close rows")
    return ta_data


def load_sector_map(conn) -> Dict[str, str]:
    """Load symbol -> sector mapping."""
    cur = conn.cursor()
    cur.execute("SELECT symbol, sector FROM master_tickers WHERE sector IS NOT NULL")
    sectors = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    print(f"  Loaded {len(sectors)} sector mappings")
    return sectors


def load_earnings(conn, from_date: str, to_date: str) -> Dict[Tuple[str, date], date]:
    """Load earnings calendar into {(symbol, event_date): event_date} for proximity checks."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, event_date
        FROM earnings_calendar
        WHERE event_date >= %s AND event_date <= %s
    """, (from_date, to_date))

    earnings = {}
    for row in cur.fetchall():
        earnings[(row[0], row[1])] = row[1]
    cur.close()
    print(f"  Loaded {len(earnings)} earnings events")
    return earnings


def load_sentiment(conn, from_date: str, to_date: str) -> Dict[Tuple[str, date], Tuple]:
    """
    Load sentiment data from underlying tables (bypasses vw_media_daily_features 90-day window).
    Returns {(ticker, asof_date): (media_count, avg_stance_weighted)}.
    """
    cur = conn.cursor()
    # Query the underlying tables directly to bypass the view's 90-day filter
    cur.execute("""
        SELECT e.entity_value AS ticker,
               (a.publish_time AT TIME ZONE 'America/New_York')::date AS asof_date,
               COUNT(*) AS media_count,
               AVG(
                   CASE
                       WHEN (i.stance_by_ticker -> e.entity_value ->> 'stance') = 'pos' THEN 1.0
                       WHEN (i.stance_by_ticker -> e.entity_value ->> 'stance') = 'neg' THEN -1.0
                       ELSE 0.0
                   END
                   * COALESCE((i.stance_by_ticker -> e.entity_value ->> 'score')::numeric, 0.0)
               ) AS avg_stance_weighted
        FROM articles a
        JOIN article_entities e ON e.article_id = a.id AND e.entity_type = 'ticker'
        LEFT JOIN article_insights i ON i.article_id = a.id
        WHERE a.publish_time >= %s
          AND a.publish_time < (%s::date + 1)::timestamp
          AND e.entity_value IS NOT NULL
        GROUP BY e.entity_value, (a.publish_time AT TIME ZONE 'America/New_York')::date
    """, (from_date, to_date))

    sentiment = {}
    for row in cur.fetchall():
        ticker = row[0]
        asof_date = row[1]
        media_count = int(row[2]) if row[2] else 0
        avg_stance = float(row[3]) if row[3] is not None else 0.0
        sentiment[(ticker, asof_date)] = (media_count, avg_stance)
    cur.close()
    print(f"  Loaded {len(sentiment)} sentiment rows")
    return sentiment


# ---------------------------------------------------------------------------
# TA lookup helpers
# ---------------------------------------------------------------------------

def build_trading_days_index(ta_data: Dict[Tuple[str, date], dict]) -> Dict[str, List[date]]:
    """Build sorted list of available trading days per symbol for prior-day lookup."""
    by_symbol = defaultdict(set)
    for (symbol, d) in ta_data.keys():
        by_symbol[symbol].add(d)
    return {sym: sorted(dates) for sym, dates in by_symbol.items()}


def find_prior_trading_day(signal_date: date, symbol: str,
                           trading_days: Dict[str, List[date]]) -> Optional[date]:
    """Find the most recent trading day strictly before signal_date for this symbol."""
    days = trading_days.get(symbol)
    if not days:
        return None
    # Binary search for the last day < signal_date
    lo, hi = 0, len(days) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if days[mid] < signal_date:
            result = days[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def check_earnings_proximity(symbol: str, signal_date: date,
                             earnings: Dict[Tuple[str, date], date],
                             days_window: int = 2) -> Tuple[bool, Optional[str]]:
    """Check if earnings are within ±days_window of signal_date."""
    for delta in range(-days_window, days_window + 1):
        check_date = signal_date + timedelta(days=delta)
        if (symbol, check_date) in earnings:
            if delta == 0:
                return True, "TODAY"
            elif delta > 0:
                return True, f"+{delta}d"
            else:
                return True, f"{delta}d"
    return False, None


# ---------------------------------------------------------------------------
# Filter reconstruction
# ---------------------------------------------------------------------------

def apply_filters(signal: dict) -> Tuple[bool, Optional[str]]:
    """Reconstruct V28 filter chain. Returns (passed, rejection_reason)."""
    reasons = []

    # 1. ETF exclusion
    if signal["symbol"] in ETF_EXCLUSIONS:
        return False, f"ETF excluded ({signal['symbol']})"

    # 2. Score >= 10
    if signal.get("score", 0) < 10:
        reasons.append(f"score {signal['score']} < 10")

    # 3. Uptrend (price > SMA20) — use the original e2e trend
    if signal.get("trend") != 1:
        reasons.append("not uptrend")

    # 4. RSI < 50
    rsi = signal.get("rsi_14")
    if rsi is not None and rsi >= 50.0:
        reasons.append(f"RSI {rsi:.1f} >= 50.0")
    elif rsi is None:
        reasons.append("no RSI data")

    # 5. SMA50 momentum guard (price > SMA50)
    sma_50 = signal.get("sma_50")
    price = signal.get("stock_price", 0)
    if sma_50 is not None and price and price < sma_50:
        reasons.append(f"below 50d SMA ({price:.2f} < {sma_50:.2f})")

    # 6. Notional >= $50K
    notional = signal.get("notional", 0)
    if notional < 50000:
        reasons.append(f"notional ${notional:,.0f} < $50,000")

    # 7. Sentiment filter (crowded trade + negative sentiment)
    mentions = signal.get("media_mentions")
    sentiment = signal.get("sentiment_score")
    if mentions is not None and mentions >= 5:
        reasons.append(f"high mentions ({mentions})")
    if sentiment is not None and sentiment < 0:
        reasons.append(f"negative sentiment ({sentiment:.2f})")

    # 8. Earnings proximity
    if signal.get("near_earnings"):
        reasons.append(f"earnings {signal.get('earnings_timing', 'nearby')}")

    passed = len(reasons) == 0
    return passed, "; ".join(reasons) if reasons else None


# ---------------------------------------------------------------------------
# Main enrichment
# ---------------------------------------------------------------------------

def enrich_signals(signals: List[dict], conn, stats_only: bool = False) -> Tuple[List[dict], dict]:
    """Enrich signals with TA, sector, earnings, sentiment data."""

    # Determine date range from signals
    dates = [s["detection_time"][:10] for s in signals]
    min_date = min(dates)
    max_date = max(dates)
    # Buffer for prior-day TA lookups
    ta_from = (date.fromisoformat(min_date) - timedelta(days=10)).isoformat()
    ta_to = max_date

    print(f"\n[1/5] Loading ta_daily_close ({ta_from} to {ta_to})...")
    ta_data = load_ta_daily(conn, ta_from, ta_to)
    trading_days = build_trading_days_index(ta_data)

    print(f"\n[2/5] Loading sector mappings...")
    sectors = load_sector_map(conn)

    print(f"\n[3/5] Loading earnings calendar ({min_date} to {max_date})...")
    # Buffer earnings by ±7 days
    earn_from = (date.fromisoformat(min_date) - timedelta(days=7)).isoformat()
    earn_to = (date.fromisoformat(max_date) + timedelta(days=7)).isoformat()
    earnings = load_earnings(conn, earn_from, earn_to)

    print(f"\n[4/5] Loading sentiment data ({min_date} to {max_date})...")
    # Buffer sentiment for prior-day lookups
    sent_from = (date.fromisoformat(min_date) - timedelta(days=5)).isoformat()
    sentiment = load_sentiment(conn, sent_from, max_date)

    print(f"\n[5/5] Enriching {len(signals)} signals...")

    # Stats tracking
    stats = {
        "total": len(signals),
        "with_ta": 0,
        "missing_ta": 0,
        "with_sector": 0,
        "with_sentiment": 0,
        "with_earnings": 0,
        "passing_v28": 0,
        "rsi_only_rejections": 0,
        "rsi_50_60_only": 0,
        "rejection_counts": defaultdict(int),
    }

    enriched = []
    for i, sig in enumerate(signals):
        signal_date = date.fromisoformat(sig["detection_time"][:10])
        symbol = sig["symbol"]

        # Prior trading day TA
        prior_date = find_prior_trading_day(signal_date, symbol, trading_days)
        ta = ta_data.get((symbol, prior_date), {}) if prior_date else {}

        sig["rsi_14"] = ta.get("rsi_14")
        sig["sma_20"] = ta.get("sma_20")
        sig["sma_50"] = ta.get("sma_50")
        sig["prior_close"] = ta.get("close_price")
        sig["ta_date"] = prior_date.isoformat() if prior_date else None

        if ta:
            stats["with_ta"] += 1
        else:
            stats["missing_ta"] += 1

        # Sector
        sig["sector"] = sectors.get(symbol, "Unknown")
        if sig["sector"] != "Unknown":
            stats["with_sector"] += 1

        # Earnings proximity
        near_earn, earn_timing = check_earnings_proximity(symbol, signal_date, earnings)
        sig["near_earnings"] = near_earn
        sig["earnings_timing"] = earn_timing
        if near_earn:
            stats["with_earnings"] += 1

        # Sentiment (prior day)
        sent_date = prior_date if prior_date else signal_date - timedelta(days=1)
        sent = sentiment.get((symbol, sent_date))
        sig["media_mentions"] = sent[0] if sent else None
        sig["sentiment_score"] = sent[1] if sent else None
        if sent:
            stats["with_sentiment"] += 1

        # ETF flag
        sig["is_etf"] = symbol in ETF_EXCLUSIONS

        # Filter verdict
        passed, reason = apply_filters(sig)
        sig["filter_verdict"] = passed
        sig["rejection_reason"] = reason

        if passed:
            stats["passing_v28"] += 1
        elif reason:
            # Track rejection reasons
            for r in reason.split("; "):
                # Normalize RSI reasons
                if r.startswith("RSI"):
                    stats["rejection_counts"]["RSI"] += 1
                else:
                    stats["rejection_counts"][r.split(" (")[0].split(" $")[0].split(" ")[0]] += 1

            # Check RSI-only rejections
            parts = [r.strip() for r in reason.split(";")]
            non_rsi = [r for r in parts if "RSI" not in r.upper()]
            if len(non_rsi) == 0 and sig["rsi_14"] is not None:
                stats["rsi_only_rejections"] += 1
                if 50.0 <= sig["rsi_14"] < 60.0:
                    stats["rsi_50_60_only"] += 1

        enriched.append(sig)

        if (i + 1) % 500 == 0:
            print(f"    ...enriched {i+1}/{len(signals)} signals")

    return enriched, stats


def print_stats(stats: dict):
    """Print enrichment statistics."""
    print()
    print("=" * 60)
    print("TA ENRICHMENT REPORT")
    print("=" * 60)
    print(f"Total signals:           {stats['total']:,}")
    print(f"Signals with TA match:   {stats['with_ta']:,} ({stats['with_ta']/stats['total']*100:.1f}%)")
    print(f"Signals missing TA:      {stats['missing_ta']:,}")
    print(f"Signals with sector:     {stats['with_sector']:,}")
    print(f"Signals with sentiment:  {stats['with_sentiment']:,}")
    print(f"Near earnings:           {stats['with_earnings']:,}")
    print()
    print("FILTER RECONSTRUCTION")
    print("=" * 60)
    print(f"Passing V28 filters:     {stats['passing_v28']:,}")
    print(f"RSI-only rejections:     {stats['rsi_only_rejections']:,}")
    print(f"RSI 50-60 only:          {stats['rsi_50_60_only']:,} (the population we're testing)")
    print()
    print("Top rejection reasons:")
    sorted_reasons = sorted(stats["rejection_counts"].items(), key=lambda x: -x[1])
    for reason, count in sorted_reasons[:10]:
        print(f"  {reason:30s} {count:>6,}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich historical signals with TA data")
    parser.add_argument("--input", default=os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "polygon_data", "backtest_results",
        "e2e_backtest_v2_strikes_sweeps_price_scored.json"))
    parser.add_argument("--output", default=os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "polygon_data", "backtest_results",
        "enriched_signals_with_ta.json"))
    parser.add_argument("--min-score", type=int, default=10,
                        help="Pre-filter: minimum score to include (default: 10, use 0 for all)")
    parser.add_argument("--stats-only", action="store_true",
                        help="Only print stats, don't write output")
    args = parser.parse_args()

    print("=" * 60)
    print("TA ENRICHMENT — Historical Signal Enrichment")
    print("=" * 60)

    # Load raw signals
    print(f"\nLoading signals from {args.input}...")
    with open(args.input) as f:
        raw_signals = json.load(f)

    # Handle both list and dict-with-signals formats
    if isinstance(raw_signals, list):
        signals = raw_signals
    elif isinstance(raw_signals, dict) and "signals" in raw_signals:
        signals = raw_signals["signals"]
    else:
        print("ERROR: Unrecognized JSON format")
        sys.exit(1)

    print(f"  Total raw signals: {len(signals):,}")

    # Pre-filter by score
    if args.min_score > 0:
        signals = [s for s in signals if s.get("score", 0) >= args.min_score]
        print(f"  After score >= {args.min_score} filter: {len(signals):,}")

    if not signals:
        print("ERROR: No signals to enrich after filtering")
        sys.exit(1)

    # Connect to DB and enrich
    conn = get_db_connection()
    enriched, stats = enrich_signals(signals, conn)
    conn.close()

    # Print stats
    print_stats(stats)

    # Write output
    if not args.stats_only:
        output_data = {
            "metadata": {
                "source": os.path.basename(args.input),
                "enriched_at": datetime.utcnow().isoformat() + "Z",
                "total_signals": stats["total"],
                "signals_with_ta": stats["with_ta"],
                "signals_passing_v28": stats["passing_v28"],
                "rsi_50_60_only": stats["rsi_50_60_only"],
                "min_score_filter": args.min_score,
                "date_range": f"{min(s['detection_time'][:10] for s in enriched)} to {max(s['detection_time'][:10] for s in enriched)}",
                "filters_applied": ["etf", "score>=10", "uptrend", "rsi<50", "sma50",
                                    "notional>=50k", "sentiment", "earnings"],
            },
            "signals": enriched,
        }

        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

        # Serialize dates
        def serialize(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return obj

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2, default=serialize)
        print(f"\nEnriched signals written to {args.output}")
        file_mb = os.path.getsize(args.output) / (1024 * 1024)
        print(f"  File size: {file_mb:.1f} MB")


if __name__ == "__main__":
    main()
