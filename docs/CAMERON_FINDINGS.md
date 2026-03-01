# Cameron Scanner — Research Findings

**Date**: 2026-02-27 (updated 2026-02-28)
**Status**: E2E analysis COMPLETE, **B2 filter stack DEPLOYED as Account C (v61)**
**Data**: 3 years of Polygon minute bars (2023-01 to 2026-02), 4,042 Cameron-eligible symbols

> **AUTHORITATIVE REPORT**: See [`Docs/CAMERON_E2E_REPORT.md`](CAMERON_E2E_REPORT.md) for the comprehensive, properly-labeled analysis covering all 3 pattern types, both exit strategies, article coverage, and sentiment correlation — with N-labels, cross-tab verification, and statistical tests. The sections below are retained for historical context but are **superseded** by the E2E report.

---

## Table of Contents

1. [Cameron Universe Definition](#1-cameron-universe-definition)
2. [Phase 1: Data Audit](#2-phase-1-data-audit)
3. [Phase 2: Multi-Day Backtest (30 configs)](#3-phase-2-multi-day-backtest)
4. [Phase 3: Intraday VWAP Reclaim](#4-phase-3-intraday-vwap-reclaim)
5. [News/Sentiment Backfill](#5-newsentiment-backfill)
6. [Sentiment Correlation Analysis (S1-S6)](#6-sentiment-correlation-analysis)
7. [Conclusions & Next Steps](#7-conclusions--next-steps)

---

## 1. Cameron Universe Definition

Derived from Ross Cameron's "5 Pillars" and cross-validated against 6 elite day traders.

| Filter | Value | Source |
|--------|-------|--------|
| Gap % | >= 4% | `(open - prev_close) / prev_close` |
| RVOL | >= 5x | `daily_volume / avg_30d_volume` |
| Price | $1 - $20 | `close_price` |
| RVOL cap | < 1,000,000 | Excludes illiquid outliers |

**Universe file**: `E:\cameron_daily_universe.parquet` (8.15M rows, 2020-2026)
**Eligible symbols**: 4,042 unique tickers across the dataset

---

## 2. Phase 1: Data Audit

**Doc**: `Docs/CAMERON_DATA_AUDIT.md`

| Metric | Data Source | Status |
|--------|------------|--------|
| Gap % | Polygon minute bars (pre-market included) | Available |
| RVOL | Polygon daily volume / orats_daily.avg_daily_volume | Available |
| Float | Polygon `weighted_shares_outstanding` or FMP `floatShares` | Partial (market_cap proxy used) |
| Price | Polygon minute bars close | Available |

No blockers for backtesting. Float approximated via market_cap < $100M as proxy.

---

## 3. Phase 2: Multi-Day Backtest

**Period**: 2023-01 to 2026-02 (3 years)
**Configs tested**: 30 (4 strategies x varying gap/rvol/price/stop)
**Raw data**: `backtest_results/cameron_backtest_results.json`

### Strategy Comparison (best config per strategy)

| Strategy | Config | Trades | WR | Avg PnL | Sharpe | PF | Total PnL |
|----------|--------|-------:|---:|--------:|-------:|---:|----------:|
| GAP_AND_GO | rvol>=10x, -3% stop | 6,083 | 20.2% | +2.89% | **6.17** | **2.24** | **+$1.76M** |
| GAP_AND_GO | baseline, -5% stop | 7,606 | 26.8% | +1.85% | 4.69 | 1.53 | +$1.40M |
| GAP_AND_GO | $1-$50, -3% stop | 7,692 | 19.2% | +1.52% | 4.37 | 1.65 | +$1.17M |
| GAP_AND_GO | baseline, -3% stop | 7,606 | 18.7% | +1.40% | 4.35 | 1.59 | +$1.06M |
| GAP_AND_GO | baseline, -2% stop | 7,606 | 14.3% | +1.22% | 4.30 | 1.72 | +$927K |
| EOD_OVERNIGHT | baseline, -2% stop | 7,593 | 20.1% | +2.03% | **5.13** | **2.31** | **+$1.54M** |
| EOD_OVERNIGHT | rvol>=3x, -3% stop | 7,764 | 23.9% | +1.68% | 4.23 | 1.77 | +$1.30M |
| EOD_OVERNIGHT | baseline, -3% stop | 7,593 | 22.7% | +1.52% | 3.80 | 1.69 | +$1.15M |
| CAMERON+MOM | rvol>=3x, -3% stop | 3,417 | 26.6% | +0.97% | 2.26 | 1.46 | +$331K |
| CAMERON+MOM | baseline, -2% stop | 1,900 | 19.5% | +1.50% | 2.51 | 1.95 | +$285K |
| NEXT_DAY_OPEN | **all configs** | — | — | **negative** | <0 | <1.0 | **losers** |

### Key Phase 2 Findings

1. **GAP_AND_GO with rvol>=10x** is the absolute winner: Sharpe 6.17, PF 2.24
2. **EOD_OVERNIGHT with -2% stop** has best risk-adjusted return: Sharpe 5.13, PF 2.31, lowest max drawdown (1.57%)
3. **NEXT_DAY_OPEN loses money across ALL configs** — confirms these are same-day plays only
4. **CAMERON+MOMENTUM** works but lower volume (~2-3 trades/day vs 8-10)
5. **77% stop-out rate** across most configs — backtest holds too long; Cameron holds 2-5 minutes with pattern-based exits
6. Higher gap thresholds (>=20%) reduce trade count without improving Sharpe
7. Higher RVOL threshold (>=10x) improves quality (Sharpe 6.17 vs 4.35 at >=5x) at cost of fewer signals

### Yearly Consistency (GAP_AND_GO baseline, -3% stop)

| Year | Trades | WR | Avg PnL | Total PnL |
|------|-------:|---:|--------:|----------:|
| 2023 | 2,399 | 20.8% | +1.42% | +$342K |
| 2024 | 2,460 | 18.6% | +1.66% | +$408K |
| 2025 | 2,467 | 17.5% | +1.32% | +$325K |
| 2026 | 280 | 12.9% | -0.46% | -$13K |

Consistent 2023-2025. 2026 only has 6 weeks of data — too early to judge.

---

## 4. Phase 3: Intraday VWAP Reclaim

**Strategy**: Cameron gap stocks that pull back below VWAP and then reclaim it intraday
**Entry**: VWAP reclaim (close above VWAP after being below)
**Exit**: Target 2 (2:1 R:R based on ATR) or stop-loss
**Raw data**: `backtest_results/cameron_intraday_summary.json`

### Combined Results

| Metric | Value |
|--------|-------|
| Total signals | 4,902 |
| Total patterns | 2,598 |
| **Total trades** | **1,303** |
| **Wins** | **620** |
| **Win Rate** | **47.6%** |
| **Avg PnL** | **+0.66%** |
| **Median PnL** | -0.40% |
| **Sharpe** | **3.87** |
| **Profit Factor** | **1.34** |
| Max Drawdown | 82.3% (cumulative PnL basis) |
| **Avg Hold** | **30.8 minutes** |
| Stop-out Rate | 33.8% |
| Trades/Day | 4.69 |

### Results by Pattern Type

| Pattern | Trades | WR | Avg PnL | Sharpe | PF | Stop Rate | Hold |
|---------|-------:|---:|--------:|-------:|---:|----------:|-----:|
| **Bull Flag** | 1,063 | 45.4% | +0.59% | 1.57 | 1.28 | 35.5% | 31 min |
| **Consolidation Breakout** | 193 | **57.5%** | **+0.92%** | **4.26** | **1.98** | 28.5% | 31 min |
| **VWAP Reclaim** | 47 | 55.3% | **+1.25%** | 1.87 | 1.50 | **17.0%** | 26 min |

- **Bull flag** is the workhorse (82% of all trades) — lower WR but high volume
- **Consolidation breakout** is the quality signal — 57.5% WR, Sharpe 4.26, PF 1.98
- **VWAP reclaim** has best risk profile (17% stop rate) but fires rarely (47 trades in 13 months)

### Yearly Breakdown

| Year | Trades | WR | Avg PnL | Total PnL $ |
|------|-------:|---:|--------:|-----------:|
| 2025 | 1,167 | 47.5% | +0.64% | +$74,351 |
| 2026 | 136 | 48.5% | +0.89% | +$12,036 |

### Phase 3 vs Phase 2 Comparison

| Metric | Phase 2 (GAP_AND_GO) | Phase 3 (VWAP Reclaim) |
|--------|---------------------|----------------------|
| Win Rate | 18.7% | **47.6%** |
| Stop-out Rate | 78.9% | **33.8%** |
| Avg Hold | Full day | **30.8 min** |
| Sharpe | 4.35 | 3.87 |
| Trades/Day | 9.78 | 4.69 |

Phase 3 trades less frequently but with dramatically better win rate (47.6% vs 18.7%) and lower stop-out rate (33.8% vs 78.9%). The VWAP reclaim pattern provides much better entry timing than buying at the open.

---

## 5. News/Sentiment Backfill

**Goal**: Improve article coverage for 4,042 Cameron symbols to enable sentiment correlation analysis.
**Script**: `scripts/cameron_news_backfill.py`

### Coverage Before vs After

| Stage | Articles | Symbols Covered | Coverage % |
|-------|--------:|--------------:|----------:|
| Before backfill | 54,260 | 1,540 / 4,042 | 38.1% |
| After FMP backfill | 62,531 | 2,614 / 4,042 | 64.6% |
| **After FMP + Reddit** | **131,281** | **3,111 / 4,042** | **77.0%** |

### FMP Backfill

- API: `financialmodelingprep.com/stable/news/stock`
- Tickers processed: 2,103
- Articles inserted: 8,271
- API calls: 2,128
- Duration: 20.6 minutes
- Cost: $0 (included in existing subscription)

### Reddit Arctic Shift Backfill

- API: `arctic-shift.photon-reddit.com` (Pushshift successor, no auth)
- Date range: 2025-01-01 to 2026-02-27
- Reference implementation: `FL3/social_scraper/reddit_fetch_full.py`

| Subreddit | Posts Scanned | Cameron Mentions |
|-----------|------------:|----------------:|
| r/wallstreetbets | 139,012 | 22,033 |
| r/pennystocks | 30,149 | 17,414 |
| r/Shortsqueeze | 14,380 | 9,355 |
| r/smallstreetbets | 29,979 | 7,062 |
| r/stocks | 53,624 | 6,958 |
| r/investing | 39,650 | 3,845 |
| r/options | 20,303 | 2,053 |
| **Total** | **327,097** | **68,720** |

- Entity mappings created: 88,201
- Duration: 43.8 minutes
- Cost: $0

### Remaining Gaps

- 931 symbols still have ZERO articles (mostly warrants, units, obscure micro-caps: AAC.U, AACI, AACT.WS, etc.)
- `sentiment_daily` coverage still low (1.9% of trades) because new articles haven't been LLM-analyzed yet
- User has a separate LLM analysis pipeline handling this; estimated cost: ~$4 for FMP articles, ~$46 for Reddit

### Bug Fix: article_sentiment JOIN

The original `load_article_detail()` used `INNER JOIN article_sentiment` which only returned LLM-analyzed articles. Changed to `LEFT JOIN` to count all articles for presence-based tests (S4, S5). Impact:

| Metric | Before (INNER JOIN) | After (LEFT JOIN) |
|--------|-------------------:|------------------:|
| Trades with pre-market article | 85 (6.5%) | **304 (23.3%)** |
| Trades with any article | ~150 | **472 (36.2%)** |

---

## 6. Sentiment Correlation Analysis

**Base dataset**: 1,303 Phase 3 VWAP reclaim trades
**Question**: Can news/sentiment data further filter these to improve performance?
**Baseline**: 47.6% WR, Sharpe 3.87, PF 1.34, +0.66% avg PnL
**Script**: `scripts/cameron_sentiment_correlation.py`
**Spec**: `Docs/cameron_sentiment_correlation_spec.md`

### TEST-S1: Has Catalyst (sentiment_daily mentions > 0) vs No Catalyst

| Group | N | WR | Avg PnL | Sharpe | PF |
|-------|--:|---:|--------:|-------:|---:|
| HAS CATALYST | 25 | 36.0% | -0.09% | -0.27 | 0.96 |
| NO CATALYST | 1,278 | 47.8% | +0.68% | 1.82 | 1.35 |

t-test: p=0.520 (not significant). Only 25 trades with sentiment_daily data.
**Verdict**: Underpowered. sentiment_daily has almost no coverage for Cameron micro-caps.

### TEST-S2: Sentiment Polarity Buckets

| Group | N | WR | Avg PnL | Sharpe |
|-------|--:|---:|--------:|-------:|
| NEGATIVE (< 0) | 0 | — | — | — |
| NEUTRAL (0 to 0.3) | 5 | 40.0% | +0.50% | 1.63 |
| POSITIVE (>= 0.3) | 20 | 35.0% | -0.23% | -0.69 |
| NO DATA | 1,278 | 47.8% | +0.68% | 1.82 |

**Verdict**: N too small (25 total with data). Cannot draw conclusions.

### TEST-S3: Mention Volume Buckets

| Group | N | WR | Avg PnL | Sharpe |
|-------|--:|---:|--------:|-------:|
| 0 mentions | 1,278 | 47.8% | +0.68% | 1.82 |
| 1-2 mentions | 16 | 37.5% | +0.51% | 1.43 |
| 3-4 mentions | 6 | 16.7% | -1.19% | -4.65 |
| 5-9 mentions | 2 | 50.0% | -2.26% | -4.93 |
| 10+ mentions | 1 | 100.0% | +1.28% | — |

**Verdict**: Underpowered. Directionally: higher mentions = worse, but N < 20 in all non-zero buckets.

### TEST-S4: News vs Social Source (article presence, no LLM required)

| Group | N | WR | Avg PnL | Sharpe | PF |
|-------|--:|---:|--------:|-------:|---:|
| **NEWS ONLY** | **79** | **49.4%** | **+0.87%** | **3.20** | **1.69** |
| SOCIAL ONLY | 362 | 47.5% | +0.76% | 1.86 | 1.36 |
| **BOTH** | **80** | **52.5%** | **+1.09%** | **2.86** | **1.64** |
| NEITHER | 782 | 46.9% | +0.55% | 1.54 | 1.29 |

**Verdict**: Most promising test. Having any coverage (news, social, or both) improves all metrics vs NEITHER. NEWS ONLY and BOTH have the best PF (1.64-1.69) and WR (49-53%). But N=79-80 for best groups and no statistical significance yet.

### TEST-S5: Pre-Market News (before 9:30 AM ET)

| Group | N | WR | Avg PnL | Sharpe | PF |
|-------|--:|---:|--------:|-------:|---:|
| HAS PRE-MARKET NEWS | 304 | 48.7% | +0.77% | 2.11 | 1.41 |
| NO PRE-MARKET NEWS | 999 | 47.2% | +0.63% | 1.69 | 1.32 |

t-test: p=0.719 (not significant).
**Verdict**: Directionally positive (+1.5pp WR, +0.14% PnL) but not significant. N=304 is reasonable but effect size is small.

### TEST-S6: Mentions Momentum (Spike Detection)

| Group | N | WR | Avg PnL | Sharpe |
|-------|--:|---:|--------:|-------:|
| SPIKING (mom > 1.0) | 2 | 100% | +2.08% | 29.05 |
| STEADY (-0.5 to 1.0) | 3 | 33.3% | +0.63% | 3.97 |
| FADING (< -0.5) | 0 | — | — | — |
| NO DATA | 1,298 | 47.5% | +0.66% | 1.78 |

**Verdict**: Completely underpowered (N=2, N=3). Cannot use.

### Correlation Matrix

| Feature | Corr | p-value | N |
|---------|-----:|--------:|--:|
| premarket_article_count | -0.002 | 0.954 | 1,303 |
| prev_day_article_count | +0.019 | 0.501 | 1,303 |
| reddit_count | +0.039 | 0.158 | 1,303 |
| news_count | -0.012 | 0.672 | 1,303 |
| premarket_avg_sentiment | +0.012 | 0.916 | 85 |
| prev_day_avg_sentiment | +0.097 | 0.483 | 54 |
| sentiment_index | -0.085 | 0.688 | 25 |

No feature shows statistically significant correlation with PnL. Article count features have sufficient N (1,303) but near-zero correlation. Sentiment features have insufficient N.

---

## 7. Conclusions & Next Steps

### What's Proven

1. **Cameron gap-up universe is real** — 4,042 symbols, 7,600+ daily signals across 3 years
2. **GAP_AND_GO intraday works** — Sharpe 4.35-6.17, consistent 2023-2025
3. **VWAP reclaim is the best entry** — 47.6% WR vs 18.7% WR for open-entry, 33.8% stop-out vs 78.9%
4. **NEXT_DAY_OPEN is dead** — negative PnL across ALL configs
5. **Higher RVOL threshold (>=10x) improves quality** — Sharpe 6.17 vs 4.35
6. **Moderate-only strength outperforms strong** — Sharpe 1.45 vs 0.17 for target_1 (N=1617 vs 661, both RELIABLE)
7. **Consolidation breakout is the highest-quality pattern** — Sharpe 2.80, WR 59.5%, PF 1.48 (N=301, RELIABLE)
8. **target_1 preferred over target_2** — higher WR (53.6% vs 45.3%), better median PnL (+0.63% vs -0.72%)

### B2 Filter Stack (DEPLOYED — v61)

The **B2** configuration was selected from E2E backtest analysis (2,278 trades, 2023-2026) as the optimal combination of filters for live trading:

| Filter | Value | Rationale |
|--------|-------|-----------|
| Pattern strength | moderate-only | Sharpe 1.45 vs 0.17 for strong (E2E Layer 1C) |
| RVOL | >= 10x | Phase 2 finding: Sharpe 6.17 vs 4.35 at >=5x |
| Scan window | 9:45-11:00 AM ET | Morning liquidity, enforced by scanner |
| Bull flag cap | max 1/day | BF is high-volume but lowest quality (Sharpe 0.65) |
| Exit strategy | target_1 | Higher WR and median PnL vs target_2 (Layer 4) |
| Patterns | All 3 (consol_breakout > vwap_reclaim > bull_flag) | Priority-sorted by quality |

**B2 backtest metrics** (moderate-only, all 3 patterns, target_1): Sharpe 2.51, WR 57.3%, PF 1.49, N=532, RELIABLE across all years.

### What's Inconclusive

1. **Sentiment/news as a filter on Cameron trades** — directionally positive (NEWS+BOTH have higher PF) but no statistical significance. The `sentiment_daily` pipeline has almost zero coverage for Cameron micro-caps (2.2% of trades). Article-presence tests (S4, S5) show small but consistent edge that may become significant with more data or LLM analysis.

2. **Float filter** — not tested in backtest (market_cap proxy available but not yet applied)

### What's Dead

1. **NEXT_DAY_OPEN** — holding overnight after a Cameron gap day loses money consistently
2. **sentiment_daily as a Cameron filter** — coverage too low for micro-cap universe (18/821 trades in 2025+)
3. **Strong-strength patterns** — Sharpe 0.17 vs 1.45 for moderate. Excluded from B2.

### Deployment Status (v61 — 2026-02-28)

All research phases have been implemented and deployed as **Account C** on Cloud Run:

| Component | Status | Notes |
|-----------|--------|-------|
| Pre-market scanner (`cameron_scanner.py`) | LIVE | Loads candidates from `orats_daily`, runs 3 detectors on 5-min Alpaca bars |
| Pattern checker (`cameron_checker.py`) | LIVE | Polls `cameron_scores` every 30s, B2 filter stack |
| Pattern detectors (bull_flag, consol_breakout, vwap_reclaim) | LIVE | Reused from `scripts/patterns/` |
| DB tables (`cameron_scores`, `paper_trades_log_c`) | CREATED | Schema mirrors Account B with Cameron-specific columns |
| Account C Alpaca paper account | CONNECTED | $100K equity, $200K buying power |
| Main loop integration | LIVE | Scanner (60s), checker (30s), WebSocket + REST stop/target monitoring |
| Dashboard (Google Sheets) | LIVE | 3 tabs: Cameron Signals, Cameron Positions, Cameron Closed |
| EOD closer | LIVE | 3:55 PM ET liquidation |
| Cloud Run revision | `paper-trading-live-00111-4rp` | All 3 accounts (A, B, C) operational |

**Rollback**: Set `USE_ACCOUNT_C = False` in `config.py`, redeploy. Accounts A and B continue unaffected.

### Remaining Next Steps

| Priority | Task | Effort |
|----------|------|--------|
| P0 | Monitor Account C live trades (first full trading week) | ongoing |
| P1 | Add float filter to backtest (market_cap proxy) | 2-3h |
| P1 | Re-run S4/S5 after LLM pipeline processes new articles | 1h |
| P2 | Tune B2 parameters based on live performance vs backtest | 2-4h |
| P2 | Evaluate adding RVOL dynamic threshold (backtest shows quality vs quantity tradeoff) | 4-6h |

### Files Reference

| File | Purpose |
|------|---------|
| `Docs/CAMERON_FINDINGS.md` | This document |
| `Docs/CAMERON_E2E_REPORT.md` | Comprehensive E2E analysis (2,278 trades, 4 layers) |
| `Docs/CLI_WORKPLAN_CAMERON_SCANNER.md` | Original Phase 0-5 workplan |
| `Docs/CLI_WORKPLAN_CAMERON_V2.md` | Updated workplan with Phase 2A/2B/2C |
| `Docs/CAMERON_DATA_AUDIT.md` | Phase 0 data availability audit |
| `Docs/cameron_sentiment_correlation_spec.md` | S1-S6 test design spec |
| `paper_trading/cameron_scanner.py` | Live pre-market scanner (Account C) |
| `paper_trading/cameron_checker.py` | B2 filter stack + pattern polling (Account C) |
| `scripts/patterns/bull_flag.py` | Bull flag detector |
| `scripts/patterns/consolidation_breakout.py` | Consolidation breakout detector |
| `scripts/patterns/vwap_reclaim.py` | VWAP reclaim detector |
| `scripts/cameron_news_backfill.py` | FMP + Reddit Arctic Shift backfill script |
| `scripts/cameron_sentiment_correlation.py` | S1-S6 analysis script |
| `scripts/backtest_cameron.py` | Phase 2 multi-day backtest engine |
| `scripts/backtest_cameron_intraday.py` | Phase 3 VWAP reclaim backtest |
| `scripts/build_daily_universe.py` | Cameron daily universe builder |
| `scripts/cameron_filter.py` | Cameron filter implementation |
| `sql/create_cameron_scores.sql` | cameron_scores table DDL |
| `sql/create_paper_trades_log_c.sql` | paper_trades_log_c table DDL |
| `backtest_results/cameron_backtest_results.json` | Phase 2 raw results (30 configs) |
| `backtest_results/cameron_intraday_summary.json` | Phase 3 raw results |
| `backtest_results/cameron_intraday_trades.csv` | Phase 3 per-trade output (1,303 trades) |
| `backtest_results/cameron_trades_with_sentiment.csv` | Phase 3 trades enriched with sentiment |
| `backtest_results/cameron_summary.csv` | Phase 2 summary (one row per config) |
