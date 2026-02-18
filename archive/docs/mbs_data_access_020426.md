# MultiBotSessions Data Access Patterns
**Generated**: 2026-02-04
**Last Updated**: 2026-02-05

## Overview

This document maps every point where MultiBotSessions (MBS) code accesses databases, including cross-database access to fl3.

**MBS Location**: `C:\Users\levir\Documents\MultiBots\multibotsessions`

---

## Database Architecture

MBS uses TWO separate PostgreSQL databases:

| Database | Purpose | Connection |
|----------|---------|------------|
| **bot_arena** | Local MBS trading state | `DATABASE_URL` env var |
| **fl3** | Remote FL3 market data (read-only) | `SENTIMENT_DATABASE_URL` or derived |

---

## Bot → Database Mapping

### Crypto Bots (No FL3 Access)

| Bot | FL3 Tables | bot_arena Tables | External APIs |
|-----|-----------|-----------------|---------------|
| **crypto_ta** | — | accounts, trades, positions, daily_snapshots | CCXT (Binance) |
| **crypto_ta_llm** | — | + llm_usage | CCXT, Anthropic/OpenAI |
| **crypto_news_llm** | — | + llm_usage | CCXT, News APIs, Anthropic |

### US Equity Bots (FL3 Access)

| Bot | FL3 Tables Read | bot_arena Tables | External APIs |
|-----|----------------|-----------------|---------------|
| **fl3_core** | (stub - not implemented) | accounts, trades, positions | — |
| **fl3_llm** | (stub - not implemented) | accounts, trades, positions | Anthropic |
| **free_cage_llm** | articles, article_entities, article_sentiment, uoa_triggers_v2, gex_metrics_snapshot, ta_snapshots_v2 | + llm_usage, bot_watch_state | Anthropic, yfinance (VIX) |
| **social_sentiment_llm** | **reddit_ticker_counts**, vw_media_daily_features, discord_mentions, articles, article_entities, article_sentiment | + llm_usage, bot_watch_state | Anthropic |

---

## Cross-Database Access (MBS → fl3)

### Tables Accessed via fl3_pool

#### social_sentiment_llm

| FL3 Table | Query Purpose | Data Freshness |
|-----------|---------------|----------------|
| `reddit_ticker_counts` | Reddit daily mention counts (7 days) | T-1 (yesterday) |
| `vw_media_daily_features` | Sentiment scores (avg_stance_weighted) | T-1 (yesterday) |
| `discord_mentions` | Hourly Discord mention counts (24h rolling) | Real-time |
| `articles` | Raw Reddit/Discord post titles/content | Last 6-48 hours |
| `article_entities` | Ticker extractions from articles | With articles |
| `article_sentiment` | Sentiment scores per article | With articles |

**Query Pattern (Updated 2026-02-05):**
```python
# Get Reddit mentions + sentiment (joined query)
SELECT r.ticker, r.asof_date, r.mention_count as mentions,
       COALESCE(v.avg_stance_weighted, 0) as sentiment
FROM reddit_ticker_counts r
LEFT JOIN vw_media_daily_features v
    ON r.ticker = v.ticker AND r.asof_date = v.asof_date
WHERE r.asof_date >= CURRENT_DATE - 7

# Get Discord mentions
SELECT symbol, mention_hour, mention_count, channel_id
FROM discord_mentions
WHERE mention_date >= CURRENT_DATE - 1
```

#### free_cage_llm

| FL3 Table | Query Purpose | Data Freshness |
|-----------|---------------|----------------|
| `articles` | News headlines for equity tickers (7 days) | Recent |
| `article_entities` | Ticker extractions | With articles |
| `article_sentiment` | Sentiment labels | With articles |
| `uoa_triggers_v2` | Unusual options activity signals | Last 60 min |
| `gex_metrics_snapshot` | Gamma/delta exposure metrics | Last 24h |
| `ta_snapshots_v2` | Technical analysis (RSI, ATR, VWAP) | Last 24h |

**Query Pattern:**
```python
# Get UOA triggers
SELECT symbol, trigger_ts, volume_ratio, call_pct
FROM uoa_triggers_v2
WHERE trigger_ts >= NOW() - INTERVAL '60 minutes'
ORDER BY trigger_ts DESC LIMIT 50

# Get GEX snapshot
SELECT symbol, net_gex, net_dex, gamma_flip_level
FROM gex_metrics_snapshot
WHERE snapshot_ts >= NOW() - INTERVAL '24 hours'
```

---

## MBS Database Tables (bot_arena)

### Tables Written By All Bots

| Table | Purpose | Operations |
|-------|---------|------------|
| `accounts` | Bot account info, balance | Created by arena engine |
| `trades` | Trade entries (symbol, side, price, qty) | INSERT by bots |
| `positions` | Open position tracking | INSERT/UPDATE by bots |
| `daily_snapshots` | End-of-day performance metrics | INSERT daily (LLM stats from ArenaStats) |
| `llm_usage` | LLM API call cost tracking | **NOT USED** - tracked in `.arena_stats.json` |
| `bot_watch_state` | Persistent state for watch mode | INSERT/UPDATE |

### Tables Read By All Bots

| Table | Purpose |
|-------|---------|
| `accounts` | Get current balance and account status |
| `trades` | Fetch recent trades for P&L calculation |
| `positions` | Check open positions for risk management |

---

## Connection Management

### FL3 Pool (asyncpg)

**File:** `market_data_service.py`

```python
async def create_fl3_pool() -> Optional[asyncpg.Pool]:
    # Priority 1: SENTIMENT_DATABASE_URL (explicit)
    dsn = os.environ.get("SENTIMENT_DATABASE_URL")

    # Priority 2: Derive from DATABASE_URL
    if not dsn:
        base_url = os.environ.get("DATABASE_URL", "")
        dsn = base_url.replace("/bot_arena", "/fl3")

    # Pool: min=1, max=5 (lightweight, read-only)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return pool
```

### MBS Pool (asyncpg)

**File:** `database.py`

```python
class Database:
    async def connect(self) -> "Database":
        self.dsn = os.environ.get("DATABASE_URL")  # bot_arena
        self._pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=5,
            max_size=20,  # Supports 6-7 concurrent bots
            command_timeout=60,
            ssl=ssl_context  # GCP Cloud SQL
        )
```

---

## Data Flow Diagram

```
                    ┌─────────────────────────────────────┐
                    │   MarketDataService.fetch_all()     │
                    │   (Runs once per trading cycle)     │
                    └──────────────────┬──────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│ Crypto OHLCV  │           │ FL3 Headlines │           │ FL3 Social    │
│ (CCXT/Binance)│           │ (articles)    │           │ (reddit/disc) │
└───────┬───────┘           └───────┬───────┘           └───────┬───────┘
        │                           │                           │
        │                           │                           │
        └───────────────────────────┴───────────────────────────┘
                                    │
                                    ▼
                         ┌─────────────────────────┐
                         │  market_data dict       │
                         │  • _headlines           │
                         │  • _uoa_triggers        │
                         │  • _gex_snapshots       │
                         │  • _equity_ta           │
                         │  • _fear_greed          │
                         │  • _vix                 │
                         │  • _fl3_pool            │
                         │  • + crypto OHLCV       │
                         └────────────┬────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
    CRYPTO BOTS              FREE_CAGE_LLM                SOCIAL_SENTIMENT_LLM
    (no FL3 access)          (full FL3 access)            (social FL3 access)
        │                             │                             │
        │                             │                             │
        └─────────────────────────────┴─────────────────────────────┘
                                      │
                                      ▼
                           ┌──────────────────────┐
                           │   bot_arena DB       │
                           │  • accounts          │
                           │  • trades            │
                           │  • positions         │
                           │  • daily_snapshots   │
                           │  • llm_usage         │
                           │  • bot_watch_state   │
                           └──────────────────────┘
```

---

## External API Usage

| API | Bot(s) | Purpose |
|-----|--------|---------|
| CCXT (Binance) | crypto_* | OHLCV data, prices, order simulation |
| Anthropic Claude | All LLM bots | Decision-making, signal interpretation |
| OpenAI | All LLM bots (optional) | Alternative LLM provider |
| yfinance | free_cage_llm | VIX index (market fear) |
| FreeCryptoNews | All bots | Fear & Greed index |

---

## Social Data Consolidation Status

**Document:** `SOCIAL_DATA_CONSOLIDATION_PLAN.md`

### Issues Resolved (2026-02-05)
1. ✅ Discord has dedicated `discord_mentions` table - Working well
2. ✅ Reddit now has `reddit_ticker_counts` table (7,741 rows backfilled)
3. ✅ Unified views created: `vw_social_mentions_hourly` and `vw_social_mentions_daily`

### Completed Solution

| Phase | Action | Status |
|-------|--------|--------|
| 1 | Create `reddit_ticker_counts` table | ✅ **DONE** |
| 2 | Keep `sentiment_daily` for V2, MBS uses `vw_media_daily_features` | ✅ **DONE** |
| 3 | Create unified `vw_social_mentions_hourly` view | ✅ **DONE** |
| 4 | Create unified `vw_social_mentions_daily` view | ✅ **DONE** |

### Current MBS Usage
- `social_sentiment_llm` now uses `reddit_ticker_counts` + `vw_media_daily_features` (for sentiment) + `discord_mentions`
- Daily snapshots read LLM stats from ArenaStats (in-memory), not `llm_usage` table
- V2 paper trading uses `vw_media_daily_features` (migrated from `sentiment_daily`)

---

## Critical Findings

### Clean Separation
- MBS bots read from BOTH fl3 and bot_arena databases
- FL3 provides **read-only** market data
- MBS bot_arena provides **read-write** trading state
- **No writes back to FL3 from MBS bots**

### Bots Without FL3 Access
- crypto_ta: Pure CCXT/TA
- crypto_ta_llm: CCXT + LLM
- crypto_news_llm: News APIs + CCXT + LLM
- fl3_core: Stub (not implemented)
- fl3_llm: Stub (not implemented)

### Bots With FL3 Access
- free_cage_llm: Full market data (headlines, UOA, GEX, TA)
- social_sentiment_llm: Social data (Reddit, Discord)

### Data Freshness

| Data | Freshness | Source |
|------|-----------|--------|
| Reddit mentions | T-1 (yesterday) | reddit_ticker_counts |
| Reddit sentiment | T-1 (yesterday) | vw_media_daily_features |
| Discord mentions | Real-time (hourly) | discord_mentions |
| TA snapshots | Last 24h | ta_snapshots_v2 |
| GEX metrics | Last 24h | gex_metrics_snapshot (always empty) |
| UOA triggers | Last 60 min | uoa_triggers_v2 (stale since Jan 30) |
| News headlines | Last 7 days | articles |

---

## File Locations

| Component | Path |
|-----------|------|
| Database abstraction | `database.py` |
| FL3 pool factory | `market_data_service.py:create_fl3_pool()` |
| Market data fetcher | `market_data_service.py:MarketDataService` |
| Social data queries | `social_sentiment_llm.py:SocialDataFetcher` |
| Bot implementations | `{crypto,free,social}_*.py` |
| Schema (MBS only) | `schema.sql` |
