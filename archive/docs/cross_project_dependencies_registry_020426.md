# Cross-Project Dependencies Registry
**Generated**: 2026-02-04

## Purpose

This registry formally documents every dependency between V1 (spartan-buckeye), V2 (fl3-v2-prod), and local processes. It is the authoritative reference for understanding what breaks when a pipeline fails.

---

## Dependency Summary

| Criticality | Count | Status |
|-------------|-------|--------|
| CRITICAL | 1 | BROKEN |
| HIGH | 2 | OK |
| MEDIUM | 2 | OK |
| LOW | 3 | OK |
| **Total** | **8** | 1 BROKEN |

---

## Formal Dependency Registry

### Dependency #1: V2 Paper Trading → V1 Sentiment Pipeline

| Property | Value |
|----------|-------|
| **ID** | DEP-001 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | V1 (`spartan-buckeye`) |
| **Pipeline/Table** | `sentiment_daily` table |
| **Consumer** | `paper-trading-live` service |
| **Filter Step** | Step 8: Crowded trade filter |
| **Criticality** | **CRITICAL** |
| **Status** | **BROKEN** |

**Dependency Chain**:
```
V1 News APIs (FMP, NewsData, FreeCrypto, Alpaca)
    ↓
V1 fr3-media-news-* jobs (every 5-10 min)
    ↓
articles table (627K rows)
    ↓
V1 fr-media-analyze job (every 15 min)
    ↓
article_sentiment table (420K rows)
    ↓
V1 fr-sentiment-agg job (daily 5:30 AM) ← FAILING
    ↓
sentiment_daily table (40K rows) ← 14 DAYS STALE
    ↓
V2 paper-trading-live (filter step 8)
```

**What Happens If Upstream Stops**:
- V2 paper trading uses stale sentiment data
- Crowded trade filter becomes ineffective
- May enter trades in overhyped symbols

**Fallback**:
- Could query `vw_media_daily_features` view instead (computes on-the-fly)
- Could disable crowded trade filter temporarily

**Last Verified Working**: 2026-01-21 (14 days ago)

**Resolution Required**:
1. Fix `fr-sentiment-agg` job, OR
2. Migrate V2 to use `vw_media_daily_features` view

---

### Dependency #2: V2 Paper Trading → V1 Master Tickers

| Property | Value |
|----------|-------|
| **ID** | DEP-002 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | V1 (`spartan-buckeye`) |
| **Pipeline/Table** | `master_tickers` table |
| **Consumer** | `paper-trading-live` service |
| **Filter Step** | Step 6: Sector concentration limit |
| **Criticality** | HIGH |
| **Status** | OK |

**Dependency Chain**:
```
V1 refresh-sector-data job (weekly)
    ↓
master_tickers table (5.9K rows)
    ↓
V2 paper-trading-live (filter step 6)
```

**What Happens If Upstream Stops**:
- Sector concentration filter uses stale sector assignments
- New tickers won't have sector data
- May over-concentrate in sectors

**Fallback**:
- V2 has `refresh-sector-data` job as backup
- Could fetch sector from Polygon/Alpaca on-demand

**Last Verified Working**: 2026-02-03 (1 day ago)

---

### Dependency #3: V2 Paper Trading → V2 Earnings Calendar

| Property | Value |
|----------|-------|
| **ID** | DEP-003 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | V2 (`fl3-v2-prod`) |
| **Pipeline/Table** | `earnings_calendar` table |
| **Consumer** | `paper-trading-live` service |
| **Filter Step** | Step 7: Earnings proximity filter |
| **Criticality** | HIGH |
| **Status** | OK |

**Dependency Chain**:
```
FMP API (earnings endpoint)
    ↓
V2 fetch-earnings-calendar job (daily 4 AM)
    ↓
earnings_calendar table (48K rows)
    ↓
V2 paper-trading-live (filter step 7)
```

**What Happens If Upstream Stops**:
- Earnings filter uses stale calendar
- May trade into earnings events (high risk)
- Expiry dates could be missed

**Fallback**:
- Query FMP API directly if table stale
- Could fetch from Alpaca calendar endpoint

**Last Verified Working**: 2026-02-04 (today)

---

### Dependency #4: V2 Paper Trading → V2 Spot Prices

| Property | Value |
|----------|-------|
| **ID** | DEP-004 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | V2 (`fl3-v2-prod`) |
| **Pipeline/Table** | `spot_prices` table |
| **Consumer** | `paper-trading-live` service |
| **Filter Step** | Step 9: Market regime (SPY check) |
| **Criticality** | MEDIUM |
| **Status** | OK |

**Dependency Chain**:
```
Alpaca API (latest quote)
    ↓
V2 update-spot-prices job (every 1 min RTH)
    ↓
spot_prices table (32K rows)
    ↓
V2 paper-trading-live (filter step 9)
```

**What Happens If Upstream Stops**:
- Market regime check uses stale SPY price
- Could trade during market weakness
- Less critical than other filters

**Fallback**:
- Fetch SPY price directly from Polygon WebSocket
- Could use Firehose equity trades for SPY

**Last Verified Working**: 2026-02-04 (today)

---

### Dependency #5: V2 Paper Trading → Polygon Firehose

| Property | Value |
|----------|-------|
| **ID** | DEP-005 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | Polygon.io (external) |
| **Pipeline/Table** | WebSocket T.* (options trades) |
| **Consumer** | `paper-trading-live` service |
| **Filter Step** | Primary data source |
| **Criticality** | **CRITICAL** |
| **Status** | OK |

**Dependency Chain**:
```
Polygon.io WebSocket (T.* - all options trades)
    ↓
V2 paper-trading-live (in-memory aggregation)
    ↓
TradeAggregator (60s rolling window)
    ↓
Scoring → Filters → Execution
```

**What Happens If Upstream Stops**:
- No options flow data
- System cannot detect UOA
- Paper trading completely blocked

**Fallback**:
- Auto-reconnect with exponential backoff
- Alert on prolonged disconnection

**Last Verified Working**: 2026-02-04 (continuous)

---

### Dependency #6: V2 Analysis → V1 ORATS Ingest

| Property | Value |
|----------|-------|
| **ID** | DEP-006 |
| **Dependent Project** | V2 (`fl3-v2-prod`) |
| **Depends On** | V1 (`spartan-buckeye`) |
| **Pipeline/Table** | `orats_daily` table |
| **Consumer** | Backtesting/analysis scripts only |
| **Criticality** | LOW |
| **Status** | OK |

**Dependency Chain**:
```
ORATS FTP (daily 10 PM PT)
    ↓
V1 orats-daily-ingest job
    ↓
orats_daily table (2.9M rows)
    ↓
V2 analysis scripts (NOT live trading)
```

**What Happens If Upstream Stops**:
- Backtesting uses stale options data
- Historical analysis affected
- **Live trading NOT affected**

**Fallback**:
- None needed for live trading
- Could fetch ORATS via REST API for analysis

**Last Verified Working**: 2026-02-04 (nightly)

**IMPORTANT**: This is NOT a live trading dependency. V2 live trading does NOT query `orats_daily`.

---

### Dependency #7: V1 Media Pipeline → V1 News APIs

| Property | Value |
|----------|-------|
| **ID** | DEP-007 |
| **Dependent Project** | V1 (`spartan-buckeye`) |
| **Depends On** | External APIs (FMP, NewsData, FreeCrypto, Alpaca) |
| **Pipeline/Table** | `articles` table |
| **Consumer** | `fr-media-analyze` job |
| **Criticality** | MEDIUM |
| **Status** | OK (except Reddit) |

**Dependency Chain**:
```
FMP News API (every 10 min)
NewsData.io API (every 10 min)
FreeCryptoNews API (every 5 min)
Alpaca News API (periodic)
    ↓
V1 fr3-media-news-* jobs
    ↓
articles table (627K rows)
    ↓
V1 fr-media-analyze job
    ↓
article_sentiment table
```

**What Happens If Upstream Stops**:
- Fewer articles for sentiment analysis
- Sentiment data becomes less comprehensive
- Crowded trade detection less reliable

**Fallback**:
- Multiple news sources provide redundancy
- Any single source can fail without total outage

**Last Verified Working**: 2026-02-04 (continuous)

**Note**: Reddit API is BROKEN since 2026-01-31

---

### Dependency #8: Local Discord → Database

| Property | Value |
|----------|-------|
| **ID** | DEP-008 |
| **Dependent Project** | Local process |
| **Depends On** | Discord API |
| **Pipeline/Table** | `discord_mentions` table |
| **Consumer** | Analysis (not live trading) |
| **Criticality** | LOW |
| **Status** | OK |

**Dependency Chain**:
```
Discord API (real-time)
    ↓
Local Discord reader (continuous)
    ↓
discord_mentions table (942 rows)
    ↓
Manual analysis
```

**What Happens If Upstream Stops**:
- No Discord mention tracking
- Social sentiment incomplete
- Does not affect live trading

**Fallback**:
- None needed for trading
- Could use archived data for analysis

**Last Verified Working**: 2026-02-04 (when running locally)

---

## Critical Path Diagram

```
                    ┌─────────────────────────────────────────────┐
                    │              EXTERNAL APIS                   │
                    │  Polygon  FMP  NewsData  FreeCrypto  Alpaca │
                    └─────────────────────────────────────────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
           ┌───────────────┐    ┌───────────────┐    ┌───────────────┐
           │ Polygon.io    │    │ V1 News Jobs  │    │ V2 Jobs       │
           │ WebSocket T.* │    │ (4 sources)   │    │ (earnings,    │
           │ CRITICAL      │    │               │    │  spot, TA)    │
           └───────┬───────┘    └───────┬───────┘    └───────┬───────┘
                   │                    │                    │
                   │                    ▼                    │
                   │            ┌───────────────┐            │
                   │            │   articles    │            │
                   │            └───────┬───────┘            │
                   │                    │                    │
                   │                    ▼                    │
                   │            ┌───────────────┐            │
                   │            │ fr-media-     │            │
                   │            │ analyze       │            │
                   │            └───────┬───────┘            │
                   │                    │                    │
                   │                    ▼                    │
                   │            ┌───────────────┐            │
                   │            │ article_      │            │
                   │            │ sentiment     │            │
                   │            └───────┬───────┘            │
                   │                    │                    │
                   │                    ▼                    │
                   │            ┌───────────────┐            │
                   │            │ fr-sentiment- │            │
                   │            │ agg (FAILING) │ ← ⚠️ BROKEN
                   │            └───────┬───────┘            │
                   │                    │                    │
                   │                    ▼                    │
                   │            ┌───────────────┐            │
                   │            │ sentiment_    │            │
                   │            │ daily (STALE) │ ← ⚠️ 14 DAYS
                   │            └───────┬───────┘            │
                   │                    │                    │
                   │      ┌─────────────┼─────────────┐      │
                   │      │             │             │      │
                   │      ▼             ▼             ▼      ▼
                   │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
                   │  │master_  │ │earnings_│ │spot_    │ │(TA from │
                   │  │tickers  │ │calendar │ │prices   │ │Polygon) │
                   │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘
                   │       │           │           │           │
                   └───────┼───────────┼───────────┼───────────┘
                           │           │           │
                           ▼           ▼           ▼
                    ┌─────────────────────────────────────────┐
                    │         V2 paper-trading-live           │
                    │                                         │
                    │  Filter Pipeline:                       │
                    │  1. Firehose → UOA Detection           │
                    │  2-5. TA Filters (from Polygon API)    │
                    │  6. Sector Limit (master_tickers)      │
                    │  7. Earnings Proximity (earnings_cal)  │
                    │  8. Crowded Trade (sentiment_daily) ⚠️  │
                    │  9. Market Regime (spot_prices)        │
                    │                                         │
                    │  → active_signals                       │
                    │  → paper_trades_log                     │
                    │  → signal_evaluations                   │
                    └─────────────────────────────────────────┘
```

---

## Dependency Health Dashboard

| ID | Dependency | Upstream | Last Check | Status |
|----|------------|----------|------------|--------|
| DEP-001 | Sentiment Pipeline | `fr-sentiment-agg` | 2026-02-04 | BROKEN |
| DEP-002 | Master Tickers | `refresh-sector-data` | 2026-02-04 | OK |
| DEP-003 | Earnings Calendar | `fetch-earnings-calendar` | 2026-02-04 | OK |
| DEP-004 | Spot Prices | `update-spot-prices` | 2026-02-04 | OK |
| DEP-005 | Polygon Firehose | Polygon WebSocket | 2026-02-04 | OK |
| DEP-006 | ORATS Daily | `orats-daily-ingest` | 2026-02-04 | OK |
| DEP-007 | News APIs | 4 news jobs | 2026-02-04 | PARTIAL |
| DEP-008 | Discord | Local reader | 2026-02-04 | OK |

---

## Recovery Procedures

### If DEP-001 (Sentiment) Fails

1. **Immediate**: V2 trading continues with stale data
2. **Short-term**:
   ```sql
   -- Check if view is viable alternative
   SELECT symbol, sum_mentions, avg_sentiment
   FROM vw_media_daily_features
   WHERE trade_date = CURRENT_DATE - 1
   LIMIT 10;
   ```
3. **Resolution**: Fix `fr-sentiment-agg` job OR migrate to view

### If DEP-005 (Polygon Firehose) Fails

1. **Immediate**: Trading halted
2. **Auto-recovery**: Reconnect with exponential backoff
3. **Manual**: Check Polygon status page, verify API key

### If Multiple Dependencies Fail

1. Halt trading if any CRITICAL dependency fails
2. Continue with degraded filters if HIGH/MEDIUM fails
3. Log warnings but proceed if LOW dependency fails

---

## Monitoring Recommendations

### Daily Checks
- [ ] Verify `sentiment_daily` has data from yesterday
- [ ] Verify `earnings_calendar` has upcoming earnings
- [ ] Verify `spot_prices` has data from current session
- [ ] Verify Polygon WebSocket connected

### Weekly Checks
- [ ] Verify `master_tickers` sector assignments current
- [ ] Review `orats_daily` data freshness
- [ ] Check for failed jobs in V1 and V2

### Alerting Rules
| Condition | Action |
|-----------|--------|
| `sentiment_daily` > 2 days stale | ALERT |
| `earnings_calendar` > 1 day stale | ALERT |
| Polygon disconnected > 5 min | ALERT |
| Any scheduled job fails | NOTIFY |

---

## Version History

| Date | Change |
|------|--------|
| 2026-02-04 | Initial registry created |
