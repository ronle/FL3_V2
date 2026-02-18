# V1 Pipeline Entry Points
**Generated**: 2026-02-04

## Overview

This document maps every active V1 code path that runs in production, what it reads, and what it writes.

**V1 Location**: `C:\Users\levir\Documents\FL3`

---

## Entry Point Summary

| Entry Point | GCP Job | Image | Tables Read | Tables Written |
|-------------|---------|-------|-------------|----------------|
| media-ingest | fr-media-ingest | fr-media-jobs | master_tickers, company_aliases, article_sources | articles, ready_for_analysis |
| media-analyze | fr-media-analyze | fr-media-jobs | ready_for_analysis, articles, company_aliases | article_sentiment, article_insights, article_entities, combo_mentions |
| sentiment-aggregate | fr-sentiment-agg | fr-media-jobs | articles, article_sentiment, article_insights | sentiment_daily, catalyst_calendar |
| media-news-fmp | fr3-media-news-fmp | fr-media-jobs | articles, article_sources | articles, ready_for_analysis |
| media-news-alpaca | fr-media-news-alpaca | fr-media-jobs | articles, article_sources | articles, ready_for_analysis |
| media-news-freecryptonews | fr3-media-news-freecryptonews | cli:v2 | articles | articles, ready_for_analysis |
| social-scrape | fr-social-scrape | fr-media-jobs | article_sources, articles | articles, ready_for_analysis |
| reddit-comments | fr-reddit-comments | fr-media-jobs | articles, reddit_post_state | articles, reddit_post_state, ready_for_analysis |
| orats-daily-ingest | orats-daily-ingest | fr-orats-jobs | orats_daily, master_tickers | orats_daily |

---

## Detailed Entry Points

### 1. media-ingest (fr-media-ingest)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `cli.py:cli_media_ingest()` (lines 70-130) |
| **Source Module** | `ingestion/ingest.py:ingest_from_adapter()` |
| **Adapters** | `ingestion/adapters/` (rss, sitemap, API-based) |

**Tables Read:**
| Table | Query Pattern | Purpose |
|-------|--------------|---------|
| `master_tickers` | `SELECT upper(symbol)` | Ticker validation |
| `company_aliases` | `SELECT alias, upper(symbol)` | Alias resolution |
| `article_sources` | `SELECT` source configuration | Source config |

**Tables Written:**
| Table | Operation | Purpose |
|-------|-----------|---------|
| `articles` | INSERT/UPSERT by canonical_url | Store articles |
| `ready_for_analysis` | INSERT/UPSERT article_id | Queue for LLM |

---

### 2. media-analyze (fr-media-analyze)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `cli.py:cli_media_analyze()` (lines 146-168) |
| **Source Module** | `analysis/media/llm_bot.py:run_llm_analysis_once()` (line 347+) |
| **External API** | OpenAI GPT for sentiment analysis |

**Tables Read:**
| Table | Query Pattern | Purpose |
|-------|--------------|---------|
| `ready_for_analysis` | `SELECT article_id, queued_at` | Get queue |
| `articles` | `SELECT title, summary, content` | Article text |
| `company_aliases` | Ticker resolution | Map aliases |

**Tables Written:**
| Table | Operation | Purpose |
|-------|-----------|---------|
| `article_sentiment` | INSERT/UPSERT | Sentiment scores |
| `article_insights` | INSERT/UPSERT | Stance, novelty, quality |
| `article_entities` | INSERT | Entity mentions |
| `combo_mentions` | INSERT/UPSERT | Influencer aggregates |
| `ready_for_analysis` | UPDATE | Mark processed |

---

### 3. sentiment-aggregate (fr-sentiment-agg) — FAILING

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `cli.py` → `analysis.sentiment_rollup:cli_sentiment_aggregate` (line 1549) |
| **Source Module** | `analysis/core/sentiment_rollup.py:cli_sentiment_aggregate()` (lines 314-327) |
| **Status** | **FAILING — sentiment_daily 14 days stale** |

**Tables Read:**
| Table | Query Pattern | Purpose |
|-------|--------------|---------|
| `articles` | Aggregate by ticker, source | Article counts |
| `article_sentiment` | `SELECT sentiment scores` | Scores to aggregate |
| `article_insights` | `SELECT stance_by_ticker` | Stance data |

**Tables Written:**
| Table | Operation | Purpose |
|-------|-----------|---------|
| `sentiment_daily` | INSERT/UPSERT asof_date, ticker | Daily rollup |
| `catalyst_calendar` | CREATE IF NOT EXISTS | Event tracking |

**Critical Note**: This job's failure breaks V2 paper trading's crowded trade filter.

---

### 4. media-news-fmp (fr3-media-news-fmp)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `ingestion/adapters/fmp_news.py:cli_media_news_fmp()` (line 302+) |
| **Source Module** | `ingestion/adapters/fmp_news.py:fetch_fmp_news()` (lines 67-130) |
| **External API** | FMP (Financial Modeling Prep) |

**Tables Read:**
| Table | Purpose |
|-------|---------|
| `articles` | Duplicate check by canonical_url |
| `article_sources` | Incremental state tracking |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `articles` | INSERT/UPSERT via MediaStore |
| `ready_for_analysis` | Enqueue for LLM |

---

### 5. media-news-alpaca (fr-media-news-alpaca)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `ingestion/adapters/alpaca_news.py:cli_media_news_alpaca()` (line 125+) |
| **Source Module** | `ingestion/adapters/alpaca_news.py:fetch_alpaca_news()` (line 37+) |
| **External API** | Alpaca Market Data API |

**Tables Read/Written:** Same pattern as media-news-fmp

---

### 6. media-news-freecryptonews (fr3-media-news-freecryptonews)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `ingestion/adapters/freecryptonews_adapter.py:cli_media_news_freecryptonews()` (line 281+) |
| **Source Module** | `ingestion/adapters/freecryptonews_adapter.py:fetch_freecryptonews()` (line 45+) |
| **External API** | FreeCryptoNews (free, no auth) |

**Tables Read/Written:** Same pattern as media-news-fmp

---

### 7. social-scrape (fr-social-scrape)

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `cli.py:cli_social_scrape()` (lines 469-615) |
| **Source Modules** | `ingestion/adapters/reddit_adapter.py`, `reddit_api.py`, `discord_adapter.py` |
| **External APIs** | Reddit (JSON + OAuth), Discord |

**Tables Read:**
| Table | Purpose |
|-------|---------|
| `article_sources` | Active sources by kind (reddit, discord) |
| `articles` | Duplicate check |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `articles` | INSERT/UPSERT via MediaStore |
| `ready_for_analysis` | Enqueue |

---

### 8. reddit-comments (fr-reddit-comments) — BROKEN

| Property | Value |
|----------|-------|
| **CLI Entry Point** | `cli.py:cli_reddit_comments()` (lines 738-910) |
| **Source Module** | `ingestion/adapters/reddit_comments.py:fetch_post_comments_delta()` (line 75+) |
| **Status** | **BROKEN — last data Jan 31, 2026** |

**Tables Read:**
| Table | Query Pattern | Purpose |
|-------|--------------|---------|
| `articles` | `WHERE source='reddit' AND external_id LIKE 't3_%'` | Recent Reddit posts |
| `reddit_post_state` | Track last_comment_id, hot_until | Incremental state |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `articles` | INSERT/UPSERT comment articles |
| `reddit_post_state` | CREATE TABLE IF NOT EXISTS, INSERT/UPSERT |
| `ready_for_analysis` | Enqueue |

---

### 9. orats-daily-ingest

| Property | Value |
|----------|-------|
| **Entry Point** | `sources/orats_ingest.py:ingest_orats_daily()` (line 1538+) |
| **Main** | `sources/orats_ingest.py:main()` (line 1665+) |
| **External Source** | ORATS FTP (us4.hostedftp.com) |
| **Schedule** | Daily 10 PM PT |

**Tables Read:**
| Table | Purpose |
|-------|---------|
| `orats_daily` | Compute IV rank, EMA, HV, rolling stats |
| `master_tickers` | Filter symbols if needed |

**Tables Written:**
| Table | Operation |
|-------|-----------|
| `orats_daily` | INSERT/UPSERT with iv_rank, iv_mean_rank, hv_30day, etc. |

**Note**: V2 does NOT use orats_daily in live trading — only for backtesting/analysis.

---

### 10. fr-media-scheduler (Service)

| Property | Value |
|----------|-------|
| **Entry Point** | `media_scheduler.py:main()` |
| **Type** | Cloud Run Service (always-on) |
| **Status** | DEGRADED (running on stale revision) |

**Orchestrates:**
- `media-ingest-db` periodically
- `media-analyze` periodically
- `social-scrape` (reddit, discord) via worker thread
- `reddit-comments` periodically
- `sentiment-aggregate` daily

**Database Impact**: Indirect (orchestrates the above commands)

---

## Database Schema Map

### Media/Articles Tables

| Table | Written By | Read By | Key Fields |
|-------|-----------|---------|-----------|
| `articles` | All news/social jobs | media-analyze, sentiment-agg | id, canonical_url, title, source, publish_time |
| `ready_for_analysis` | All news/social jobs | media-analyze | article_id, queued_at |
| `article_sentiment` | media-analyze | sentiment-aggregate | article_id, sentiment, confidence |
| `article_insights` | media-analyze | sentiment-aggregate | article_id, stance_by_ticker, novelty_score |
| `article_entities` | media-analyze | — | article_id, entity_type, entity_value |
| `combo_mentions` | media-analyze | — | date, influencer, ticker, article_count |
| `sentiment_daily` | sentiment-aggregate | **V2 paper trading** | asof_date, ticker, mentions, sentiment_index |
| `catalyst_calendar` | sentiment-aggregate | — | ticker, event_date, kind, phase |
| `reddit_post_state` | reddit-comments | reddit-comments | post_external_id, last_comment_id |
| `article_sources` | manual admin | All news jobs | id, kind, url, enabled |

### Options Data Tables

| Table | Written By | Read By |
|-------|-----------|---------|
| `orats_daily` | orats-daily-ingest | V2 analysis scripts (NOT live trading) |
| `company_aliases` | manual admin | media-ingest, media-analyze |
| `master_tickers` | manual admin / V2 refresh-sector-data | media-ingest, V2 paper trading |

---

## Dependency Chain

```
News APIs (FMP, NewsData, FreeCrypto, Alpaca)
    ↓
fr3-media-news-* jobs (every 5-10 min)
    ↓
articles table (627K rows)
    ↓
fr-media-analyze job (every 15 min, OpenAI)
    ↓
article_sentiment, article_insights, combo_mentions
    ↓
fr-sentiment-agg job (daily 5:30 AM) ← FAILING
    ↓
sentiment_daily table ← 14 DAYS STALE
    ↓
V2 paper-trading-live (crowded trade filter)
```

---

## File Paths

| Component | Absolute Path |
|-----------|---------------|
| CLI dispatcher | `C:\Users\levir\Documents\FL3\cli.py` |
| Media scheduler | `C:\Users\levir\Documents\FL3\media_scheduler.py` |
| Ingestion core | `C:\Users\levir\Documents\FL3\ingestion\ingest.py` |
| LLM analysis | `C:\Users\levir\Documents\FL3\analysis\media\llm_bot.py` |
| Sentiment rollup | `C:\Users\levir\Documents\FL3\analysis\core\sentiment_rollup.py` |
| ORATS ingest | `C:\Users\levir\Documents\FL3\sources\orats_ingest.py` |
| Media store | `C:\Users\levir\Documents\FL3\storage\media_store.py` |
| Adapters | `C:\Users\levir\Documents\FL3\ingestion\adapters\` |
