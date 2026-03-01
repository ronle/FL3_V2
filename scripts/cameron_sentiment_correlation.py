"""
Cameron Pattern + Sentiment Correlation Analysis

Reads:
  1. Phase 3 backtest output (cameron_intraday_trades.csv or expanded _full variant)
  2. fl3 database: sentiment_daily, articles, article_entities, article_sentiment

Produces:
  1. Correlation matrix: sentiment features vs trade PnL
  2. TEST-S1 through TEST-S6 results tables
  3. Recommended filters with adoption criteria
  4. Enriched dataset saved to CSV

Dependencies:
  - Cloud SQL Proxy running on port 5433
  - Phase 3 backtest output available
  - pandas, numpy, scipy.stats, psycopg2

Usage:
    python -m scripts.cameron_sentiment_correlation
    python -m scripts.cameron_sentiment_correlation --trades cameron_intraday_trades_target_1_full.csv
"""

import argparse
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg2
from scipy import stats

# --- CONFIG ---
FL3_DB = "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"
CAMERON_TRADES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backtest_results", "cameron_intraday_trades.csv"
)
RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backtest_results"
)


# --- DATA LOADING ---

def load_cameron_trades() -> pd.DataFrame:
    """Load Phase 3 backtest results."""
    df = pd.read_csv(CAMERON_TRADES_PATH, parse_dates=["trade_date"])
    print(f"Loaded {len(df)} Cameron trades")
    print(f"Date range: {df['trade_date'].min().date()} to {df['trade_date'].max().date()}")
    return df


def load_sentiment_daily(symbols: list, start_date: date, end_date: date) -> pd.DataFrame:
    """Load pre-aggregated sentiment data for trade symbols."""
    conn = psycopg2.connect(FL3_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT ticker, asof_date, sentiment_index, mentions_total,
               media_mentions, social_mentions, mentions_mom_1d, mentions_mom_3d
        FROM sentiment_daily
        WHERE ticker = ANY(%s::text[])
          AND asof_date BETWEEN %s AND %s
    """, (symbols, start_date, end_date))
    rows = cur.fetchall()
    cols = ["ticker", "asof_date", "sentiment_index", "mentions_total",
            "media_mentions", "social_mentions", "mentions_mom_1d", "mentions_mom_3d"]
    df = pd.DataFrame(rows, columns=cols)
    cur.close()
    conn.close()
    print(f"Loaded {len(df)} sentiment_daily rows for {df['ticker'].nunique() if len(df) else 0} symbols")
    return df


def load_article_detail(symbols: list, start_date: date, end_date: date) -> pd.DataFrame:
    """Load raw article-level data with timestamps for pre-market analysis.
    Does NOT require article_sentiment — just article presence + entity mapping.
    Sentiment columns are LEFT JOINed (nullable) for tests that need them.
    """
    conn = psycopg2.connect(FL3_DB)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            ae.entity_value AS ticker,
            a.publish_time,
            a.source,
            a.title,
            s.sentiment,
            s.confidence
        FROM articles a
        JOIN article_entities ae ON ae.article_id = a.id
        LEFT JOIN article_sentiment s ON s.article_id = a.id
        WHERE ae.entity_type = 'ticker'
          AND ae.entity_value = ANY(%s::text[])
          AND a.publish_time BETWEEN %s AND (%s::date + INTERVAL '1 day')
        ORDER BY a.publish_time
    """, (symbols, start_date, end_date))
    rows = cur.fetchall()
    cols = ["ticker", "publish_time", "source", "title", "sentiment", "confidence"]
    df = pd.DataFrame(rows, columns=cols)
    cur.close()
    conn.close()
    scored = df["sentiment"].notna().sum() if len(df) else 0
    print(f"Loaded {len(df)} articles for {df['ticker'].nunique() if len(df) else 0} symbols ({scored} with sentiment scores)")
    return df


# --- JOIN ---

def merge_trades_with_sentiment(
    trades: pd.DataFrame,
    sentiment: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join Cameron trades with D-1 sentiment data.
    D-1 because sentiment_daily aggregates after market close.
    """
    trades = trades.copy()
    trades["lookup_date"] = trades["trade_date"] - pd.Timedelta(days=1)

    sentiment = sentiment.copy()
    sentiment["asof_date"] = pd.to_datetime(sentiment["asof_date"])

    merged = trades.merge(
        sentiment,
        left_on=["symbol", "lookup_date"],
        right_on=["ticker", "asof_date"],
        how="left",
    )

    coverage = merged["sentiment_index"].notna().mean()
    print(f"Sentiment coverage: {coverage:.1%} of trades have D-1 sentiment data")

    return merged


def add_premarket_features(
    trades: pd.DataFrame,
    articles: pd.DataFrame,
) -> pd.DataFrame:
    """
    For each trade, count pre-market articles and compute pre-market sentiment.
    """
    trades = trades.copy()

    # Initialize columns
    for col in ["premarket_article_count", "premarket_avg_sentiment",
                "prev_day_article_count", "prev_day_avg_sentiment",
                "reddit_count", "news_count"]:
        trades[col] = np.nan

    if articles.empty:
        print("No articles found — skipping pre-market features")
        return trades

    articles = articles.copy()
    articles["publish_time"] = pd.to_datetime(articles["publish_time"], utc=True)
    articles["publish_date"] = articles["publish_time"].dt.date
    articles["publish_hour"] = articles["publish_time"].dt.hour + articles["publish_time"].dt.minute / 60

    # Pre-market: before 13:30 UTC = 9:30 AM ET (or approximate with hour < 14 UTC)
    premarket = articles[articles["publish_hour"] < 14].copy()

    print(f"Processing pre-market features for {len(trades)} trades...")

    for idx, trade in trades.iterrows():
        td = trade["trade_date"].date()
        sym = trade["symbol"]

        # Pre-market articles on trade day
        pm_arts = premarket[
            (premarket["ticker"] == sym) & (premarket["publish_date"] == td)
        ]
        trades.loc[idx, "premarket_article_count"] = len(pm_arts)
        if len(pm_arts) > 0:
            trades.loc[idx, "premarket_avg_sentiment"] = pm_arts["sentiment"].mean()

        # Previous day articles
        prev_date = td - timedelta(days=1)
        pd_arts = articles[
            (articles["ticker"] == sym) & (articles["publish_date"] == prev_date)
        ]
        trades.loc[idx, "prev_day_article_count"] = len(pd_arts)
        if len(pd_arts) > 0:
            trades.loc[idx, "prev_day_avg_sentiment"] = pd_arts["sentiment"].mean()

        # Source breakdown (trade day + prev day)
        recent = articles[
            (articles["ticker"] == sym)
            & (articles["publish_date"].isin([prev_date, td]))
        ]
        trades.loc[idx, "reddit_count"] = (recent["source"] == "reddit").sum()
        trades.loc[idx, "news_count"] = (recent["source"] != "reddit").sum()

    return trades


# --- TESTS ---

def compute_metrics(df: pd.DataFrame) -> dict:
    """Compute standard trading metrics for a group of trades."""
    if len(df) == 0:
        return {"n": 0, "wr": 0, "avg_pnl": 0, "median_pnl": 0, "sharpe": 0, "total_pnl": 0, "pf": 0}

    wins = (df["pnl_pct"] > 0).sum()
    losers = df[df["pnl_pct"] < 0]["pnl_pct"]
    gp = df[df["pnl_pct"] > 0]["pnl_pct"].sum()
    gl = abs(losers.sum()) if len(losers) else 0
    std = df["pnl_pct"].std()

    return {
        "n": len(df),
        "wr": wins / len(df),
        "avg_pnl": df["pnl_pct"].mean(),
        "median_pnl": df["pnl_pct"].median(),
        "sharpe": df["pnl_pct"].mean() / std * np.sqrt(252) if std > 0 else 0,
        "total_pnl": df["pnl_pct"].sum(),
        "pf": gp / gl if gl > 0 else (float("inf") if gp > 0 else 0),
    }


def _print_group(label: str, group: pd.DataFrame):
    m = compute_metrics(group)
    print(
        f"  {label:30s}: N={m['n']:4d}, WR={m['wr']:.1%}, "
        f"AvgPnL={m['avg_pnl']:+.3%}, Sharpe={m['sharpe']:.2f}, PF={m['pf']:.2f}"
    )


def _ttest(group_a: pd.DataFrame, group_b: pd.DataFrame, label_a: str, label_b: str):
    if len(group_a) > 10 and len(group_b) > 10:
        t_stat, p_val = stats.ttest_ind(
            group_a["pnl_pct"].dropna(), group_b["pnl_pct"].dropna()
        )
        sig = "*** SIGNIFICANT" if p_val < 0.05 else "(not significant)"
        print(f"\n  t-test ({label_a} vs {label_b}): t={t_stat:.3f}, p={p_val:.4f} {sig}")


def run_test_s1(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S1: Has Catalyst (mentions > 0) vs No Catalyst")
    print("=" * 70)
    has = df[df["mentions_total"] > 0]
    no = df[(df["mentions_total"] == 0) | df["mentions_total"].isna()]
    _print_group("HAS CATALYST", has)
    _print_group("NO CATALYST", no)
    _ttest(has, no, "has_catalyst", "no_catalyst")


def run_test_s2(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S2: Sentiment Polarity Buckets")
    print("=" * 70)
    buckets = {
        "NEGATIVE (< 0)": df[df["sentiment_index"] < 0],
        "NEUTRAL (0 to 0.3)": df[
            (df["sentiment_index"] >= 0) & (df["sentiment_index"] < 0.3)
        ],
        "POSITIVE (>= 0.3)": df[df["sentiment_index"] >= 0.3],
        "NO DATA": df[df["sentiment_index"].isna()],
    }
    for label, group in buckets.items():
        _print_group(label, group)


def run_test_s3(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S3: Mention Volume Buckets")
    print("=" * 70)
    buckets = {
        "0 mentions": df[(df["mentions_total"] == 0) | df["mentions_total"].isna()],
        "1-2 mentions": df[df["mentions_total"].between(1, 2)],
        "3-4 mentions": df[df["mentions_total"].between(3, 4)],
        "5-9 mentions": df[df["mentions_total"].between(5, 9)],
        "10+ mentions": df[df["mentions_total"] >= 10],
    }
    for label, group in buckets.items():
        _print_group(label, group)


def run_test_s4(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S4: News vs Social Source")
    print("=" * 70)
    buckets = {
        "NEWS ONLY": df[(df["news_count"] > 0) & (df["reddit_count"] == 0)],
        "SOCIAL ONLY": df[(df["reddit_count"] > 0) & (df["news_count"] == 0)],
        "BOTH": df[(df["news_count"] > 0) & (df["reddit_count"] > 0)],
        "NEITHER": df[(df["news_count"] == 0) & (df["reddit_count"] == 0)],
    }
    for label, group in buckets.items():
        _print_group(label, group)


def run_test_s5(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S5: Pre-Market News (before 9:30 AM ET)")
    print("=" * 70)
    has_pm = df[df["premarket_article_count"] > 0]
    no_pm = df[(df["premarket_article_count"] == 0) | df["premarket_article_count"].isna()]
    _print_group("HAS PRE-MARKET NEWS", has_pm)
    _print_group("NO PRE-MARKET NEWS", no_pm)
    _ttest(has_pm, no_pm, "has_premarket", "no_premarket")


def run_test_s6(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("TEST-S6: Mentions Momentum (Spike Detection)")
    print("=" * 70)
    buckets = {
        "SPIKING (mom > 1.0)": df[df["mentions_mom_1d"] > 1.0],
        "STEADY (-0.5 to 1.0)": df[df["mentions_mom_1d"].between(-0.5, 1.0)],
        "FADING (mom < -0.5)": df[df["mentions_mom_1d"] < -0.5],
        "NO DATA": df[df["mentions_mom_1d"].isna()],
    }
    for label, group in buckets.items():
        _print_group(label, group)


# --- CORRELATION MATRIX ---

def run_correlation_analysis(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("CORRELATION MATRIX: Sentiment Features vs Trade PnL")
    print("=" * 70)

    features = [
        "sentiment_index", "mentions_total", "media_mentions", "social_mentions",
        "mentions_mom_1d", "mentions_mom_3d",
        "premarket_article_count", "premarket_avg_sentiment",
        "prev_day_article_count", "prev_day_avg_sentiment",
        "reddit_count", "news_count",
    ]

    print(f"\n  {'Feature':35s} {'Corr':>8s} {'p-value':>10s} {'N':>6s}")
    print(f"  {'-' * 35} {'-' * 8} {'-' * 10} {'-' * 6}")

    for feat in features:
        if feat not in df.columns:
            continue
        valid = df[["pnl_pct", feat]].dropna()
        if len(valid) < 20:
            print(f"  {feat:35s} {'N/A':>8s} {'N/A':>10s} {len(valid):6d}")
            continue
        corr, p_val = stats.pearsonr(valid["pnl_pct"], valid[feat])
        sig = "***" if p_val < 0.01 else "**" if p_val < 0.05 else "*" if p_val < 0.10 else ""
        print(f"  {feat:35s} {corr:+8.4f} {p_val:10.4f} {len(valid):6d} {sig}")


# --- RECOMMENDED FILTERS ---

def generate_recommendations(df: pd.DataFrame):
    print("\n" + "=" * 70)
    print("RECOMMENDED FILTERS (adoption criteria: p < 0.05 or WR delta > 5pp)")
    print("=" * 70)

    recommendations = []

    # Filter 1: Require mentions > 0
    has = df[df["mentions_total"] > 0]
    no = df[(df["mentions_total"] == 0) | df["mentions_total"].isna()]
    if len(has) > 20 and len(no) > 20:
        delta_wr = (has["pnl_pct"] > 0).mean() - (no["pnl_pct"] > 0).mean()
        _, p = stats.ttest_ind(has["pnl_pct"].dropna(), no["pnl_pct"].dropna())
        if p < 0.05 or abs(delta_wr) > 0.05:
            direction = "REQUIRE" if delta_wr > 0 else "AVOID"
            recommendations.append(
                f"  {direction} catalyst: mentions > 0 (WR delta: {delta_wr:+.1%}, p={p:.3f})"
            )

    # Filter 2: Block negative sentiment
    neg = df[df["sentiment_index"] < 0]
    non_neg = df[df["sentiment_index"] >= 0]
    if len(neg) > 10 and len(non_neg) > 10:
        delta_wr = (non_neg["pnl_pct"] > 0).mean() - (neg["pnl_pct"] > 0).mean()
        _, p = stats.ttest_ind(non_neg["pnl_pct"].dropna(), neg["pnl_pct"].dropna())
        if p < 0.05 or abs(delta_wr) > 0.05:
            recommendations.append(
                f"  BLOCK negative sentiment < 0 (WR delta: {delta_wr:+.1%}, p={p:.3f})"
            )

    # Filter 3: Block high mentions (crowded)
    low = df[df["mentions_total"] < 5]
    high = df[df["mentions_total"] >= 5]
    if len(low) > 20 and len(high) > 10:
        delta_wr = (low["pnl_pct"] > 0).mean() - (high["pnl_pct"] > 0).mean()
        _, p = stats.ttest_ind(low["pnl_pct"].dropna(), high["pnl_pct"].dropna())
        if p < 0.05 or abs(delta_wr) > 0.05:
            recommendations.append(
                f"  BLOCK crowded trades: mentions >= 5 (WR delta: {delta_wr:+.1%}, p={p:.3f})"
            )

    # Filter 4: Prefer pre-market news
    has_pm = df[df["premarket_article_count"] > 0]
    no_pm = df[(df["premarket_article_count"] == 0) | df["premarket_article_count"].isna()]
    if len(has_pm) > 10 and len(no_pm) > 10:
        delta_wr = (has_pm["pnl_pct"] > 0).mean() - (no_pm["pnl_pct"] > 0).mean()
        _, p = stats.ttest_ind(has_pm["pnl_pct"].dropna(), no_pm["pnl_pct"].dropna())
        if p < 0.05 or abs(delta_wr) > 0.05:
            direction = "REQUIRE" if delta_wr > 0 else "INFO ONLY"
            recommendations.append(
                f"  {direction} pre-market news (WR delta: {delta_wr:+.1%}, p={p:.3f})"
            )

    if recommendations:
        for r in recommendations:
            print(r)
    else:
        print("  No filters met adoption criteria. Sentiment may not add edge to Cameron signals.")

    print("\n  NOTE: Filters only adopted if p < 0.05 AND consistent across sub-periods.")


# --- MAIN ---

def run_article_subset_analysis(merged: pd.DataFrame):
    """
    Analyze the subset of trades that have article matches.
    Tests whether article sentiment scores predict trade performance.
    """
    print("\n" + "=" * 70)
    print("ARTICLE-MATCHED SUBSET ANALYSIS")
    print("=" * 70)

    has_any = merged[
        (merged["news_count"] > 0) | (merged["reddit_count"] > 0)
    ]
    no_any = merged[
        (merged["news_count"] == 0) & (merged["reddit_count"] == 0)
    ]

    print(f"\n  Trades WITH article match: {len(has_any)}")
    print(f"  Trades WITHOUT article match: {len(no_any)}")

    if len(has_any) < 30:
        print("  INSUFFICIENT article-matched trades (N < 30). Skipping.")
        return

    _print_group("WITH ARTICLES", has_any)
    _print_group("WITHOUT ARTICLES", no_any)
    _ttest(has_any, no_any, "with_articles", "without_articles")

    # Sub-tests on the article-matched subset only
    print("\n  --- Within article-matched subset ---")

    # By pre-market sentiment score (from article_sentiment)
    has_premarket_sent = has_any[has_any["premarket_avg_sentiment"].notna()]
    if len(has_premarket_sent) >= 20:
        print(f"\n  Trades with pre-market sentiment scores: {len(has_premarket_sent)}")

        pos_sent = has_premarket_sent[has_premarket_sent["premarket_avg_sentiment"] >= 0]
        neg_sent = has_premarket_sent[has_premarket_sent["premarket_avg_sentiment"] < 0]
        _print_group("  POSITIVE pre-market sentiment", pos_sent)
        _print_group("  NEGATIVE pre-market sentiment", neg_sent)
        if len(pos_sent) > 10 and len(neg_sent) > 10:
            _ttest(pos_sent, neg_sent, "pos_premarket", "neg_premarket")

    # By prev-day sentiment score
    has_prevday_sent = has_any[has_any["prev_day_avg_sentiment"].notna()]
    if len(has_prevday_sent) >= 20:
        print(f"\n  Trades with prev-day sentiment scores: {len(has_prevday_sent)}")

        pos_pd = has_prevday_sent[has_prevday_sent["prev_day_avg_sentiment"] >= 0]
        neg_pd = has_prevday_sent[has_prevday_sent["prev_day_avg_sentiment"] < 0]
        _print_group("  POSITIVE prev-day sentiment", pos_pd)
        _print_group("  NEGATIVE prev-day sentiment", neg_pd)
        if len(pos_pd) > 10 and len(neg_pd) > 10:
            _ttest(pos_pd, neg_pd, "pos_prevday", "neg_prevday")

    # By article count buckets (within matched subset)
    total_articles = has_any["news_count"] + has_any["reddit_count"]
    buckets = {
        "1 article": has_any[total_articles == 1],
        "2-3 articles": has_any[total_articles.between(2, 3)],
        "4-10 articles": has_any[total_articles.between(4, 10)],
        "10+ articles": has_any[total_articles > 10],
    }
    print(f"\n  Article count distribution (within matched):")
    for label, group in buckets.items():
        if len(group) >= 5:
            _print_group(f"  {label}", group)

    # By pattern type within article-matched
    print(f"\n  By pattern type (article-matched only):")
    for ptype in ["bull_flag", "consolidation_breakout", "vwap_reclaim"]:
        pt_has = has_any[has_any["pattern_type"] == ptype]
        pt_no = no_any[no_any["pattern_type"] == ptype]
        if len(pt_has) >= 10:
            _print_group(f"  {ptype} WITH articles", pt_has)
            _print_group(f"  {ptype} NO articles", pt_no)

    # By year (article-matched only)
    print(f"\n  By year (article-matched only):")
    has_any_copy = has_any.copy()
    has_any_copy["year"] = has_any_copy["trade_date"].dt.year
    for yr, grp in has_any_copy.groupby("year"):
        if len(grp) >= 10:
            _print_group(f"  {yr}", grp)


def main():
    parser = argparse.ArgumentParser(description="Cameron sentiment correlation")
    parser.add_argument(
        "--trades", default=None,
        help="Trades CSV filename in backtest_results/ (default: cameron_intraday_trades.csv)"
    )
    args = parser.parse_args()

    trades_path = CAMERON_TRADES_PATH
    if args.trades:
        trades_path = os.path.join(RESULTS_DIR, args.trades)

    print("Cameron Pattern + Sentiment Correlation Analysis")
    print("=" * 70)

    # 1. Load trades
    trades = pd.read_csv(trades_path, parse_dates=["trade_date"])
    print(f"Loaded {len(trades)} Cameron trades from {os.path.basename(trades_path)}")
    print(f"Date range: {trades['trade_date'].min().date()} to {trades['trade_date'].max().date()}")

    # 2. Determine overlap period
    symbols = trades["symbol"].unique().tolist()
    start = trades["trade_date"].min() - pd.Timedelta(days=2)
    end = trades["trade_date"].max()
    print(f"\nAnalysis window: {start.date()} to {end.date()}")
    print(f"Unique symbols: {len(symbols)}")

    # 3. Load sentiment data
    sentiment = load_sentiment_daily(symbols, start.date(), end.date())
    articles = load_article_detail(symbols, start.date(), end.date())

    # 4. Join
    merged = merge_trades_with_sentiment(trades, sentiment)
    merged = add_premarket_features(merged, articles)

    # 5. Run all tests
    run_test_s1(merged)
    run_test_s2(merged)
    run_test_s3(merged)
    run_test_s4(merged)
    run_test_s5(merged)
    run_test_s6(merged)

    # 6. Correlation matrix
    run_correlation_analysis(merged)

    # 7. Article-matched subset deep dive
    run_article_subset_analysis(merged)

    # 8. Recommendations
    generate_recommendations(merged)

    # 9. Save enriched dataset
    suffix = ""
    if args.trades and "_full" in args.trades:
        suffix = "_full"
    output_path = os.path.join(RESULTS_DIR, f"cameron_trades_with_sentiment{suffix}.csv")
    merged.to_csv(output_path, index=False)
    print(f"\nSaved enriched dataset to {output_path}")
    print(f"Total trades: {len(merged)}, with sentiment: {merged['sentiment_index'].notna().sum()}")


if __name__ == "__main__":
    main()
