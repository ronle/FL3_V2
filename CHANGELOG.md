# Changelog

All notable changes to FL3_V2 paper trading system.

## [2026-02-06] - Pipeline Coverage Fix: SIP Feed, BucketAggregator, Symbol Tracking (v44)

### Critical Fixes
- **TA Pipeline `feed="sip"` missing** (`scripts/ta_pipeline_v2.py:178`): `get_bars_batch()` defaulted to IEX feed (~10% symbol coverage). Added `feed="sip"`. Result: **11 → 280 valid** symbols per 5-min cycle (25x improvement).
- **BucketAggregator wired into paper trading** (`paper_trading/main.py`): Component 3.5 existed but was never integrated. Now accumulates firehose trades into 30-min buckets and flushes to `intraday_baselines_30m` at bucket boundaries. Uses asyncpg pool. Prevents baseline expiration (was set to expire Feb 18 from Jan 29-30 data).
- **Symbol tracking for ALL triggered symbols** (`paper_trading/main.py:_check_for_signals`): Previously only tracked symbols that PASSED all filters (3-5/day). Now tracks every UOA-triggered symbol via `tracked_tickers_v2` upsert. Count grew from 290 → 294+ within first minute.

### Changes
- `scripts/ta_pipeline_v2.py`: Added `feed="sip"` to `get_bars_batch()` call
- `paper_trading/main.py`:
  - Added `import re`, `BucketAggregator` import
  - Added asyncpg pool creation in `run()` after `load_baselines()`
  - Added OCC symbol parsing + `bucket_aggregator.add_trade()` in `_process_trade()`
  - Added `tracked_tickers_v2` upsert for all triggered symbols in `_check_for_signals()`
  - Added bucket flush + pool close in `shutdown()`

### Deployed
- Image: `paper-trading:v44` → both `paper-trading-live` service AND `fl3-v2-ta-pipeline` job
- Revision: `paper-trading-live-00064-xs4`

---

## [2026-02-06] - Dynamic TA Alpaca Switch & Health Check Overhaul (v39–v43)

### Summary
Switched the live trading dynamic TA fetcher (`signal_filter.py`) from Polygon (5 req/min, frequently timing out) to Alpaca (200 req/min). Required three iterations (v41–v43) to get the full call chain correct. Also fixed a v38 crash that force-closed all positions, a `.gcloudignore` pattern that excluded production files, and a dashboard date display issue. Overhauled the pipeline health check from 18 to 23 tests.

### Critical Fixes

1. **v38 crash: all 5 positions force-closed (+$479)**
   - **Root cause**: Missing `PositionManager.update_dashboard_positions()` method. AttributeError on startup crashed the service, which triggered the shutdown handler that liquidated all open positions.
   - **Fix**: Added the missing method, wrapped dashboard calls in try/except, changed shutdown handler to only liquidate on graceful exit (not crash).
   - **Lesson**: Dashboard failures should never take down the trading engine.

2. **Scale-to-zero overnight** (infrastructure)
   - **Root cause**: No `min-instances` set on Cloud Run service. Service scaled to zero overnight, no scheduler ping to wake it.
   - **Fix**: `gcloud run services update paper-trading-live --min-instances=1`
   - **Prevention**: `min-instances=1` ensures always-on for WebSocket-based services.

3. **v39: `.gcloudignore` excluded production files**
   - **Root cause**: `polygon_*.py` pattern (no leading `/`) uses gitignore rules — matches files in ALL directories, including `adapters/polygon_bars.py`.
   - **Fix**: Prefix root-only patterns with `/` in `.gcloudignore`. Updated `.dockerignore` for consistency.
   - **Key lesson**: `.gcloudignore` = gitignore rules (matches anywhere). `.dockerignore` = Go filepath.Match (matches root only). Always use `/` prefix for root-only patterns in `.gcloudignore`.

4. **v40: Dashboard date/time display fix**
   - Fixed dashboard timestamp formatting in Google Sheets.

### Dynamic TA: Polygon → Alpaca (v41–v43)

The live signal filter fetches TA dynamically (via `fetch_ta_for_symbol()`) when a symbol isn't in the intraday cache. Previously used Polygon REST (5 req/min rate limit, 3s timeout = frequent failures). Switched to Alpaca (200 req/min, batched).

| Version | Change | Result |
|---------|--------|--------|
| v41 | Replaced Polygon `fetch_ta()` with `AlpacaBarsFetcher.get_bars()` | 1 bar — `feed` param not passed through `get_bars()` wrapper |
| v42 | Added `feed="sip"` passthrough in `get_bars()` | 1 bar — no `start` date, Alpaca returns only today's bar |
| v43 | Added `start=now-120days` (same pattern as `premarket_ta_cache.py:163`) | **82 bars** — working correctly |

**v43 fix** (`paper_trading/signal_filter.py:865-870`):
```python
from datetime import timezone
start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=120)
bar_data = await fetcher.get_bars(symbol, timeframe="1Day", limit=70, start=start_date, feed="sip")
```

**Lesson**: Always trace the FULL call chain E2E and compare against the working reference implementation (`premarket_ta_cache.py`) before building. Three deployments could have been one.

### Health Check Overhaul (23 tests)

Updated `tests/pipeline_health_check.py` from 18 → 23 tests to match actual live system state.

**New tests (5):**
| Test | ID | What it checks |
|------|----|----------------|
| `check_service_min_instances` | TEST-1.5 | `paper-trading-live` has `min-instances>=1` (prevents scale-to-zero) |
| `check_active_signals_today` | TEST-2.7 | `active_signals` table has entries today (replaced non-existent `signal_evaluations`) |
| `check_orats_daily` | TEST-2.8 | V1 dependency: `orats_daily.asof_date` is T-1 (data arrives after close) |
| `check_alpaca_bars_api` | TEST-5.4 | SIP feed returns >50 daily bars with same params as `fetch_ta_for_symbol()` |
| `check_position_sync` | TEST-5.5 | Alpaca positions match `paper_trades_log` open trades |

**Improved tests (4):**
| Test | Change |
|------|--------|
| `check_baselines` (TEST-2.3) | Added FAIL tier at >7 days (was WARN-only >3d) |
| `check_spot_prices` (TEST-2.6) | JOINs on `tracked_tickers_v2` with percentage-based thresholds (was checking all 15K V1 rows) |
| `check_ta_coverage` (TEST-3.2) | Percentage-based: ≤10% stale=PASS, 10-30%=WARN, >30%=FAIL (was absolute counts) |
| `EXPECTED_JOBS` | Added `premarket-orchestrator` to scheduler job checklist |

### Trades (v43 — 2026-02-06)
| Symbol | Shares | Entry | Score | RSI | Notes |
|--------|--------|-------|-------|-----|-------|
| TGT | — | $114.79 | 14 | — | First trade on v43 (dynamic TA not needed — in cache) |
| FLEX | 155 | $64.26 | 13 | 49.0 | First trade using dynamic Alpaca TA (82 bars fetched) |
| SYNA | — | $89.99 | 13 | — | |

### Deployed Image
`us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v43`

### Files Changed
| File | Changes |
|------|---------|
| `paper_trading/signal_filter.py` | Dynamic TA: Polygon→Alpaca, `start` + `feed` params |
| `adapters/alpaca_bars_batch.py` | `get_bars()` now passes `feed` and `start` through |
| `paper_trading/position_manager.py` | Added `update_dashboard_positions()` method |
| `.gcloudignore` | Root-only `/` prefix on all dev script patterns |
| `.dockerignore` | Synced with `.gcloudignore` patterns |
| `paper_trading/dashboard.py` | Timestamp display fix |
| `tests/pipeline_health_check.py` | 18→23 tests, 5 new + 4 improved |

---

## [2026-02-06] - Premarket TA Cache: Alpaca + Pagination Fix (v38c)

### Summary
Premarket TA cache switched from Polygon to Alpaca with pagination support. Fixed critical bug where Alpaca's multi-symbol API was only returning 4/305 symbols due to missing pagination. Symbol coverage now 294 (vs 82 before migration).

### Fixed
1. **Alpaca API pagination** (v38c)
   - **Root cause**: Alpaca's multi-symbol bars API paginates by total bar count across ALL symbols, not per-symbol. Requesting `limit=70` returned ~70 bars total (1 symbol), not 70 per symbol.
   - **Fix**: Rewrote `_fetch_bars_batch()` to follow `next_page_token` until all symbols have sufficient bars
   - Uses `limit=10000` per request (API max) to minimize round-trips
   - File: `adapters/alpaca_bars_batch.py`

2. **Date format for tz-aware datetimes** (v38)
   - Fixed RFC3339 format: only append "Z" for naive datetimes
   - Tz-aware datetimes already include offset (e.g., "-05:00")
   - File: `adapters/alpaca_bars_batch.py`

### Changed
1. **Alpaca bars instead of Polygon** (v38)
   - `premarket_ta_cache.py` now uses `AlpacaBarsFetcher` with `timeframe="1Day", limit=70`
   - Uses SIP feed for full market coverage
   - Env vars: `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` (replaces `POLYGON_API_KEY`)
   - File: `paper_trading/premarket_ta_cache.py`

2. **Dynamic symbol list from tracked_tickers_v2** (v38)
   - New `get_tracked_symbols()` queries `tracked_tickers_v2 WHERE ta_enabled = TRUE`
   - Merged with `DEFAULT_SYMBOLS` as safety net (union, not replace)
   - File: `paper_trading/premarket_ta_cache.py`

3. **Health check validates symbol coverage** (v38)
   - TEST-2.1 now compares `ta_daily_close` symbol count against `tracked_tickers_v2`
   - PASS: >= 80% of tracked count (floor: 82)
   - WARN: date is fresh but symbol count below expected
   - File: `tests/pipeline_health_check.py`

### Results
| Metric | Before (v38b) | After (v38c) |
|--------|---------------|--------------|
| Symbols with data | 4 | 294 |
| ta_daily_close rows | 82 | 294 |
| API pages fetched | 1 | 1 (optimized) |

### Deployed Image
`us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v38c`

### Docs Updated
- `FL3_ECOSYSTEM_BIBLE.md` — pipeline registry and version updated
- `CLAUDE.md` — Alpaca API pagination documented

---

## [2026-02-05] - Intraday TA Refresh (v37)

### Summary
Live trading now uses fresh 5-minute TA data from `ta_snapshots_v2` instead of stale prior-day data throughout the trading session.

### Data Sources (Updated Architecture)
| Data | Source | Type |
|------|--------|------|
| Options trades | Polygon WebSocket (T.*) | Real-time (Options Advanced plan) |
| TA data (RSI, SMA20) - open | `ta_daily_close` DB table | Prior day close |
| TA data (RSI, SMA20) - intraday | `ta_snapshots_v2` DB table | 5-minute refresh |
| TA data (SMA50) | `ta_daily_close` DB table | Prior day (50-day avg doesn't change intraday) |
| Stock prices | Alpaca REST API (snapshot) | Real-time (2s timeout) |

### Changed
1. **Intraday TA from ta_snapshots_v2** (v37)
   - Before 9:35 AM: Uses daily cache from `ta_daily_close` (prior day close)
   - After 9:35 AM: Uses fresh 5-min TA from `ta_snapshots_v2`
   - SMA50 always from daily cache (50-day average)
   - Intraday cache refreshes every 5 minutes
   - File: `paper_trading/signal_filter.py:SignalGenerator`

2. **Auto-track symbols on signal pass** (v37)
   - Symbols that pass filters are now added to `tracked_tickers_v2`
   - Ensures new symbols get 5-min TA updates from ta_pipeline
   - File: `paper_trading/signal_filter.py:track_symbol_for_ta()`

3. **Pipeline health check script** (v37)
   - Comprehensive test suite for V2 pipeline integrity
   - Covers: GCP jobs, data freshness, tracking, signal filtering, Alpaca integration
   - 18 automated checks across 5 categories
   - Run: `python -m tests.pipeline_health_check`
   - File: `tests/pipeline_health_check.py`
   - Docs: `tests/V2_PIPELINE_HEALTH_CHECK.md`

### Prerequisite
- `ta_pipeline_v2` job must be running during market hours
- Job writes to `ta_snapshots_v2` every 5 minutes
- Run: `python -m scripts.ta_pipeline_v2`

---

## [2026-02-05] - WebSocket Stability & Price Fix (v36 LOCKED)

### Summary
Fixed critical WebSocket stability issues causing service hangs during market hours. Root cause was blocking I/O operations (Polygon TA fetch, DB queries) exceeding WebSocket ping timeout (10s).

### Data Sources (Current Architecture)
| Data | Source | Type |
|------|--------|------|
| Options trades | Polygon WebSocket (T.*) | Real-time (Options Advanced plan) |
| TA data (RSI, SMA, MACD) | Polygon REST API (bars) | Historical daily bars, calculated in-process |
| Stock prices (positions) | Alpaca REST API (snapshot) | Real-time |
| Stock prices (signals) | Alpaca REST API (snapshot) | Real-time (2s timeout) |

### Fixed
1. **Timeout-protected TA fetch** (v36)
   - Added 3-second timeout on Polygon TA fetch using `asyncio.wait_for()`
   - Previously blocked 10-12s due to rate limiting, exceeding WebSocket ping timeout
   - Falls back to cached/default values on timeout
   - File: `paper_trading/signal_filter.py:create_signal_async()`

2. **Timeout-protected price fetch** (v36)
   - Added 2-second timeout on Alpaca price snapshot
   - Falls back to TA cache's `last_close` on timeout
   - File: `paper_trading/signal_filter.py:create_signal_async()`

3. **Pre-loaded DB caches** (v35)
   - Sector cache: 5898 symbols loaded at startup
   - Earnings cache: Symbols with earnings in ±2 days loaded at startup
   - Non-blocking DB writes via ThreadPoolExecutor
   - File: `paper_trading/signal_filter.py`

4. **Real-time price for signal evaluation** (v35)
   - Added `_fetch_current_price()` using Alpaca snapshot API
   - Signals now show actual prices (e.g., "170.56 < 179.24") instead of "0.00 < X"
   - File: `paper_trading/signal_filter.py`

5. **WebSocket max_connections handling** (v33)
   - Waits 60s before reconnecting on "max_connections exceeded" error
   - File: `firehose/client.py:151-160`

6. **Disabled 15-min delayed stock WebSocket** (v33)
   - `USE_STOCK_WEBSOCKET: False` in config
   - Polygon Stocks Starter plan = 15-min delayed
   - Now using Alpaca REST for real-time stock prices
   - File: `paper_trading/config.py:48`

### Deployment
- **Image**: `us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v36`
- **Service**: `paper-trading-live`
- **Region**: `us-west1`
- **Max instances**: 1 (single Polygon WebSocket connection)

### Trades Executed (2026-02-05)
- NICE: 87 shares @ $113.74
- PACS: 261 shares @ $37.73
- COR: 28 shares @ $353.48

---

## Technical Debt (as of 2026-02-05)

### High Priority - RESOLVED
1. **~~Aggregator always returns price=0~~** ✓ [PR #1](https://github.com/ronle/FL3_V2/pull/1)
   - Options trades don't contain stock prices, only option premiums
   - Fix: Aggregator returns `price: None`, signal_filter fetches from Alpaca

2. **~~TA JSON cache not shared between containers~~** ✓ [PR #2](https://github.com/ronle/FL3_V2/pull/2)
   - Fix: `load_ta_cache()` now reads from `ta_daily_close` database table

### Medium Priority - RESOLVED
3. **~~TA fetch timeout may skip filters~~** ✓ [PR #3](https://github.com/ronle/FL3_V2/pull/3)
   - Fix: `create_signal_async()` returns None if RSI/SMA missing

4. **~~No TA cache persistence across restarts~~** ✓ Fixed by [PR #2](https://github.com/ronle/FL3_V2/pull/2)
   - Database persists across container restarts

### Low Priority - RESOLVED
5. **~~Rate limiter in Polygon bars adapter~~** ✓ (v38)
   - Was: 5 requests/minute = 12s between requests via Polygon
   - Fix: Switched premarket-ta-cache to Alpaca batched fetcher (100 symbols/call, 200 req/min)

6. **Hardcoded ETF exclusion list**
   - ETFs like SPY, QQQ hardcoded in signal_filter.py
   - Fix: Move to config or database table

---

## [2026-02-04] - V28 Market Regime Filter

### Added
- Market regime filter using SPY price vs open
- Pauses new entries when SPY drops > 0.5% from open

### Trades
- WYNN: 87 shares @ $113.85
- ROST: 52 shares @ $189.84
- DGX: 52 shares @ $187.71
- SIRI: 476 shares @ $20.78

---

## [2026-02-03] - CLI Handoff V28

### Added
- Earnings filter (5.5): Rejects signals within 2 days of earnings
- Direction classifier (5.6): Uses call_pct to determine bullish/bearish
- Liquidity filter (5.7): Filters penny stocks and low volume tickers

### Fixed
- Various paper trading bugs from Jan 30 first live session

---

## [2026-01-30] - First Live Session

### Notes
- First live paper trading session
- Multiple bugs identified and documented for fixing

---

## Filter Chain Reference (as of v43)

The signal filter applies these **8 active** checks in `apply()`:

1. **ETF exclusion** - Hardcoded list (SPY, QQQ, IWM, etc.)
2. **Score threshold** - score >= 10
3. **Uptrend SMA20** - price > 20-day SMA (includes trend check)
4. **RSI filter** - RSI < 50 (oversold)
5. **SMA50 momentum** - price > 50-day SMA
6. **Notional baseline** - >= per-symbol baseline from `intraday_baselines_30m`
7. **Crowded trade + sentiment** - mentions < 5, sentiment >= 0 (from `vw_media_daily_features`)
8. **Earnings proximity** - no earnings within 2 days (from `earnings_calendar`)

**Configured but NOT called in `apply()` (as of v43):**
- Sector concentration (max 2 per sector) — code exists, not wired
- Market regime (SPY check) — code exists, not wired

---

## Database Tables (as of v43)

### Written by Live Trading
- `active_signals` - Signals that passed all filters
- `paper_trades_log` - Executed trades
- `tracked_tickers_v2` - Symbols added for intraday TA updates

### Read by Live Trading
- `ta_daily_close` - Prior-day TA (before 9:35 AM)
- `ta_snapshots_v2` - Intraday TA (after 9:35 AM, 5-min refresh)
- `vw_media_daily_features` - Crowded trade + sentiment filter
- `master_tickers` - Sector lookup (used in signal creation, not filtering)
- `earnings_calendar` - Earnings proximity filter
- `intraday_baselines_30m` - Per-symbol notional baselines
