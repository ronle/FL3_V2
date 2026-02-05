# Changelog

All notable changes to FL3_V2 paper trading system.

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

### Low Priority - OPEN
5. **Rate limiter in Polygon bars adapter**
   - 5 requests/minute = 12s between requests
   - Contributes to TA fetch delays
   - Fix: Batch requests or use paid tier with higher limits

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

## Filter Chain Reference

The signal filter applies these checks in order:

1. **ETF exclusion** - Hardcoded list (SPY, QQQ, IWM, etc.)
2. **Score threshold** - score >= 10
3. **RSI filter** - RSI < 50 (oversold)
4. **Uptrend SMA20** - price > 20-day SMA
5. **SMA50 momentum** - price > 50-day SMA
6. **Notional baseline** - >= per-symbol baseline from `intraday_baselines_30m`
7. **Crowded trade** - mentions < 5, sentiment >= 0 (from `vw_media_daily_features`)
8. **Sector limit** - max 2 per sector (from `master_tickers`)
9. **Market regime** - SPY not down > 0.5% from open
10. **Earnings proximity** - no earnings within 2 days (from `earnings_calendar`)

---

## Database Tables

### Written by Live Trading
- `signal_evaluations` - All evaluated signals with pass/fail reasons
- `active_signals` - Signals that passed all filters
- `paper_trades_log` - Executed trades

### Read by Live Trading
- `vw_media_daily_features` - Crowded trade + sentiment filter
- `master_tickers` - Sector concentration limit
- `earnings_calendar` - Earnings proximity filter
- `intraday_baselines_30m` - Per-symbol notional baselines
