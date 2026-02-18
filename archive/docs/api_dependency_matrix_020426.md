# External API Dependency Matrix
**Generated**: 2026-02-04

## Summary

| API | V1 Uses | V2 Uses | Status |
|-----|---------|---------|--------|
| Polygon.io | No | Yes (Firehose + REST) | Active |
| Alpaca Markets | Yes (News) | Yes (Bars, Spot, Trading) | Active |
| ORATS | Yes (Daily FTP) | No (backtesting only) | Active |
| Reddit | Yes | No | BROKEN (since Jan 31) |
| Discord | Local only | No | Active |
| FMP | Yes (News) | Yes (Earnings) | Active |
| NewsData.io | Yes (News) | No | Active |
| FreeCryptoNews | Yes (News) | No | Active |
| OpenAI | Yes (Sentiment) | No | Active |
| Finnhub | Secret exists | Secret exists | NOT USED |

---

## Detailed API Documentation

### 1. Polygon.io

| Property | Value |
|----------|-------|
| **V1 Usage** | None currently |
| **V2 Usage** | Firehose WebSocket (T.* - ALL options trades), REST for stock bars |
| **Destination Tables** | In-memory aggregation → scoring → `signal_evaluations`, `active_signals` |
| **Frequency** | Real-time during market hours (9:30 AM - 4:00 PM ET) |
| **Rate Limits** | Unlimited WebSocket, 50,000 REST calls/day |
| **Secret** | `POLYGON_API_KEY` (both projects) |
| **Status** | Active |

**Data Flow**:
```
Polygon WebSocket (T.*) → TradeAggregator (60s window) → Scoring → Filters → Trades
Polygon REST (bars) → signal_filter.py → On-demand TA calculation
```

---

### 2. Alpaca Markets

| Property | Value |
|----------|-------|
| **V1 Usage** | News articles via `fr-media-news-alpaca` → `articles` |
| **V2 Usage** | Stock bars, spot prices, paper trading execution |
| **Destination Tables** | `articles` (V1), `spot_prices` (V2), `paper_trades_log` (V2) |
| **Frequency** | News: periodic, Spot: every 1 min, Trading: real-time |
| **Secrets** | Multiple keys exist (see below) |
| **Status** | Active |

**Secret Mapping**:
| Secret Name | Project | Used By |
|-------------|---------|---------|
| `APCA_API_KEY_ID` | V1 | Legacy |
| `APCA_PAPER_API_KEY_ID` | V1 | Legacy paper trading |
| `APCA_V19PLUS_API_KEY_ID` | V1 | `paper-trading-live` (V1 project) |
| `ALPACA_API_KEY` | V2 | `paper-trading-live` (V2 project) |

**ISSUE**: Two different `paper-trading-live` services exist using different Alpaca accounts!

---

### 3. ORATS

| Property | Value |
|----------|-------|
| **V1 Usage** | Daily FTP download via `orats-daily-ingest` |
| **V2 Usage** | None in live trading (backtesting scripts only) |
| **Destination Tables** | `orats_daily` (2.9M rows) |
| **Frequency** | Daily at 10 PM PT |
| **Secrets** | `ORATS_FTP_USER`, `ORATS_FTP_PASSWORD` |
| **Status** | Active (V1), NOT USED by V2 live |

**Important**: The `orats-track-top50` job is FAILING but V2 does NOT depend on it.

---

### 4. Reddit API

| Property | Value |
|----------|-------|
| **V1 Usage** | `fr-social-scrape`, `fr-reddit-comments` |
| **V2 Usage** | None |
| **Destination Tables** | `articles` (source='reddit'), `reddit_post_state` |
| **Frequency** | Was periodic |
| **Secrets** | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`, `REDDIT_REFRESH_TOKEN`, `REDDIT_USER_AGENT` |
| **Status** | BROKEN - last data Jan 31, 2026 |

---

### 5. Discord

| Property | Value |
|----------|-------|
| **V1 Usage** | Local reader only (not Cloud Run) |
| **V2 Usage** | None |
| **Destination Tables** | `discord_mentions` |
| **Frequency** | Continuous when running locally |
| **Secret** | `DISCORD_BOT_TOKEN` |
| **Status** | Active (local) |

---

### 6. FMP (Financial Modeling Prep)

| Property | Value |
|----------|-------|
| **V1 Usage** | News via `fr3-media-news-fmp` → `articles` |
| **V2 Usage** | Earnings via `fetch-earnings-calendar` → `earnings_calendar` |
| **Destination Tables** | `articles`, `earnings_calendar` |
| **Frequency** | News: every 10 min, Earnings: daily at 4 AM |
| **Secret** | `FMP_API_KEY` (both projects) |
| **Status** | Active |

---

### 7. NewsData.io

| Property | Value |
|----------|-------|
| **V1 Usage** | News via `fr3-media-news-api` → `articles` |
| **V2 Usage** | None |
| **Destination Tables** | `articles` |
| **Frequency** | Every 10 min |
| **Secrets** | `API_KEY_NEWSDATA_IO`, `NEWSDATA_APIKEY` |
| **Status** | Active |

**ISSUE**: Duplicate secrets exist - likely same key stored twice.

---

### 8. FreeCryptoNews

| Property | Value |
|----------|-------|
| **V1 Usage** | Crypto news via `fr3-media-news-freecryptonews` → `articles` |
| **V2 Usage** | None |
| **Destination Tables** | `articles` |
| **Frequency** | Every 5 min |
| **Secret** | None (public API) |
| **Status** | Active |

---

### 9. OpenAI

| Property | Value |
|----------|-------|
| **V1 Usage** | Sentiment analysis via `fr-media-analyze` |
| **V2 Usage** | None |
| **Destination Tables** | `article_insights`, `article_sentiment` |
| **Frequency** | Every 15 min |
| **Secret** | `OPENAI_API_KEY` |
| **Status** | Active |

---

### 10. Finnhub

| Property | Value |
|----------|-------|
| **V1 Usage** | Secret exists but no job found using it |
| **V2 Usage** | Secret exists but no job found using it |
| **Destination Tables** | Unknown |
| **Secret** | `FINNHUB_API_KEY` (both projects) |
| **Status** | NOT USED |

---

## Redundant API Access

| API | Redundancy | Resolution |
|-----|------------|------------|
| Alpaca | 4 different key sets across projects | Consolidate to 1 paper + 1 live key |
| FMP | Same key in both projects | OK - different purposes (news vs earnings) |
| NewsData.io | 2 secrets with same key | Delete `NEWSDATA_APIKEY`, keep `API_KEY_NEWSDATA_IO` |
| Finnhub | Keys in both projects, neither used | Delete from both projects |
