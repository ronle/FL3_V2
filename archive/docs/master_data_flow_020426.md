# Master Data Flow Diagram
**Generated**: 2026-02-04

## Overview

This document maps all data flows across V1 (spartan-buckeye) and V2 (fl3-v2-prod) projects, showing how data moves from external APIs through processing to consumption.

---

## Architecture Diagram (Mermaid)

```mermaid
flowchart TB
    subgraph External["External APIs"]
        POLYGON[("Polygon.io")]
        ALPACA[("Alpaca Markets")]
        ORATS[("ORATS FTP")]
        FMP[("FMP")]
        NEWSDATA[("NewsData.io")]
        FREECRYPTO[("FreeCryptoNews")]
        REDDIT[("Reddit")]
        OPENAI[("OpenAI")]
        DISCORD[("Discord")]
    end

    subgraph V1["V1 Project (spartan-buckeye)"]
        subgraph V1Jobs["Cloud Run Jobs"]
            J_ORATS["orats-daily-ingest<br/>Daily 10PM"]
            J_FMP_NEWS["fr3-media-news-fmp<br/>Every 10min"]
            J_NEWSDATA["fr3-media-news-api<br/>Every 10min"]
            J_FREECRYPTO["fr3-media-news-freecryptonews<br/>Every 5min"]
            J_ALPACA_NEWS["fr-media-news-alpaca"]
            J_ANALYZE["fr-media-analyze<br/>Every 15min"]
            J_SENTIMENT["fr-sentiment-agg<br/>Daily 5:30AM"]
            J_REDDIT["fr-reddit-comments"]
        end
        subgraph V1Services["Services"]
            S_SCHEDULER["fr-media-scheduler"]
            S_PAPER_V1["paper-trading-live<br/>(V1 - NOT ACTIVE)"]
        end
    end

    subgraph V2["V2 Project (fl3-v2-prod)"]
        subgraph V2Jobs["Cloud Run Jobs"]
            J_TA["fl3-v2-ta-pipeline<br/>Every 5min RTH"]
            J_SPOT["update-spot-prices<br/>Every 1min RTH"]
            J_EARNINGS["fetch-earnings-calendar<br/>Daily 4AM"]
            J_PREMARKET["premarket-ta-cache<br/>Daily 6AM"]
            J_SECTOR["refresh-sector-data<br/>Weekly"]
        end
        subgraph V2Services["Services"]
            S_PAPER_V2["paper-trading-live<br/>(V2 - ACTIVE)"]
        end
    end

    subgraph Local["Local Processes"]
        L_DISCORD["Discord Reader"]
    end

    subgraph DB["Shared Database (fl3)"]
        T_ORATS[("orats_daily<br/>2.9M rows")]
        T_ARTICLES[("articles<br/>627K rows")]
        T_SENTIMENT[("article_sentiment<br/>420K rows")]
        T_SENT_DAILY[("sentiment_daily<br/>40K rows")]
        T_TA[("ta_daily_close<br/>213K rows")]
        T_SPOT[("spot_prices<br/>32K rows")]
        T_EARNINGS[("earnings_calendar<br/>48K rows")]
        T_MASTER[("master_tickers<br/>5.9K rows")]
        T_SIGNALS[("signal_evaluations<br/>2K rows")]
        T_ACTIVE[("active_signals<br/>5 rows")]
        T_TRADES[("paper_trades_log<br/>5 rows")]
        T_DISCORD[("discord_mentions<br/>942 rows")]
    end

    %% V1 Data Flows
    ORATS -->|FTP| J_ORATS --> T_ORATS
    FMP -->|REST| J_FMP_NEWS --> T_ARTICLES
    NEWSDATA -->|REST| J_NEWSDATA --> T_ARTICLES
    FREECRYPTO -->|REST| J_FREECRYPTO --> T_ARTICLES
    ALPACA -->|REST| J_ALPACA_NEWS --> T_ARTICLES
    T_ARTICLES --> J_ANALYZE
    OPENAI -->|API| J_ANALYZE --> T_SENTIMENT
    T_SENTIMENT --> J_SENTIMENT --> T_SENT_DAILY
    REDDIT -.->|BROKEN| J_REDDIT

    %% V2 Data Flows
    POLYGON -->|WebSocket| S_PAPER_V2
    ALPACA -->|REST| J_TA --> T_TA
    ALPACA -->|REST| J_SPOT --> T_SPOT
    FMP -->|REST| J_EARNINGS --> T_EARNINGS
    J_SECTOR --> T_MASTER

    %% V2 Paper Trading Reads
    T_MASTER -.->|sector filter| S_PAPER_V2
    T_SENT_DAILY -.->|crowded filter| S_PAPER_V2

    %% V2 Paper Trading Writes
    S_PAPER_V2 --> T_SIGNALS
    S_PAPER_V2 --> T_ACTIVE
    S_PAPER_V2 --> T_TRADES

    %% Local
    DISCORD --> L_DISCORD --> T_DISCORD

    %% Styling
    classDef active fill:#90EE90
    classDef degraded fill:#FFD700
    classDef broken fill:#FF6B6B
    classDef notused fill:#D3D3D3

    class J_ORATS,J_FMP_NEWS,J_NEWSDATA,J_FREECRYPTO,J_ANALYZE,J_TA,J_SPOT,J_EARNINGS,J_PREMARKET,S_PAPER_V2 active
    class J_SENTIMENT,S_SCHEDULER degraded
    class J_REDDIT,S_PAPER_V1 broken
```

---

## Pipeline Status Summary

### Active & Healthy (Green)

| Pipeline | Project | Scheduler | Destination | Status |
|----------|---------|-----------|-------------|--------|
| ORATS Daily Ingest | V1 | Daily 10PM | `orats_daily` | Working |
| FMP News | V1 | Every 10min | `articles` | Working |
| NewsData News | V1 | Every 10min | `articles` | Working |
| FreeCryptoNews | V1 | Every 5min | `articles` | Working |
| Media Analyze | V1 | Every 15min | `article_sentiment`, `article_insights` | Working |
| TA Pipeline | V2 | Every 5min RTH | `ta_daily_close`, `ta_snapshots_v2` | Working |
| Spot Prices | V2 | Every 1min RTH | `spot_prices` | Working |
| Earnings Calendar | V2 | Daily 4AM | `earnings_calendar` | Working |
| Premarket TA Cache | V2 | Daily 6AM | `ta_daily_close` | Working |
| Sector Data Refresh | V2 | Weekly | `master_tickers` | Working |
| Paper Trading | V2 | Continuous | `signal_evaluations`, `active_signals`, `paper_trades_log` | Working |
| Discord Reader | Local | Continuous | `discord_mentions` | Working |

### Degraded (Yellow)

| Pipeline | Project | Issue | Impact |
|----------|---------|-------|--------|
| Sentiment Aggregation | V1 | `fr-sentiment-agg` job failing | `sentiment_daily` 14 days stale |
| Media Scheduler | V1 | Running on stale revision | May affect job orchestration |

### Broken/Inactive (Red)

| Pipeline | Project | Issue | Impact |
|----------|---------|-------|--------|
| Reddit Ingest | V1 | Stopped Jan 31, 2026 | No Reddit data flowing |
| ORATS Top 50 | V1 | Job failing | None - V2 doesn't use it |
| Paper Trading (V1) | V1 | Uses different Alpaca account | Confusion - should be deleted |

### Not Integrated (Gray)

| Component | Status | Notes |
|-----------|--------|-------|
| `intraday_baselines_30m` | Data accumulates but never read | `load_baselines()` never called |
| `earnings_calendar` | Populated but not queried | Earnings filter not implemented in V2 |
| `ta_daily_close` | Populated but not queried in live | V2 fetches TA from Polygon API directly |

---

## Cross-Project Data Dependencies

### Critical Path: V1 → V2

```
V1 Media Pipeline:
  articles → article_sentiment → fr-sentiment-agg → sentiment_daily
                                                            │
                                                            ▼
                                          V2 paper-trading-live
                                          (crowded trade filter)
```

**This is the ONLY V1→V2 live dependency, and it's BROKEN (sentiment_daily is 14 days stale)**

### V2 Internal Dependencies

```
V2 Pipelines → Shared Database → V2 Paper Trading

  update-spot-prices → spot_prices ← (not currently queried)
  fetch-earnings-calendar → earnings_calendar ← (not currently queried)
  refresh-sector-data → master_tickers ← sector concentration filter
  premarket-ta-cache → ta_daily_close ← (not queried - uses Polygon API)
```

### V1 Internal Dependencies

```
V1 Media Pipeline (self-contained):

  News APIs → articles → fr-media-analyze → article_sentiment
                                                    │
                                                    ▼
                                           fr-sentiment-agg
                                                    │
                                                    ▼
                                           sentiment_daily
```

---

## Table Write Ownership

| Table | Primary Writer | Secondary Writer |
|-------|----------------|------------------|
| `orats_daily` | V1 `orats-daily-ingest` | None |
| `articles` | V1 news jobs (4 sources) | None |
| `article_sentiment` | V1 `fr-media-analyze` | None |
| `sentiment_daily` | V1 `fr-sentiment-agg` | None |
| `ta_daily_close` | V2 `premarket-ta-cache` | None |
| `spot_prices` | V2 `update-spot-prices` | None |
| `earnings_calendar` | V2 `fetch-earnings-calendar` | None |
| `master_tickers` | V2 `refresh-sector-data` | V1 sync scripts |
| `signal_evaluations` | V2 `paper-trading-live` | None |
| `active_signals` | V2 `paper-trading-live` | None |
| `paper_trades_log` | V2 `paper-trading-live` | None |
| `discord_mentions` | Local Discord reader | None |

---

## Data Freshness Requirements

| Table | Required Freshness | Current Status | Consumer |
|-------|-------------------|----------------|----------|
| `sentiment_daily` | Daily | 14 days stale | V2 paper trading |
| `master_tickers` | Weekly | Current | V2 paper trading |
| `spot_prices` | 1 minute | Current | V2 paper trading (not queried) |
| `earnings_calendar` | Daily | Current | V2 paper trading (not queried) |
| `ta_daily_close` | Daily | Current | V2 paper trading (not queried) |
| `orats_daily` | Daily | Current | V2 backtesting only |
