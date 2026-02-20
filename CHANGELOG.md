# Changelog

All notable changes to FL3_V2 paper trading system.

## [2026-02-20 11:10 PST] — v54/v54a/v54b/v54c/v54d: Race Condition Fix + Dashboard Formatting

### Done
- **Bulletproof race condition fix (v54→v54a):** WebSocket hard stop firing multiple trade messages caused concurrent `asyncio.create_task` calls closing the same position twice
  - Layer 1: `_closing_symbols` / `_closing_symbols_b` debounce sets in `main.py` — prevents duplicate task creation
  - Layer 2: `_closing_in_progress` guard in `PositionManager.close_position()` — protects ALL callers (WS + REST polling)
  - Layer 3: `active_trades.pop(symbol, None)` — safe pop prevents KeyError on second close attempt
- **Dashboard Positions formatting (v54a→v54b):** Switched `rewrite_positions()` from `value_input_option='RAW'` to `'USER_ENTERED'` for consistent formatting. Removed `+` from P/L format specifier (`+2.38%` was parsed as formula by Google Sheets → stored as `0.0238`)
- **Dashboard Closed tab formatting (v54c):** Switched `close_position()` from `'RAW'` to `'USER_ENTERED'`. Removed `+` from P/L% and $P/L format specifiers (`$-149.80` → `-$149.80`)
- **GEX cache Cloud SQL fix (v54d):** `SignalGenerator.__init__` wasn't calling `.strip()` on DATABASE_URL — trailing `\r` from Secret Manager corrupted Cloud SQL socket path. GEX cache now loads 6,797 symbols successfully.
- **Backfilled** DOW and ARDX to Account B Closed tab (hard stop race condition had prevented DB/gsheet writes)

### Deployment
- **v54d** (revision `paper-trading-live-00098-twj`): All fixes live. GEX cache healthy, WebSocket connected, dashboards consistent.

### State
- All dashboard writes now use `USER_ENTERED` mode for consistent formatting
- Three-layer race condition defense active
- GEX cache loading successfully (6,797 symbols)

### Files Changed
- `paper_trading/main.py` — debounce sets for hard stop close tasks
- `paper_trading/position_manager.py` — `_closing_in_progress` guard, safe pop, P/L format fix
- `paper_trading/dashboard.py` — `USER_ENTERED` mode for Positions and Closed tabs, format fixes
- `paper_trading/signal_filter.py` — `.strip()` on DATABASE_URL in SignalGenerator

---

## [2026-02-19 14:59 PST] — v53a: Alpaca SIP WebSocket for Real-Time Hard Stops

### Done
- Rewrote `firehose/stock_price_monitor.py` from Polygon stocks WS (15-min delayed) to Alpaca SIP WS (real-time)
  - URL: `wss://socket.polygon.io/stocks` → `wss://stream.data.alpaca.markets/v2/sip`
  - Auth: Polygon API key → Alpaca key+secret pair
  - Subscriptions: channel-prefix format (`T.AAPL`) → array format (`{"trades":["AAPL"]}`)
  - Message parsing: `ev` field → `T` field, `sym` → `S`, unix-ms timestamps → RFC-3339 parsing
  - Added `_parse_timestamp()` for RFC-3339 → unix ms conversion
  - Added handling for `subscription` confirmation and `error` messages
- Enabled `USE_STOCK_WEBSOCKET = True` in config.py
- Updated `main.py` constructor to pass Alpaca creds instead of Polygon key
- Fixed standalone test at bottom of `main.py` (was still using `StockPriceMonitor(polygon_key)`)
- Made `start()` always launch retry loop — initial connection failure is no longer permanent
- Removed premature `_websocket_enabled = False` on initial failure (retry loop handles it)
- Updated `scripts/test_stock_websocket.py` with dynamic subscribe/unsubscribe test
- Local test passed: 33 trades, 4 quotes, dynamic sub/unsub all working (after hours)

### Deployment
- **v53** (revision 00092-v2v): Auth failed with `{"T":"error","code":500}` — concurrent connection conflict during revision handoff (old revision still alive)
- **v53a** (revision 00093-tlf): Connected + authenticated + real-time prices enabled. Fix: `start()` now always launches retry loop so transient failures self-heal

### State
- Alpaca SIP WebSocket connected and healthy on prod (revision `paper-trading-live-00093-tlf`)
- Hard stops now event-driven (sub-second) via WebSocket, with 30s REST polling as backup safety net
- Image: `paper-trading:v53a`

### Next
- Monitor during next market session to confirm trades/quotes flow and hard stops fire via WS path
- Consider subscribing Account B positions to the same WebSocket monitor

### Files Changed
- `firehose/stock_price_monitor.py` — full rewrite (Polygon → Alpaca SIP)
- `paper_trading/main.py` — Alpaca creds to monitor, removed permanent WS disable on failure
- `paper_trading/config.py` — `USE_STOCK_WEBSOCKET = True`, updated comments
- `scripts/test_stock_websocket.py` — rewritten for Alpaca + dynamic sub/unsub test

## [2026-02-19 14:15 PST] — Account B 3-Year Backtest Validation

### Done
- Backfilled `orats_daily_returns` for 2023 (1,460,104 rows computed from stock_price via LEAD() window function)
- Ran close-to-close backtest (r_p1) across 11 scenarios — all profitable, best Sharpe 6.89 (score >= 0.65)
- Ran intraday backtest with 1-min Polygon bars on engulfing-only signals — catastrophic (-$273K, 4% WR), proving engulfing alone is not tradable without UOA timing
- Replayed full UOA pipeline on 770 options files (Jan 2023 - Jan 2026, ~4.5 hours) → 2.3M signals, 9,746 score >= 10
- Ran 3-year realistic Account B sim (UOA + engulfing + 1-min bars): 559 trades, +17.1%, Sharpe 2.26, PF 1.47, max DD 2.5%

### Key Findings
- Close-to-close returns are misleading for intraday strategies (overnight gap + invisible stop-outs)
- Engulfing alone at market open = catastrophic (34.7% hard stop rate, 4% WR)
- UOA + Engulfing + intraday sim = consistent edge across all 3 years (50.1% WR, +0.29%/trade avg)
- Strategy degrades gracefully — worst month was -$1,395 (Sep 2023), no blowups

### State
- 3-year backtest complete and validated
- Live Account B architecture confirmed correct

### Next
- Consider replaying with different score thresholds (score >= 8) for more volume
- Investigate 2023 weakness vs 2025 strength — market regime or data quality?
- Update ACCOUNT_B_BACKTEST_STATUS.md with full 3-year results

### Files Changed
- `scripts/backfill_returns_2023.py` (new — backfill 2023 forward returns)
- `scripts/backtest_account_b_historical.py` (new — close-to-close multi-scenario backtest)
- `scripts/backtest_account_b_intraday.py` (new — intraday 1-min bar backtest)
- `scripts/backtest_account_b_sim.py` (modified — SIGNAL_FILE env var override)
- `archive/scripts/e2e_backtest_v2.py` (modified — BACKTEST_OUTPUT_DIR env var)
- `D:\backtest_results\e2e_backtest_v2_strikes_sweeps_price_scored.json` (new — 1.6GB, 3-year signals)
- `backtest_results/account_b_trades.csv` (updated — 559 trades, 3-year)
- `backtest_results/account_b_equity.csv` (updated — 3-year equity curve)
- `backtest_results/account_b_historical_hardstop.json` (new — close-to-close results)
- `backtest_results/account_b_intraday_score0.65.json` (new — intraday engulfing-only results)

---

## [2026-02-19 12:05 PST] — v53b: Dashboard P/L formatting fix

### Done
- **Root cause:** Google Sheets `clear()` clears cell values but NOT formatting. When `rewrite_positions` writes `"+8.78%"` with `USER_ENTERED` mode, Sheets interprets it as numeric 0.0878. If the cell retains "Number" format from a prior write, it displays `0.0878` instead of `8.78%`.
- **Fix (dashboard.py):** Switched from `USER_ENTERED` to `RAW` value_input_option for all data writes in `rewrite_positions`, `update_position`, and `close_position`. Headers and signals left as `USER_ENTERED` (no percentage values).
- **Deploy:** Revision `paper-trading-live-00091-snb`, image `paper-trading:v53b-sheet-format`
- **Verified:** Startup clean — Account A: 10 positions/0 orphans, Account B: 10 positions/0 orphans, zero errors

### State
- Service healthy on revision 00091-snb
- EOD close at 3:55 PM ET (~50 min)

### Next
- Monitor today's EOD close to verify both accounts liquidate properly

### Files Changed
- `paper_trading/dashboard.py` — RAW mode for P/L sheet writes

---

## [2026-02-19 11:48 PST] — v53: Orphan position cleanup + EOD closer fix

### Done
- **Root cause:** Account B had 25 positions on Alpaca (15 orphaned from missed EOD closes + 10 today). The EOD closer's `should_close()` required `now < MARKET_CLOSE` (4 PM ET), so if the service started after 4 PM, the close window was permanently missed. Orphaned Alpaca positions accumulated across days while the in-memory `active_trades` dict had no idea they existed.
- **Fix 1 (eod_closer.py):** Removed `now < MARKET_CLOSE` constraint from `should_close()`. Now triggers at EXIT_TIME (3:55 PM ET) or any time after, until `_closed_today` is set. Prevents missed windows.
- **Fix 2 (position_manager.py):** `sync_on_startup()` Case C (Alpaca-only positions with no DB record) now **closes orphaned positions** on Alpaca instead of adopting them. Logs each as `orphan_cleanup` in DB.
- **Deploy:** Revision `paper-trading-live-00090-hmz`, image `paper-trading:v53-orphan-fix`
- **Result:** Account B sync closed 15 orphaned positions (OMER, CACC, MDGL, TDW, WH, RDY, SDY, BITI, RELY, IJH, LCII, OXY, TRMD, FAS, UUP). Account A had 0 orphans. Both accounts now at 10 positions each.

### State
- Service healthy on revision 00091-snb (updated to v53b)

### Next
- Monitor EOD close

### Files Changed
- `paper_trading/eod_closer.py` — expanded `should_close()` window
- `paper_trading/position_manager.py` — orphan cleanup in `sync_on_startup()`

---

## [2026-02-18 13:10 PST] — Account B dashboard headers fix

### Done
- Inserted missing header rows into Account B Google Sheets tabs (Signals + Closed)
- Positions tab already had headers (self-healing via `rewrite_positions()`)
- One-off script: `temp/fix_account_b_headers.py`

### State
- All three Account B tabs now have correct headers
- Headers will auto-refresh tomorrow via `clear_daily()` at market open

### Next
- Monitor Account B EOD close (~3:55 PM ET)

### Files Changed
- `temp/fix_account_b_headers.py` (new, one-off)

---

## [2026-02-18] - Account B Cloud Run Deploy + Critical Fixes (v52)

### Summary
First successful Cloud Run deployment of Account B with engulfing-primary architecture. Discovered and fixed three critical bugs that had been silently breaking Account B on Cloud Run since the v51c deploy on Feb 12.

### Critical Fixes

1. **`DATABASE_URL` secret trailing `\r\r`** — The GCP Secret Manager value for `DATABASE_URL` had two carriage return characters appended, corrupting the Cloud SQL Unix socket path for any code using `psycopg2.connect()` without `.strip()`. The main app (asyncpg) was unaffected. **Every engulfing check was failing** on Cloud Run — Account B could never pass its primary gate.
   - **Fix**: Added `.strip()` in `EngulfingChecker.__init__()` to sanitize the URL
   - **File**: `paper_trading/engulfing_checker.py`

2. **`log_signal()` format error** — `engulfing_strength` is a string (`"strong"`, `"moderate"`, `"weak"`) from the DB, but dashboard code tried to format it as a float (`f"{engulfing_strength:.2f}"`). Every signal log silently failed with `Unknown format code 'f' for object of type 'str'`.
   - **Fix**: Changed to `str(engulfing_strength)`
   - **File**: `paper_trading/dashboard.py:163`

3. **Cloud Run traffic pinned to old revision** — Traffic was hardcoded to revision `paper-trading-live-00084-cwr` by name instead of using `latestRevision: true`. New revisions were created but immediately retired because they'd never receive traffic.
   - **Fix**: `gcloud run services update-traffic paper-trading-live --to-latest`
   - Also fixed Cloud SQL annotation that had `\r` in instance name via `--set-cloudsql-instances`

### Deployment
- **Revision**: `paper-trading-live-00089-d86`
- **Image**: `us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/fl3-v2:v4b-signal-fix`
- **Account A**: 10 positions restored via 3-way sync, healthy
- **Account B**: Initialized with $100K, engulfing watchlist loaded (9,208 symbols), 10 trades executed on first cycle
- **Dashboard tabs created**: "Account B Signals", "Account B Positions", "Account B Closed"

### Account B First Trades (2026-02-18)
| Symbol | Score | Engulfing | Vol Ratio | Entry |
|--------|-------|-----------|-----------|-------|
| MOD | 13 | strong | 0.5x | $219.33 |
| DHT | 11 | moderate | 8.1x | $16.72 |
| AMAT | 11 | strong | 0.8x | $368.87 |
| APA | 12 | moderate | 0.7x | $28.75 |
| MHK | 15 | weak | 0.4x | $132.54 |
| YEXT | 11 | moderate | 0.0x | $5.55 |
| STOK | 13 | weak | 0.3x | $31.21 |
| GKOS | 11 | moderate | 1.5x | $119.37 |
| TW | 13 | moderate | 0.0x | $116.68 |
| HTGC | 10 | moderate | 2.9x | $15.94 |

### New Files
- **`scripts/_backfill_signals.py`**: One-off script to backfill 10 missing signals from trade logs to "Account B Signals" Google Sheet tab (signals were lost due to format bug on revision 00088)

### Modified Files
- **`paper_trading/engulfing_checker.py`**: `.strip()` on `database_url` in `__init__`
- **`paper_trading/dashboard.py`**: `str(engulfing_strength)` instead of `f"{engulfing_strength:.2f}"` in `log_signal()`

### Lessons Learned
- **Always `.strip()` env vars** used for DB connections — secrets can have trailing whitespace/CR from copy-paste or Windows line endings
- **`gcloud run deploy` does NOT auto-route traffic** if traffic was previously pinned to a specific revision name. Must use `--to-latest` or `update-traffic` to fix
- **Test format strings with actual DB values** — engulfing_strength is a text enum in the DB, not a numeric score

---

## [2026-02-12] - Account B: Engulfing-Primary Architecture Flip (v51c)

### Summary
Flipped Account B architecture: engulfing pattern is now the **primary gate**, V2 score >= 10 is confirmation. Account B evaluates at the aggregator level (before signal creation/filter chain), so it no longer requires TA fetch, RSI, sentiment, or earnings filters. Daily engulfing patterns (timeframe='1D') are bulk-loaded into a watchlist at startup and daily reset for O(1) lookups; 5-min patterns remain as fallback.

Account B is **no longer a strict subset** of Account A — a symbol may fail Account A's RSI filter but still trade in Account B.

### Modified Files
- **`paper_trading/engulfing_checker.py`**: Added `_daily_watchlist` dict, `load_daily_watchlist()` method (bulk-loads timeframe='1D' patterns from last 20h). `has_engulfing_confirmation()` now checks daily watchlist first (O(1)), falls back to per-query 5-min check. Added `timeframe = '5min'` filter to fallback query.
- **`paper_trading/config.py`**: Added `ENGULFING_DAILY_LOOKBACK_HOURS: int = 20`
- **`paper_trading/main.py`**: Account B check moved BEFORE `create_signal_async()` / `signal_filter.apply()`. Uses only `stats["score"] >= SCORE_THRESHOLD` + engulfing check. Added `load_daily_watchlist()` at startup (after Account B sync) and daily reset.
- **`CLAUDE.md`**: Updated Account B section to reflect new architecture

### Architecture Change
```
OLD: UOA trigger → TA fetch → 10-filter chain → Account A trade → engulfing check → Account B trade
NEW: UOA trigger → score >= 10? → engulfing check → Account B trade (independent of Account A)
                 → TA fetch → 10-filter chain → Account A trade (unchanged)
```

---

## [2026-02-12] - Account B: V2 + Engulfing Pattern A/B Test (v51)

### Summary
Added a second parallel Alpaca paper trading account (Account B) that only trades signals with a confirming bullish engulfing candlestick pattern on the 5-minute chart. Account A behavior is completely unchanged. This enables an A/B comparison: Account A = all V2 signals, Account B = V2 signals + engulfing confirmation.

### v51b Update — Spec Alignment
Updated Account B implementation to match revised spec:
- **Timeframe changed**: Daily engulfing (20h lookback) → 5-minute patterns (30min lookback)
- **Score threshold removed**: Presence-only check (score column reserved for future use)
- **Architecture changed**: Cache-based (bulk-load) → per-query (Option A) — simpler, runs only 5-20 times/day
- **Table whitelist**: Added `ALLOWED_TABLES` safety check in `dashboard.py` for dynamic SQL
- **Schema updated**: `target_1`/`target_2` columns, `score` nullable, new index

### New Files
- **`paper_trading/engulfing_checker.py`**: Per-query checker for `engulfing_scores` table. Simple `has_engulfing_confirmation(symbol, lookback_minutes)` function — one indexed query per signal, no caching.
- **`sql/create_engulfing_scores.sql`**: DDL for shared scores table (written by DayTrading 5-min scanner, read by V2). Includes `target_1`, `target_2`, nullable `score`.
- **`sql/create_paper_trades_b.sql`**: DDL for Account B trade log (cloned from `paper_trades_log`)

### Modified Files
- **`paper_trading/config.py`**: Added `USE_ACCOUNT_B` (True), `ENGULFING_LOOKBACK_MINUTES` (30)
- **`paper_trading/dashboard.py`**: Added `ALLOWED_TABLES` whitelist + assertions in `log_trade_open()`, `log_trade_close()`, `load_open_trades_from_db()`. Added `table_name` parameter (backward-compatible default).
- **`paper_trading/position_manager.py`**: Added `trades_table` and `skip_dashboard` constructor parameters. Dashboard/signal DB calls gated behind `skip_dashboard` flag. `update_dashboard_positions()` returns early when `skip_dashboard=True`.
- **`paper_trading/main.py`**: Full Account B lifecycle — init (separate AlpacaTrader, PositionManager, EODCloser, EngulfingChecker), signal routing (after Account A trade, check engulfing confirmation), hard stop monitoring, EOD close, daily reset, shutdown cleanup.
- **`CLAUDE.md`**: Updated "Account B — V2 + Engulfing Pattern" section

### DDL
- `engulfing_scores` table with `UNIQUE(symbol, pattern_date, timeframe)`, index on `(symbol, direction, scan_ts)`
- `paper_trades_log_b` table (clone of `paper_trades_log INCLUDING ALL`)

### Environment Variables (New)
| Variable | Purpose |
|----------|---------|
| `ALPACA_API_KEY_B` | Account B Alpaca API key |
| `ALPACA_SECRET_KEY_B` | Account B Alpaca secret key |

### Architecture
- Account B is a strict subset of Account A — every B trade is also an A trade
- Independent position limits, independent Alpaca accounts, independent trade logs
- No Google Sheets dashboard for Account B (DB logging only via `paper_trades_log_b`)
- `engulfing_scores` table written by separate DayTrading 5-min scanner (every 5 min during market hours)
- 30-minute lookback = last 6 scan cycles must overlap with UOA trigger

### Rollback
Set `USE_ACCOUNT_B = False` in config.py, redeploy. Account A continues unaffected.

---

## [2026-02-11] - Intraday 1-Min Bar Collection + Full Market Backfill (v50/v50a/v50b/v50c)

### Summary
New `spot_prices_1m` table stores full intraday 1-min OHLCV bars for all tracked symbols (~1,800), collected every 60 seconds by `IntradayBarCollector` running inside `paper-trading-live`. Replaces the lossy `spot_prices` table which only kept the last write per day due to `UNIQUE(ticker, trade_date)` UPSERT. Compatibility view `vw_spot_prices_latest` provides drop-in replacement for downstream consumers. Historical backfill loaded 7 trading days (~7M bars).

### New Files
- **`paper_trading/bar_collector.py`**: `IntradayBarCollector` class — fetches latest 1-min bars via Alpaca `/v2/stocks/bars/latest` (SIP feed), buffers in memory, flushes to DB via asyncpg UPSERT. 1,800+ symbols (20 batches of 100) per cycle. Includes 7-day retention cleanup.
- **`scripts/backfill_bars_1m.py`**: One-off historical backfill script — fetches N trading days of 1-min bars via Alpaca `/v2/stocks/bars` (historical endpoint with pagination). Batches 100 symbols per API request, 5,000 rows per DB insert. Ran as dedicated Cloud Run Job.

### Modified Files
- **`paper_trading/config.py`**: Added `COLLECT_INTRADAY_BARS`, `INTRADAY_BARS_MAX_BATCHES` (20 — full market), `INTRADAY_BARS_INTERVAL_SEC` (60), `INTRADAY_BARS_RETENTION_DAYS` (7)
- **`paper_trading/main.py`**: Wired `IntradayBarCollector` — init after BucketAggregator, periodic collect in main loop, retention on daily reset, flush+close on shutdown

### DDL
- `spot_prices_1m` table with `UNIQUE(symbol, bar_ts)`, indexes on `(symbol, bar_ts)` and `(bar_ts)`
- `vw_spot_prices_latest` view — maps `spot_prices_1m` to old `spot_prices` schema (`ticker`, `trade_date`, `underlying`, etc.)

### Version History
| Version | Change | Result |
|---------|--------|--------|
| v50 | Initial deploy with SIP feed | 403 — free plan blocks real-time SIP on `/bars/latest` |
| v50a | Switched to IEX feed | Working — 200 bars/cycle |
| v50b | Switched back to SIP feed | Working — user upgraded Alpaca plan, SIP now allowed |
| v50c | Scaled `MAX_BATCHES` from 2 → 20 | Full market — 1,785 bars/cycle for all tracked symbols |

### Historical Backfill (v50c-backfill)
- **Dedicated Cloud Run Job**: `backfill-bars-1m` (created, executed, deleted after completion)
- **Results**: 6,949,208 bars fetched and inserted across 1,804 symbols, 9 full trading days (Jan 29 – Feb 11)
- **DB totals**: 6,966,680 rows (includes live-collected bars), ~700K bars per full trading day
- **Runtime**: ~11 minutes (19 batches of 100 symbols each)
- **Lesson**: Don't repurpose existing jobs (e.g., `orats-daily-ingest`) for one-off tasks — create dedicated jobs and delete after use.

### API Budget (updated)
- 20 requests/min added (20 batches × 1 req/batch), total ~32.6 of unlimited (upgraded plan)

### Deployed
- Image: `paper-trading:v50c`
- Revision: `paper-trading-live-00077-xwt`
- Verified: 1,785 bars/cycle flowing, 7M+ rows in `spot_prices_1m`

### Future Migration Steps
1. ~~Scale `INTRADAY_BARS_MAX_BATCHES` to cover all tracked symbols~~ DONE (v50c)
2. Verify `vw_spot_prices_latest` covers all symbols in `spot_prices`
3. Update MBS Arena code to read `vw_spot_prices_latest`
4. Disable `update-spot-prices` Cloud Scheduler trigger
5. Delete `update-spot-prices` job after 1 week

---

## [2026-02-10] - Score Column + Hard Stop Tightened (v49)

### Summary
Added `Score` column to both the "Positions" and "Closed Today" Google Sheets tabs so signal strength is visible at a glance. Backfilled all 25 historical closed trades with scores recovered from `active_signals` for crash-recovery entries. Tightened hard stop from -5% to -2% to dump losers faster and free slots for winners.

### Hard Stop Change
- **`paper_trading/config.py`**: `HARD_STOP_PCT` changed from `-0.05` (-5%) to `-0.02` (-2%)
- **Rationale**: Feb 10 saw 7/10 trades lose (day total -$561), with biggest losers at -3.1% and -2.6% bleeding out to EOD. Tighter stop frees position slots for new winners.

### Modified Files
- **`paper_trading/dashboard.py`**:
  - `update_position()` — Added `score: int` parameter, inserted at column B. Update range changed from `A-E` to `A-F`.
  - `close_position()` — Added `score: int` parameter, inserted at column C.
  - `clear_daily()` — Updated headers for both tabs:
    - Positions: `Symbol | Score | Entry | Current | P/L % | Status`
    - Closed Today: `Date/Time | Symbol | Score | Shares | Entry | Exit | P/L % | $ P/L | Result`
- **`paper_trading/position_manager.py`**:
  - `open_position()` — Passes `score=trade.signal_score` to `dashboard.update_position()`
  - `close_position()` — Passes `score=trade.signal_score` to `dashboard.close_position()`
  - `update_dashboard_positions()` — Passes `score=trade.signal_score` to `dashboard.update_position()`

### Modified Files (Backfill)
- **`scripts/backfill_closed_sheet.py`**:
  - Query now uses `LEFT JOIN LATERAL active_signals` with `COALESCE(NULLIF(p.signal_score, 0), a.score, 0)` to recover scores for crash-recovery trades
  - Header and rows updated to 9 columns (A-I)

### Backfill Results
- 25 closed trades written with scores
- 5 crash-recovery trades (Feb 6) recovered from `active_signals`: SSYS=13, SYNA=13, TGT=14, CSX=10, FLEX=13

### Deployed
- Image: `paper-trading:v49` (`sha256:04bf8d8f...`)
- Revision: `paper-trading-live-00071-79g`

---

## [2026-02-09] - Dashboard: Add Shares & Dollar P/L to Closed Today

### Summary
Added `Shares` and `$ P/L` columns to the Google Sheets "Closed Today" tab so overall profit/loss is visible at a glance without mental math. Backfilled all 25 historical closed trades from `paper_trades_log`.

### Modified Files
- **`paper_trading/dashboard.py`**:
  - `close_position()` — Added `shares: int` and `pnl_dollars: float` parameters (backward-compatible defaults)
  - Row format changed from 6 → 8 columns: `Date/Time | Symbol | Shares | Entry | Exit | P/L % | $ P/L | Result`
  - `clear_daily()` — Updated "Closed Today" header row to match new columns
  - Fixed stale `pnl` reference in debug log (was NameError, now uses `pnl_pct`)
- **`paper_trading/position_manager.py`**:
  - `close_position()` call at line 460 now passes `shares=trade.shares, pnl_dollars=trade.pnl`

### New Files
- **`scripts/backfill_closed_sheet.py`** — One-off script to backfill "Closed Today" tab from `paper_trades_log`
  - Fetches DB credentials from Secret Manager, converts Cloud SQL socket URL to local TCP
  - Batch-writes all rows in one `update()` call

### Backfill Results
- 25 closed trades written (Feb 4 → Feb 9)
- Includes crash_recovery entries (5 trades from Feb 5 v38 crash)

---

## [2026-02-08] - Adaptive RSI: Bounce-Day Relaxation (v48)

### Summary
Implements adaptive RSI threshold that relaxes from 50 → 60 on market bounce-back days (SPY opens green after 2+ consecutive red closes). Backtest-validated over 7 months: 19 new trades, 73.7% WR, +$1,647 incremental PnL, Sharpe 5.75 → 5.88, zero hard stops hit.

### Modified Files
- **`paper_trading/config.py`**:
  - Added `USE_ADAPTIVE_RSI: bool = True`
  - Added `ADAPTIVE_RSI_THRESHOLD: float = 60.0`
  - Added `ADAPTIVE_RSI_MIN_RED_DAYS: int = 2`
- **`paper_trading/signal_filter.py`**:
  - Added bounce-day state vars to `SignalFilter.__init__` (`_bounce_eligible`, `_bounce_checked`, `_is_bounce_day`, `_effective_rsi_threshold`)
  - Added `_check_bounce_day_eligible()` — queries `ta_daily_close` for SPY's last 5 closes, counts red streak
  - Added `_auto_confirm_bounce_day()` — fetches SPY open via Alpaca REST at 9:31 AM ET, confirms bounce day if open > prior close
  - Modified RSI check in `apply()` to use `_effective_rsi_threshold` (50 normal, 60 bounce)
  - Added bounce-day tag to pass log: `[BOUNCE DAY: RSI<60]`
  - Added metadata tagging: `bounce_day`, `rsi_threshold_used`
  - Updated `reset_stats()` to reset bounce state and re-check for new day
  - Added bounce check to `_preload_caches()`
- **`tests/pipeline_health_check.py`**:
  - Added TEST-2.12: `check_adaptive_rsi_config()` — validates config sanity
- **`CLAUDE.md`**:
  - Updated filter chain step 3 to note adaptive RSI (V29)

### Backtest Support Files (New)
- **`scripts/enrich_signals_ta.py`** — TA enrichment for historical signals (joins ta_daily_close, master_tickers, earnings_calendar, sentiment)
- **`scripts/backtest_rsi_regime.py`** — Extended with `--input` flag for enriched JSON signals

### Rollback
Set `USE_ADAPTIVE_RSI = False` in `config.py`. One-line change, no code removal needed.

---

## [2026-02-07] - GEX Cache Optimization (v47)

### Summary
Replaced per-signal `psycopg2.connect()` in `_lookup_gex()` with a bulk-loaded in-memory cache. GEX data only changes nightly (ORATS ingest), so opening a new DB connection for every signal was unnecessary churn — especially during busy periods with 50+ signals.

### Modified Files
- **`paper_trading/signal_filter.py`**:
  - Added `_gex_cache` dict and `_gex_cache_loaded` flag to `SignalGenerator.__init__`
  - New `_load_gex_cache()` — bulk loads latest GEX for all ~5,500 symbols in one `DISTINCT ON` query, lazy-loaded on first call
  - `_lookup_gex()` now returns `self._gex_cache.get(symbol)` — O(1) dict lookup, zero DB connections

### Impact
- **Before**: ~50+ DB connections per busy period (one per signal)
- **After**: 1 DB query total (on first signal of the day), then pure in-memory lookups
- No behavioral change — same GEX metadata attached to signals

---

## [2026-02-07] - GEX + ORATS Ingest Migration to V2 (v46a)

### Summary
Migrated the V1 ORATS daily ingest job to V2, extended it with GEX (Gamma Exposure) computation from raw per-strike data, created a historical backfill CLI, and added shadow GEX metadata to live signal evaluations. Backfilled 2+ years of historical GEX data (2.85M rows).

### New Files
- **`sources/__init__.py`** — New package for data source modules
- **`sources/orats_ingest.py`** (~1050 lines) — V1 ORATS ingest adapted for V2:
  - `_get_secret()` — Secret Manager resolution with env var fallback for local dev
  - GEX accumulator in `_parse_orats_csv()` — parallel to existing symbol aggregation
  - `_find_gamma_flip()` — Interpolated zero-crossing of cumulative GEX (filters strikes to 20%-300% of spot, returns crossing nearest to spot)
  - `_finalize_gex_metrics()` — Computes call/put walls, gamma flip, snapshot_ts at 4PM ET
  - `_bulk_upsert_gex()` — Batch upsert via `execute_values` with ON CONFLICT
  - All V1 functions preserved: FTP download, CSV parsing, IV rank, HV 30d, EMAs
  - psycopg v3 → psycopg2 conversion (`execute_values` instead of COPY API)
- **`scripts/backfill_gex.py`** (~165 lines) — Historical GEX backfill CLI:
  - `--dir "D:\ORATS_TMP_Files"` — Process all files in directory
  - `--file` — Process single file
  - `--from` / `--to` — Date range filter
  - Imports GEX functions from `sources.orats_ingest`
  - Progress logging with elapsed time

### Modified Files
- **`paper_trading/signal_filter.py`**:
  - Added `metadata: Optional[Dict] = None` to `Signal` dataclass
  - Added `_lookup_gex(symbol)` method to `SignalGenerator` — queries latest GEX from `gex_metrics_snapshot`
  - Added GEX lookup in `create_signal_async()` after signal creation (wrapped in try/except, failure doesn't affect signal)
  - Added `metadata` column to `_log_evaluation_sync()` INSERT (JSONB)
- **`sql/create_signal_evaluations.sql`** — Added `trade_placed`, `entry_price`, `metadata JSONB` columns
- **`.dockerignore`** / **`.gcloudignore`** — Updated production code comment to include `sources/`

### DDL Applied
- `ALTER TABLE gex_metrics_snapshot ADD CONSTRAINT uq_gex_symbol_ts UNIQUE (symbol, snapshot_ts)` — Enables idempotent UPSERT
- `ALTER TABLE signal_evaluations ADD COLUMN metadata JSONB` — Shadow GEX data on signals
- `CREATE INDEX idx_signal_evaluations_date ON signal_evaluations(detected_at DESC)`
- Grants: `FR3_User` SELECT on `signal_evaluations`

### GEX Computation
Per-strike from ORATS CSV (~900K rows/day):
```
GEX per strike = gamma x OI x 100 x spot^2 x 0.01  (call positive, put negative)
DEX per strike = delta x callOI x 100 - |delta-1| x putOI x 100
Call wall = strike with max call OI
Put wall = strike with max put OI
Gamma flip = interpolated zero-crossing of cumulative GEX (nearest to spot)
```

### Infrastructure
- **Cloud Run Job**: `orats-daily-ingest` (image: `paper-trading:v46a`, CMD: `python -m sources.orats_ingest`, 2Gi memory, 30m timeout)
- **Cloud Scheduler**: `orats-daily-ingest-trigger` — 10 PM PT Mon-Fri
- **Cloud SQL**: Added `--add-cloudsql-instances` to job for DB socket access
- **Secrets**: All 3 (`DATABASE_URL`, `ORATS_FTP_USER`, `ORATS_FTP_PASSWORD`) resolved via Secret Manager at runtime
- **Service account**: `660675366661-compute@developer.gserviceaccount.com` — has `secretmanager.secretAccessor`

### Bug Fix (v46 → v46a)
- **`_find_gamma_flip()` returning bogus values** — Was returning first zero crossing (at deep OTM strikes with minimal OI, e.g., TSLA gamma_flip = $6.32 with spot at $394). Fixed to:
  1. Filter strikes to 20%-300% of spot price
  2. Collect ALL zero crossings
  3. Return crossing nearest to spot
- After fix: AAPL flip=$268 (spot $276), TSLA/NVDA/SPY correctly return `null` when GEX is uniformly negative

### Historical Backfill Results
- **528 files** processed (Jan 2, 2024 → Feb 5, 2026), **0 errors**
- **2,847,480 GEX rows** across **526 trading days**, **6,751 unique symbols**
- Runtime: 84 minutes (~9s/file)

### First Scheduled Run Verified (Feb 6 → Feb 7 1AM ET)
- Scheduler triggered at 10 PM PT Feb 6 (6:00 AM UTC Feb 7)
- Completed in 7m 42s, exit(0)
- **orats_daily**: 5,831 rows for Feb 6 (matches V1)
- **gex_metrics_snapshot**: 5,573 rows for Feb 6

### GEX Data Quality (Feb 6)
| Symbol | Spot | Net GEX | Gamma Flip | Call Wall | Put Wall |
|--------|------|---------|------------|-----------|----------|
| AAPL | $278.20 | +$1.74B | $268.75 | $280 | $250 |
| NVDA | $186.26 | +$1.06B | $184.92 | $200 | $140 |
| SPY | $690.87 | +$2.99B | $711.57 | $700 | $600 |
| TSLA | $414.29 | +$529M | $432.18 | $960 | $300 |
| META | $660.17 | +$83M | $946.31 | $700 | $600 |

### Deployed
- Image: `paper-trading:v46a` (`sha256:ef7d7ca7...`)
- `paper-trading-live` service: revision `paper-trading-live-00067-2vq`
- `orats-daily-ingest` job: generation 3

### Backfill Complete
- **Historical GEX backfill executed** on 2026-02-07 via `scripts/backfill_gex.py --dir "D:\ORATS_TMP_Files"`
- 528 ORATS ZIP files (Jan 2, 2024 → Feb 5, 2026) processed locally through Cloud SQL Auth Proxy
- 2,847,480 rows written to `gex_metrics_snapshot` (526 trading days, 6,751 symbols)
- Zero errors, 84 minutes runtime, idempotent via UPSERT (safe to re-run)

### Pending
- After 3-5 days of V2 running cleanly, disable V1's `orats-daily-ingest` scheduler in `spartan-buckeye-474319-q8`
- Shadow GEX metadata will appear in `signal_evaluations.metadata` on next market-hours signal

---

## [2026-02-06] - Crash-Resilient Trade Persistence (v45)

### Critical Fixes
- **`paper_trades_log` never written to** (`paper_trading/dashboard.py`, `paper_trading/position_manager.py`): Trade state was in-memory only. Added `log_trade_open()`, `log_trade_close()`, `load_open_trades_from_db()` functions. Wired into `open_position()` and `close_position()`.
- **Cross-day WHERE bug** (`paper_trading/dashboard.py`): `update_signal_trade_placed()` and `close_signal_in_db()` used `DATE(detected_at) = CURRENT_DATE`, preventing exits for positions opened on prior days. Fixed with subquery matching by symbol + action status.
- **Startup 3-way reconciliation** (`paper_trading/position_manager.py:sync_on_startup()`): Rewrote to reconcile DB (`paper_trades_log`) with Alpaca positions — Case A (both = restore metadata), Case B (DB-only = crash_recovery), Case C (Alpaca-only = create record).

### Changes
- `paper_trading/dashboard.py`:
  - Fixed `update_signal_trade_placed()` WHERE clause (removed `DATE(detected_at) = CURRENT_DATE`)
  - Fixed `close_signal_in_db()` WHERE clause (same)
  - Added `log_trade_open()` — INSERT with `RETURNING id`
  - Added `log_trade_close()` — UPDATE by `trade_db_id` (fallback by symbol)
  - Added `load_open_trades_from_db()` — SELECT open trades for startup recovery
- `paper_trading/position_manager.py`:
  - Added `trade_db_id: Optional[int]` to `TradeRecord` dataclass
  - Wired `log_trade_open()` in `open_position()`
  - Wired `log_trade_close()` in `close_position()`
  - Rewrote `sync_on_startup()` with 3-way DB+Alpaca reconciliation

### DDL
- `CREATE INDEX idx_paper_trades_log_open ON paper_trades_log (symbol) WHERE exit_time IS NULL`
- Cleaned 5 orphaned `active_signals` stuck in HOLDING from Feb 5

### Deployed
- Image: `paper-trading:v45` → `paper-trading-live` service
- Revision: `paper-trading-live-00065-9gb`

---

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

## Database Tables (as of v46a)

### Written by Live Trading
- `active_signals` - Signals that passed all filters
- `paper_trades_log` - Executed trades (open/close with DB IDs)
- `tracked_tickers_v2` - Symbols added for intraday TA updates
- `signal_evaluations` - All signal evaluations with pass/fail + `metadata` JSONB (shadow GEX)
- `intraday_baselines_30m` - Live bucket aggregation from firehose
- `spot_prices_1m` - 1-min OHLCV bars for ~1,800 symbols (SIP feed, 60s cycle, 7-day retention)

### Written by ORATS Ingest (nightly)
- `orats_daily` - Symbol-level options activity (~5,831 rows/day)
- `gex_metrics_snapshot` - Per-symbol GEX/DEX/walls/gamma flip (~5,570 rows/day, 2.85M total)

### Read by Live Trading
- `ta_daily_close` - Prior-day TA (before 9:35 AM)
- `ta_snapshots_v2` - Intraday TA (after 9:35 AM, 5-min refresh)
- `vw_media_daily_features` - Crowded trade + sentiment filter
- `master_tickers` - Sector lookup (used in signal creation, not filtering)
- `earnings_calendar` - Earnings proximity filter
- `intraday_baselines_30m` - Per-symbol notional baselines
- `gex_metrics_snapshot` - Shadow GEX lookup for signal metadata
