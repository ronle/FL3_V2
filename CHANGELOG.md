# Changelog

All notable changes to FL3_V2 paper trading system.

## [2026-03-01 11:10 PST] — Cameron Article Enrichment (V2 Side)

### Done
- **`article_lookup.py`** (new): `check_articles_for_symbol()` queries `articles` + `article_entities` for ticker matches within 36h lookback. Returns `ArticleInfo(has_news, article_count, latest_title, latest_publish_time)`. Graceful degradation on any error.
- **`cameron_scanner.py`**: Added `_publish_candidates()` — writes today's candidates to `cameron_candidates_daily` table after `load_candidates()`. Coordination table for V1 article fetch job.
- **`cameron_scanner.py`**: Early `load_candidates()` call at startup (before 9:45 AM scan window) so candidates are published to coordination table before V1 article fetch runs at 8:30 AM.
- **`cameron_checker.py`**: Added `has_news: bool` and `article_count: int` fields to `CameronTradeSetup` dataclass.
- **`main.py`**: `_poll_cameron_patterns()` enriches each setup with article data before iteration. Logs `"Cameron NEWS: SYMBOL has N articles"` when articles found. Dashboard strength column shows `[NEWS xN]` suffix.
- **`dashboard.py`**: `log_trade_open()` extended with `has_news` and `article_count` params. `paper_trades_log_c` INSERT includes both new columns. `paper_trades_log_b` branch unchanged (those columns don't exist on that table).
- **`position_manager.py`**: `TradeRecord` extended with `has_news` and `article_count`. `open_limit_position()` reads from setup via `getattr()` (safe defaults), passes through to `log_trade_open()`.
- **SQL**: `cameron_candidates_daily` table (PK: trade_date, symbol) + ALTER `paper_trades_log_c` to add `has_news` BOOLEAN and `article_count` INTEGER.
- **BACKLOG.md**: Added P1 item for FL3 side: Cameron Pre-Market Article Fetch job (`fr3-cameron-news-fetch`, 8:30 AM ET Mon-Fri)

### State
- V2 side complete — all code changes ready for deployment
- DDL must be run before deploying code (`sql/create_cameron_candidates_daily.sql`, `sql/alter_paper_trades_log_c_has_news.sql`)
- V1 article fetch job NOT yet implemented (backlogged as P1)
- Without V1 job, `has_news` will be True only for symbols already covered by existing `fr3-media-news-fmp` job

### Next
- Run DDL on production database
- Build and deploy new image
- Implement V1 `cameron_news_fetch.py` (FL3 project, P1 backlog item)
- After 1-2 weeks of data collection: analyze `paper_trades_log_c WHERE has_news = TRUE` vs baseline

### Files Changed
- `sql/create_cameron_candidates_daily.sql` (new)
- `sql/alter_paper_trades_log_c_has_news.sql` (new)
- `paper_trading/article_lookup.py` (new)
- `paper_trading/cameron_scanner.py` (modified)
- `paper_trading/cameron_checker.py` (modified)
- `paper_trading/main.py` (modified)
- `paper_trading/dashboard.py` (modified)
- `paper_trading/position_manager.py` (modified)
- `BACKLOG.md` (modified — added P1 item)

---

## [2026-02-28 19:00 PST] — Expanded Cameron Backtest (2020-2026) + Sentiment Analysis

### Done
- **Rebuilt Cameron universe** from 2020-01-01 (was 2023+): 13.2M rows, 18,946 symbols, 1,535 trading days
- **Expanded intraday backtest**: 4,146 trades across 6 years (2020-2026), dual-exit (target_1 + target_2). Profitable every year. Results saved to `backtest_results/cameron_intraday_trades_target_1_full.csv` and `_target_2_full.csv`
- **Sentiment correlation on expanded trades**: Ran S1-S6 tests + article-matched subset analysis on 4,146 trades. Conclusion: sentiment_daily is dead for Cameron (0.4% coverage), NEWS ONLY shows directional signal (N=53, Sharpe 4.89) but not significant, no filter meets adoption criteria (p<0.05)
- **Updated CAMERON_FINDINGS.md**: Section 7 now reflects B2 deployment status, filter stack definition, and remaining next steps
- **Fixed `cameron_sentiment_correlation.py`**: Added `--trades` CLI arg for flexible input, `::text[]` cast for large symbol arrays, added `run_article_subset_analysis()` for deep dive on article-matched trades

### State
- B2 filter stack validated across 6 years — no changes needed
- Sentiment filters conclusively dropped from Cameron roadmap
- Account C live on Cloud Run (revision `paper-trading-live-00111-4rp`), monitoring starts Monday

### Next
- Monitor Account C first live trading week
- Evaluate live performance vs 6-year backtest baseline

### Files Changed
- `E:/backtest_cache/cameron_daily_universe.parquet` (rebuilt from 2020)
- `backtest_results/cameron_intraday_trades_target_1_full.csv` (new)
- `backtest_results/cameron_intraday_trades_target_2_full.csv` (new)
- `backtest_results/cameron_intraday_summary_target_1_full.json` (new)
- `backtest_results/cameron_intraday_summary_target_2_full.json` (new)
- `backtest_results/cameron_trades_with_sentiment_full.csv` (new)
- `scripts/cameron_sentiment_correlation.py` (modified)
- `Docs/CAMERON_FINDINGS.md` (updated)

---

## [2026-02-28 00:30 PST] — v61 DEPLOYED: Account C — Cameron B2 Pattern Trader

### Done
- **Account C integration**: Full Cameron B2 pattern trader wired as third independent paper trading account, mirroring Account B architecture.
- **`cameron_scanner.py`** (new): Real-time pattern scanner coroutine. Pre-market loads gapper candidates from `orats_daily` (gap≥4%, rvol≥10x, $1-$20, cap 30 symbols). During 9:45-11:00 AM ET every 60s: fetches 5-min bars from Alpaca, runs 3 pattern detectors (consolidation_breakout, vwap_reclaim, bull_flag), UPSERTs moderate-strength patterns to `cameron_scores` table.
- **`cameron_checker.py`** (new): Polls `cameron_scores` every 30s. B2 filter stack: moderate-only, priority sort (consol_breakout > vwap_reclaim > bull_flag), bull flag max 1/day, dedup by (symbol, pattern_date, pattern_type).
- **`cameron_scores` table** (new): 13 columns, UNIQUE on (symbol, pattern_date, pattern_type, interval), indexed on scan_ts. Grants to `fr3_app`.
- **`paper_trades_log_c` table** (new): Cloned from `paper_trades_log_b` (24 columns including direction, stop, target, etc.). Grants to `fr3_app`.
- **Config**: 10 new settings in `TradingConfig` — scan window, poll/scan intervals, BF daily cap, max risk ($500), max positions (5), confirmation window (30 min).
- **main.py**: ~130 lines added — Account C init, WebSocket stop/target monitoring (bullish-only), async exit, pattern polling, scanner tick, daily reset, dashboard updates, startup sync, shutdown.
- **dashboard.py**: `paper_trades_log_c` in ALLOWED_TABLES; extended column branches handle `_c` alongside `_b` for log_trade_open and load_open_trades_from_db.
- **Deployed** `paper-trading:v61` → revision `paper-trading-live-00110-mlh` (100% traffic)
- **Note**: Account C is disabled at runtime until `ALPACA_API_KEY_C` / `ALPACA_SECRET_KEY_C` env vars are provisioned on Cloud Run. Accounts A and B unaffected.

### State
- v61 LIVE on `paper-trading-live` (revision `paper-trading-live-00110-mlh`, 100% traffic)
- Account C: code deployed, DB tables created, awaiting Alpaca paper account keys
- Accounts A and B: unchanged, running normally
- Market closed (Saturday) — Account C first live test after keys provisioned + next market open
- Rollback: `paper-trading:v60` or set `USE_ACCOUNT_C = False`

### Next
- Provision Alpaca paper trading account for Account C (API key + secret)
- Set `ALPACA_API_KEY_C` and `ALPACA_SECRET_KEY_C` on Cloud Run service
- Monitor first Cameron scan session:
  - Candidate loading from orats_daily
  - Pattern detection rate during 9:45-11:00 AM window
  - Limit order submissions and fill rates
  - Stop/target exit execution

### Files Changed
- `paper_trading/cameron_checker.py` (NEW)
- `paper_trading/cameron_scanner.py` (NEW)
- `sql/create_cameron_scores.sql` (NEW)
- `sql/create_paper_trades_log_c.sql` (NEW)
- `paper_trading/config.py` (MODIFIED)
- `paper_trading/dashboard.py` (MODIFIED)
- `paper_trading/main.py` (MODIFIED)

## [2026-02-27 17:35 PST] — v60 DEPLOYED: Account B Redesign — Big-Hitter Pattern Trader

### Done
- **Full Account B redesign**: Replaced UOA trigger + engulfing confirmation flow with independent pattern polling from `engulfing_scores` table (5-min timeframe).
- **New signal flow**: Poll `engulfing_scores` every 30s → apply big-hitter filters (candle_range ≤ 0.57, risk/share ≥ $1, volume confirmed, trend context) → submit limit order at pattern's entry_price → monitor fill (cancel after 30 min) → monitor stop_loss/target_1 → exit at stop (market), target (market), or EOD 3:55 PM.
- **Both directions**: Supports long AND short trades (direction from engulfing pattern). Bearish patterns submit sell-short orders, with direction-aware P&L (entry - exit for shorts).
- **Limit order entry**: No more market orders for Account B. Limit orders at pattern entry_price with 30-min confirmation window. Unfilled orders auto-cancelled.
- **$500 max risk/trade**: Position sizing via `qty = floor($500 / risk_per_share)` instead of portfolio-percentage sizing.
- **DB migration**: Added 9 columns to `paper_trades_log_b`: direction, stop_price, target_price, risk_per_share, limit_order_id, order_submitted_at, pattern_date, candle_range, pattern_strength.
- **Dashboard update**: Account B tabs now show Direction/Entry/Stop/Target/Risk-per-Share/Strength instead of old Score/Engulfing/Notional layout.
- **No UOA dependency**: Account B no longer requires firehose UOA triggers. Operates independently via DB polling.
- **Deployed** `paper-trading:v60` → revision `paper-trading-live-00109-6tx` (100% traffic, latestRevision=true)
- **Verified**: Clean startup, all components healthy, Account B initialized as Big-Hitter Pattern Trader.

### State
- v60 LIVE on `paper-trading-live` (revision `paper-trading-live-00109-6tx`, 100% traffic)
- Account A: 10 momentum positions held (GRAL, BOIL, NVO, KD, MSTZ, QURE, KLAR, ODD, LQDA, FLNC)
- Account B: 0 positions, $95K equity, $355K buying power — ready for Monday
- Market closed (Friday 8:30 PM ET) — first live test will be Monday market open
- Rollback: `paper-trading:v59` or set `USE_ACCOUNT_B = False`

### Next
- Monitor Monday's first trading session for Account B:
  - Pattern poll count and filter pass/fail reasons
  - Limit order submissions at correct entry prices
  - Fill detection and position tracking
  - Stop/target monitoring via WebSocket price updates
  - EOD close of both long and short positions
  - Pending unfilled order cancellation at EOD
- Verify dashboard Account B tabs display correctly with new layout

### Files Changed
- `paper_trading/config.py` — Removed old engulfing lookback settings, added big-hitter config (poll interval, max risk, candle range, min risk/share, confirmation window, lookback)
- `paper_trading/engulfing_checker.py` — Complete rewrite: `EngulfingChecker` → `PatternPoller` + `TradeSetup` dataclass
- `paper_trading/alpaca_trader.py` — Added `sell_short()` and `buy_to_cover()` methods
- `paper_trading/position_manager.py` — Extended `TradeRecord` with direction/stop/target fields, added `_pending_limit_orders`, `open_limit_position()`, `check_pending_orders()`, `check_stops_and_targets()`, `cancel_all_pending_limit_orders()`, direction-aware P&L
- `paper_trading/dashboard.py` — Updated `log_signal()`, `clear_daily()`, `log_trade_open()`, `load_open_trades_from_db()` for Account B big-hitter layout and new DB columns
- `paper_trading/main.py` — Replaced `EngulfingChecker` with `PatternPoller`, added `_poll_account_b_patterns()`, `_check_account_b_pending()`, direction-aware stop/target in WebSocket callback, EOD pending order cancellation
- `sql/alter_paper_trades_b_big_hitter.sql` — Migration adding 9 columns to `paper_trades_log_b`
- `CHANGELOG.md`, `CLAUDE.md` — Documentation

## [2026-02-27 16:51 PST] — v59 DEPLOYED: Fix orphan position auto-sell on container restart

### Done
- **Root cause identified**: Cloud Run recycled container at ~1 AM ET. `sync_on_startup()` found Account B positions with no DB records (orphaned), submitted sell orders immediately. Alpaca queued them for market open, closing positions prematurely.
- **Fix**: Orphaned positions (Alpaca-only, no DB record) are now **adopted** into `active_trades` instead of auto-closed. A DB record is created so future restarts see them as Case A (DB+Alpaca). Normal exit logic (EOD closer / hard stop) handles closing at the right time.
- **Deployed** `paper-trading:v59` → revision `paper-trading-live-00108-dnh` (100% traffic)
- **Verified**: Clean startup, Account A 10 momentum positions restored (DB+Alpaca), Account B 0 positions (already sold this morning), 0 orphaned.

### Root Cause Analysis — Account B 1 AM Sell
- Account B positions were placed at ~3:56-3:58 PM ET yesterday (after EOD closer ran at 3:55 PM)
- Positions survived overnight (EOD closer had `_closed_today=True`)
- At ~1 AM ET, Cloud Run recycled container → `sync_on_startup()` ran
- Positions existed on Alpaca but had no matching records in `paper_trades_log_b`
- Old code (Case C): immediately submitted sell orders → queued for market open
- New code (Case C): adopts into `active_trades`, creates DB record, lets EOD closer handle exit

### State
- v59 LIVE on `paper-trading-live` (revision `paper-trading-live-00108-dnh`, 100% traffic)
- Account A: 10 momentum positions held (GRAL, BOIL, NVO, KD, MSTZ, QURE, KLAR, ODD, LQDA, FLNC)
- Account B: 0 positions (sold at market open due to pre-fix bug)
- Rollback: `paper-trading:v58c` (reverts to auto-close orphans)

### Next
- Investigate why Account B trades had no DB records despite going through the system (`log_trade_open()` may have failed silently)
- Monitor next container restart to confirm orphans are adopted correctly

### Files Changed
- `paper_trading/position_manager.py` (sync_on_startup Case C: close → adopt)
- `CHANGELOG.md` (this entry)
- `CLAUDE.md` (current status updated)

## [2026-02-26 13:15 PST] — v58c DEPLOYED: Momentum Query Fix + Fill Retry

### Done
- **Fixed momentum screen timeout** (v58b): `rsi_screener.py` query used `DISTINCT ON (symbol)` scanning all 8.3M rows of `orats_daily` — timed out on Cloud Run at 3:50 PM ET. Replaced with `WHERE asof_date = (SELECT MAX(asof_date) WHERE asof_date < CURRENT_DATE)` which scans ~5K rows. Same results, sub-second execution.
- **Fixed ghost position bug** (v58c): `position_manager.py:open_position()` waited only 2s for Alpaca fill confirmation. At market open, fills can take >2s — CNR and PLTK both hit "Position not found after buy" and were never added to `active_trades`. Orders filled on Alpaca but were invisible to EOD closer. Added retry loop: 2s + 3s + 5s + 8s = 18s max with fill status checks.
- **Manual momentum buys submitted**: Screen timed out at 3:50 PM, ran manually at 3:58 PM. All 10 positions filled (ODD, GRAL, DRVN, KD, TNET, LMND, FLNC, MSTZ, LQDA, NVO).
- **Cancelled stale Account B orders**: 2 orphaned sell orders for CNR/PLTK (submitted after market close) cancelled. Positions remain — tomorrow's sync_on_startup will close them.

### Root Cause Analysis — Account B Ghost Positions
- CNR bought 9:46 AM, PLTK bought 9:50 AM (first 2 trades at open)
- Both: order submitted -> 2s wait -> `get_position()` returned None -> `return None`
- Orders DID fill on Alpaca moments later, but `active_trades` dict never got the entry
- EOD closer at 3:55 PM iterated `active_trades` (10 entries) — CNR/PLTK invisible
- At 4:03 PM (v58b restart), sync_on_startup found them as "orphaned Alpaca positions" but market was closed

### State
- v58c LIVE on `paper-trading-live` (revision `00107-m6v`, 100% traffic)
- Account A: 10 momentum positions held overnight (ODD, GRAL, DRVN, KD, TNET, LMND, FLNC, MSTZ, LQDA, NVO)
- Account B: 2 orphaned positions (CNR, PLTK) — will be closed at tomorrow's startup

### Next
- Verify tomorrow: momentum screen fires at 3:50 PM without timeout
- Verify tomorrow: Account B ghost position fix (fills take <18s to confirm)
- Monitor CNR/PLTK orphan cleanup at market open

### Files Changed
- `paper_trading/rsi_screener.py` (query optimization: DISTINCT ON -> subquery)
- `paper_trading/position_manager.py` (fill retry: 2s single check -> 4-attempt backoff up to 18s)
- `CHANGELOG.md` (this entry)
- `CLAUDE.md` (current status updated)

## [2026-02-26 01:25 PST] — v58a DEPLOYED: Momentum Screener Live

### Done
- **Rewrote RSI screener → Momentum screener** (previous session, deployed this session):
  - `rsi_screener.py`: `RSICandidate` → `MomentumCandidate`, `RSIScreener` → `MomentumScreener`
  - Eliminated 178-line RSI computation, replaced with single `DISTINCT ON` query on `orats_daily` for `price_momentum_20d < -0.10`
  - Sorts by momentum ascending (most beaten-down first), returns top 10
  - `config.py`: `USE_RSI_SCREENER` → `USE_MOMENTUM_SCREENER`, all `RSI_SCREEN_*` → `MOMENTUM_*`, hard stop -3%
  - `main.py`: All `_rsi_*` references → `_momentum_*`, updated imports and log messages
  - `eod_closer.py`: `USE_RSI_SCREENER` → `USE_MOMENTUM_SCREENER`
- **Fixed 107GB Docker context leak**: `.tmp/` directory (DuckDB temp storage) was not in `.dockerignore`/`.gcloudignore`. Added to both.
- **Built locally** via Docker Desktop (Cloud Build MCP tool was being rejected). Context now ~2.4 MB.
- **Deployed** `paper-trading:v58a` → revision `paper-trading-live-00105-mgg` (ACTIVE, 100% traffic)
- **Verified**: Zero errors, startup healthy (1.19s), caches loaded (5,977 sector, 1,679 earnings)

### State
- v58a is LIVE on `paper-trading-live`
- Momentum screener will run at 3:50 PM ET, buy at 3:56 PM, D+1 exit at 3:55 PM, -3% hard stop
- Account B unchanged (engulfing-primary)

### Next
- Monitor first live run today at 3:50/3:55/3:56 PM ET
- Verify momentum candidates logged correctly
- Consider -2% stop upgrade after live validation (backtest Sharpe 1.31 vs 1.03)

### Files Changed
- `paper_trading/rsi_screener.py` (rewritten: RSI → Momentum screener)
- `paper_trading/config.py` (renamed RSI fields → MOMENTUM fields)
- `paper_trading/main.py` (renamed all _rsi_ → _momentum_ references)
- `paper_trading/eod_closer.py` (USE_RSI_SCREENER → USE_MOMENTUM_SCREENER)
- `.dockerignore` (added `.tmp/` — was leaking 107GB DuckDB temp)
- `.gcloudignore` (added `.tmp/` — kept in sync)
- `CHANGELOG.md` (this entry)
- `CLAUDE.md` (current status updated)

## [2026-02-26 00:30 PST] — V6 Research: OI Direction Killed, Momentum < -10% Validated

### Done
- **V6 OI Direction Investigation** (7 tests):
  - Tests 1-3: Same-day OI showed promising results (Sharpe 1.40, p=0.0015) — but used look-ahead bias
  - Test 4: D-1 OI entry timing (5 months) — EOD still optimal, but favorable window artifact
  - **Test 5 (DEFINITIVE): D-1 OI, 3 years, minute bars — OI direction DEAD** (spread=0.016%, t=0.195, p=0.85)
- **RSI<30 minute-bar reality check**:
  - Ran V5 `backtest_rsi30_minutebars.py` — Sharpe **0.25** (top 10) vs clip-method's 1.87 (7x overstatement)
  - Random 10 from RSI<30 pool = Sharpe 0.24 (zero selection skill)
  - Root cause: `ret.clip(lower=-3%)` counts intraday stop-and-reverse as winners
- **Momentum < -10% discovery and validation** (`backtest_momentum_realistic.py`):
  - `price_momentum_20d < -0.10`, top 10 most beaten-down, -3% stop: **Sharpe 1.03**
  - With slippage (3bp/10bp/3bp), position limits (10/day), real minute-bar stops
  - t=5.57, p<0.0001, 3/3 years positive (2023: 0.74, 2024: 1.12, 2025: 1.25)
  - Selection skill exists: most beaten 1.03 > random 0.64 > least beaten 0.41
  - Stop sensitivity: -2% Sharpe 1.31, -3% Sharpe 1.03, no stop 0.23
- **Documentation updated**: V6_OI_DIRECTION_FINDINGS.md (complete rewrite), V5_FINDINGS_SUMMARY.md (corrected recommendations), CLAUDE.md (v58 plan revised), MEMORY.md (V6 conclusions)

### State
- v58 plan revised: momentum < -10% replaces RSI<30 as screener strategy
- Existing `rsi_screener.py` code needs update to use momentum filter instead
- Not yet deployed

### Next
- Rewrite `rsi_screener.py` → `momentum_screener.py` (or update in-place):
  - Query orats_daily D-1 for `price_momentum_20d < -0.10, stock_price >= 10, avg_daily_volume >= 1000`
  - Sort by momentum ascending (most beaten-down first)
  - Buy top 10 at 3:56 PM, exit D+1 at 3:55 PM, -3% hard stop
- Build and deploy v58
- Consider -2% stop (Sharpe 1.31) vs -3% (Sharpe 1.03) — tradeoff between Sharpe and stop frequency

### Files Changed
- `temp/V6_OI_DIRECTION_FINDINGS.md` (complete rewrite — OI killed, momentum validated)
- `temp/V5_FINDINGS_SUMMARY.md` (corrected recommendations with V6 update)
- `temp/backtest_oi_d1_3yr.py` (definitive D-1 OI test — written in prior session)
- `temp/backtest_momentum_realistic.py` (NEW — realistic momentum backtest)
- `CLAUDE.md` (current status updated for v58 momentum plan)
- `CHANGELOG.md` (this entry)
- `.claude/projects/.../memory/MEMORY.md` (V6 conclusions replace V5)

## [2026-02-25 16:30 PST] — v58: RSI<30 EOD Screener (Account A)

### Done
- **New module:** `paper_trading/rsi_screener.py` — EOD screener queries orats_daily D-1, computes RSI(14) via Wilder's smoothing, filters price>=$10 + ADV>=1K, ranks by RSI ascending
- **Config:** Added `USE_RSI_SCREENER=True`, `USE_UOA_SIGNALS=False`, `RSI_SCREEN_TIME=15:50`, `RSI_BUY_TIME=15:56`, `RSI_MAX_CANDIDATES=10`, `HARD_STOP_PCT=-0.03`
- **EOD closer:** `_close_prior_day_positions()` — only closes prior-day positions at 3:55 PM (D+1 exit), leaves same-day buys open overnight. Account B always closes all (via `use_prior_day_close=False`).
- **Main loop:** RSI screen at 3:50 PM, prior-day close at 3:55 PM, RSI buys at 3:56 PM. Account A UOA signal path gated on `USE_UOA_SIGNALS` (disabled). Account B unchanged.
- **Timezone safety:** `_close_prior_day_positions()` localizes naive datetimes to ET before date comparison

### State
- Code complete, not yet deployed. Next step: build image, deploy as v58.

### Next
- Build and deploy v58 to Cloud Run
- Verify logs at 3:50/3:55/3:56 PM — screen runs, prior-day closes, new buys execute
- Monitor overnight hold + next-day D+1 exit
- Verify Account B independence (engulfing signals continue all day)

### Files Changed
- `paper_trading/rsi_screener.py` (NEW — ~150 lines)
- `paper_trading/config.py` (added RSI screener flags, -3% stop)
- `paper_trading/eod_closer.py` (prior-day close + use_prior_day_close param)
- `paper_trading/main.py` (import, init, run loop, _execute_rsi_buys, daily reset, UOA gate)
- `CHANGELOG.md` (this entry)

## [2026-02-25 16:00 PST] — V5 Research Complete + Entry Timing + RSI<30 Strategy Discovery

### Done
- **V5 Hypothesis Tests** (43K filtered signals): All 4 hypotheses rejected
  - H1-B: Vol does NOT expand post-signal (ratio 1.019, not significant)
  - H5-A: Fade call-heavy has no contrarian edge
  - H6-A: Zero stock-specific alpha after sector ETF adjustment
  - H2-C: Aggregate call_pct does not predict SPY direction (r=-0.041)
- **Base Assumption Test** (1.55M unfiltered signals): call_pct has ZERO directional power (r=-0.0014). 66% of signals are 100% call
- **Put-Heavy Mean Reversion**: Raw returns looked great (+0.50% D+5), but excess vs SPY = 0.00%. Pure market beta
- **B8 vs SPY Decomposition**: Raw excess +0.055% (p=0.65). Stop-adjusted excess +0.260% (p=0.03). Stop creates the P&L
- **B8 vs Random Monte Carlo** (500 sims): B8 beats 100% of random (z=7.6). UOA stocks 4.8x more volatile — stop works harder
- **B8 vs Random by Year**: 2022 bear: B8 loses (-$15K), worse than random (-$7K). 2023 drives 53% of total P&L
- **Regime Indicators**: Tested 8 indicators (SMA50/200, momentum, vol, drawdown, golden cross, composite). None improve P&L over "always long + stop"
- **B8 Entry Timing** (critical finding): EOD entry -> D+1 = +$643K. Signal-time -> same-day EOD = **-$321K**. Current live engine runs the worst strategy. Stocks move UP between signal and close (t=-10.03, p=0.0000)
- **B8 vs RSI<30 Universe**: B8 only beats 71% of random RSI<30 picks (z=0.55). UOA adds marginal value over simple RSI screening
- **RSI<30 Config Sweep**: Full parameter sweep on 440K symbol-days. Best config: RSI<30, $10+, -3% stop = **Sharpe 1.87**, 7/7 years positive, +$545K (10-pos sim). 6.7x higher Sharpe than B8
  - RSI threshold: lower is better per-trade (RSI<20 = +0.64%/trade) but fewer opportunities
  - Price floor: higher = better Sharpe ($10+ = 1.61, $30+ = 1.89, $50+ = 1.94)
  - Stop: tighter = higher Sharpe (-2% = 3.17, -3% = 2.41, -5% = 1.61)
  - Momentum: most beaten-down Q1 (<-11% 5d) = +1.17%/trade, Sharpe 3.35
  - Trend filter: irrelevant (RSI<30 stocks are 99% below SMA20 already)
  - Excess vs SPY: +0.105% (t=22.58, p=0.0000) — statistically significant
- **V5 Findings Summary**: `temp/V5_FINDINGS_SUMMARY.md` — comprehensive report with all 13 tests

### Key Conclusions
1. UOA does NOT predict direction — it selects volatile stocks
2. The stop loss IS the strategy (truncates fat left tail, right tail runs)
3. **Entry timing is critical**: EOD entry required. Signal-time entry DESTROYS returns
4. **UOA adds no value over simple RSI<30 screening** (z=0.55 vs random RSI<30)
5. **RSI<30 + EOD buy + D+1 exit + -3% stop** = Sharpe 1.87, 7/7 years positive, no UOA needed
6. Most beaten-down stocks (5d momentum Q1) bounce hardest: Sharpe 3.35
7. UOA's real value is for options volatility strategies (4.8x more volatile)

### State
- V5 research phase complete. All findings documented in `temp/V5_FINDINGS_SUMMARY.md`
- v57 still deployed (UOA-based, intraday entry). Needs fundamental architecture change
- Recommended new strategy: RSI<30 EOD screener (replaces UOA signal-based approach)
- Parallel track: options_trader project exploring volatility-based options strategies

### Next
- Design and implement v58: RSI<30 EOD screener (replace UOA intraday triggers)
- Architecture change: queue/screen at ~3:50 PM, buy at 3:55 PM, sell next day 3:55 PM
- Test ADV filter within RSI<30 universe (not yet tested)
- options_trader: test straddle/strangle strategies using UOA as volatility selector

### Files Changed
- `temp/run_v5_hypothesis_tests.py` (new) — V5 enrichment + 4 hypothesis tests
- `temp/test_base_assumption.py` (new) — Base assumption on 1.55M signals
- `temp/test_actionable_paths.py` (new) — 3 actionable paths analysis
- `temp/deep_dive_put_reversion.py` (new) — Put-heavy deep dive
- `temp/put_reversion_clean.py` (new) — Clean universe analysis
- `temp/put_reversion_vs_spy.py` (new) — SPY benchmark comparison
- `temp/b8_vs_spy.py` (new) — B8 P&L decomposition
- `temp/b8_random_baseline.py` (new) — Monte Carlo vs random stocks
- `temp/b8_random_by_year.py` (new) — Year-by-year bear market test
- `temp/regime_indicators.py` (new) — 8 regime indicators tested
- `temp/composite_deep_dive.py` (new) — Composite regime breakdown
- `temp/b8_entry_timing.py` (new) — Entry timing comparison (signal vs EOD)
- `temp/b8_vs_rsi30_universe.py` (new) — B8 vs RSI<30 universe Monte Carlo
- `temp/rsi30_config_sweep.py` (new) — RSI<30 parameter optimization
- `temp/V5_FINDINGS_SUMMARY.md` (new, updated) — Complete findings report with 13 tests
- `CLAUDE.md` — Updated Current Status
- `CHANGELOG.md` — This entry

---

## [2026-02-25 08:00 PST] — Re-enrich combined_signals_v5.parquet (full 6-year dataset)

### Done
- **Created `enrich_signals_v4.py`** — single enrichment script that reads `combined_signals_v4.parquet` (43,788 signals, 2020-2026) and adds 18 columns from Cloud SQL + derived calculations
- **Forward returns** (ret_p1 through ret_p20): 99.6% coverage from `orats_daily_returns`, multiplied by 100 for percentages
- **SMA-50** (D-1 shifted): 97.8% coverage, computed from `orats_daily.stock_price` rolling 50-day mean, shifted by 1 trading day to avoid look-ahead
- **price_momentum_20d + avg_daily_volume** (D-1 shifted): 100% coverage from `orats_daily`
- **SPY daily return + spy_up**: 100% coverage from `orats_daily` WHERE symbol='SPY', pct_change()
- **market_cap**: 79.2% coverage from `master_tickers` (34,664/43,788 signals)
- **Derived analytical bands**: sma50_ok, s4_group (A_PASS_S4: 14,119 / C_RSI_FAIL_CP_OK: 29,669), rsi_band (9 bins), cp_band (6 bins), score_band (4 tiers), signal_hour, time_bucket (4 categories)
- **Output**: `D:/backtest_cache/combined_signals_v5.parquet` — 43,788 rows x 50 columns, runtime ~2.5 min

### State
- `combined_signals_v5.parquet` is the fully enriched 6-year signal dataset, ready for backtest analysis
- Supersedes `signals_enriched_v2.parquet` (which only covered 2023-2026, 9.6K rows)

### Next
- Run full S4 backtest on v5 dataset (6 years vs previous 3 years)
- Investigate s4_group distribution: 0 B/D/E groups (all signals have RSI, none in B_RSI_OK_CP_FAIL or D_FAIL_BOTH)

### Files Changed
- `temp/enrich_signals_v4.py` (new) — enrichment script
- `D:/backtest_cache/combined_signals_v5.parquet` (new) — enriched output

---

## [2026-02-24 01:00 PST] — v57: ADV >= 1K filter + -5% hard stop (3-year backtest validated)

### Done
- **3-year realistic backtest (v2)** using minute-bar data (780 trading days, DuckDB bar cache)
  - 16-config sweep across stop levels, ADV thresholds, slippage stress, and multi-day holds
  - Per-ADV-bucket slippage model from 142 live trades: <1K=19bp, 1-2K=6bp, 2-5K=7bp, 5-10K=5bp, 10-20K=8bp, 20K+=6bp
  - T+1 entry (1-min delay), bar-by-bar hard stop on lows, 15:55 ET EOD exit
  - Cloud SQL proxy for D-1 ADV enrichment (orats_daily LATERAL JOIN)
- **D-1 ADV timing fix**: Corrected look-ahead bias — ORATS data arrives after close, so live uses prior day's value. LATERAL JOIN pattern finds most recent `asof_date < signal_date`.
  - D-1 vs D0 impact: 1,346 signals (14%) cross the 5K boundary, 47.5% of signals < 1K ADV (was 31.7% with D0)
- **ADV threshold sweep** (0 to 6K in 500 increments): ADV >= 1K is optimal
  - ADV 0: Sharpe 0.78, 806 trades | ADV 500: 1.03, 522 | **ADV 1K: 1.25, 404** | ADV 2K: 0.83, 297 | ADV 5K: 0.56, 183
- **Stop sensitivity at ADV >= 1K**: -7% best (Sharpe 1.33), -4% close (1.28), -5% deployed (1.25)
- **Slippage stress**: ADV model Sharpe 1.25, 5bp=1.31, 10bp=1.09, 15bp=0.86, 20bp=0.62, 25bp=0.39 (still profitable)
- **Deployed v57** to `paper-trading-live-00102-zgf`:
  - `config.py`: `MIN_ADV=1000`, `HARD_STOP_PCT=-0.05`, `USE_ADV_FILTER=True`
  - `signal_filter.py`: ADV cache (bulk DISTINCT ON from orats_daily, O(1) lookup), rejection logged as `adv_below_1000`
  - Verified: ADV cache loaded 7,391 symbols on startup, no errors

### Key Findings
| Config | Trades | WR | Sharpe | P&L | MaxDD | PF |
|--------|--------|------|--------|---------|--------|------|
| No filter, -2% stop (old S5) | 811 | 48.3% | 0.39 | $3,784 | -4.71% | 1.09 |
| No filter, -5% stop | 806 | 51.4% | 0.78 | $8,636 | -3.22% | 1.19 |
| **ADV>=1K, -5% stop (v57)** | **404** | **56.7%** | **1.25** | **$10,457** | **-2.20%** | **1.52** |
| ADV>=5K, -5% stop (D0 bias) | 231 | 58.0% | 1.56 | $11,950 | -1.81% | 2.00 |
| ADV>=5K, -5% stop (D-1 correct) | 183 | 54.1% | 0.56 | $2,388 | -1.83% | 1.26 |

- Multi-day holds dead: only 7 trades in 3 years at ADV>=5K + high call_pct
- Original B4 (ADV 5K) collapsed from Sharpe 1.56 to 0.56 after D-1 correction — look-ahead bias was massive

### Live Config Summary (v57)
| Filter | Setting | Source |
|--------|---------|--------|
| RSI threshold | < 55 (S5) | `ta_daily_close` / `ta_snapshots_v2` |
| call_pct gate | DISABLED (S5) | — |
| GEX dead zone | Skip 2-5% above flip (S5+GEX) | `gex_metrics_snapshot` cache |
| ADV filter | >= 1,000 contracts/day, D-1 (v57) | `orats_daily` cache |
| Hard stop | -5% (v57, widened from -2%) | Alpaca positions API |
| Max positions | 10 | — |
| Sentiment | mentions < 5, sentiment >= 0 | `vw_media_daily_features` |
| Earnings | reject within ±2 days | `earnings_calendar` cache |
| Market regime | pause if SPY < -0.5% from open | Alpaca snapshot API |

### State
- v57 live on `paper-trading-live-00102-zgf`. Next trading day: Mon Feb 24.
- Backtest artifacts: `D:\backtest_cache\realistic_results\` (16 configs + sweep CSVs)

### Next
- Monitor ADV filter rejection rate in signal_evaluations during first live trading day
- Consider -4% stop (Sharpe 1.28, 5% stop rate vs 3%) if -5% proves too wide

### Files Changed
- `paper_trading/config.py` — `MIN_ADV=1000`, `HARD_STOP_PCT=-0.05`, `USE_ADV_FILTER=True`
- `paper_trading/signal_filter.py` — `_load_adv_cache()`, ADV filter in `apply()`, cache refresh on daily reset
- `temp/enrich_adv_d1.py` — D-1 ADV enrichment (LATERAL JOIN)
- `temp/run_backtest_v2.py` — 16-config sweep runner
- `temp/adv_threshold_sweep.py` — ADV threshold sweep (0-6K)
- `temp/adv1k_deep_test.py` — Stop + slippage sensitivity at ADV>=1K
- `temp/realistic_backtest.py` — Added ADV slippage model, per-bucket entry slippage, ADV filter

---

## [2026-02-23 09:10 PST] — Add market_cap column to master_tickers

### Done
- **DDL:** Added `market_cap BIGINT` and `market_cap_updated_at TIMESTAMPTZ` columns to `master_tickers` (ALTER run as `fr3_app` via Cloud SQL Auth Proxy)
- **Population script:** `scripts/refresh_market_cap.py` — fetches market_cap from Polygon `/v3/reference/tickers/{ticker}` API
  - Delta refresh by default (only NULL rows), `--all` to re-fetch everything
  - 200ms spacing (5 req/sec Polygon free tier), rate limit detection with 12s backoff
  - Connects as `fr3_app` (table owner), Polygon key from env or Secret Manager
  - Flags: `--stats`, `--dry-run`, `--limit N`, `--all`
- **Full population run:** 5,976 symbols processed in ~34 min, zero failures
  - 3,766 symbols with market_cap (63%)
  - 2,214 checked but NULL (ETFs, warrants, funds — stamped so they won't be retried)
  - Top: NVDA $4.6T, AAPL $3.9T, GOOGL $3.8T, MSFT $2.9T, AMZN $2.3T

### State
- Column populated and queryable. No deploy needed (DB-only change + offline script).
- Re-run monthly: `python -m scripts.refresh_market_cap --all`

### Files Changed
- `scripts/refresh_market_cap.py` (new)

---

## [2026-02-22] — S4: RSI Hard Cap + Call% Gate

### Done
- **Disabled adaptive RSI** (`USE_ADAPTIVE_RSI = False`): RSI threshold now hard-capped at 50.0 permanently. Bounce-day relaxation (V29) removed from active filtering. Historical analysis showed RSI 55-60 band (bounce-day relaxation zone) had -1.44% avg P&L, 23.5% WR, PF 0.14 across 122 Account A trades (Feb 4-20).
- **Added call_pct filter** (`USE_CALL_PCT_FILTER = True`, `CALL_PCT_MAX = 0.95`): Rejects signals where call_pct > 95%. Pure-call triggers showed -0.38% avg P&L, 38.2% WR, PF 0.58.
- **Combined S4 backtest**: Applying both filters on historical trades flips portfolio from -$511 to +$507, cuts stop-hit rate from 25% to 11.5%, reduces max losing streak from 16 to 5.
- Account B unaffected (bypasses filter chain entirely at aggregator level).

### State
- Ready for deploy. Verification: `python -c "from paper_trading.config import DEFAULT_CONFIG; print(DEFAULT_CONFIG.USE_ADAPTIVE_RSI, DEFAULT_CONFIG.CALL_PCT_MAX)"` should print `False 0.95`

### Files Changed
- `paper_trading/config.py` — `USE_ADAPTIVE_RSI = False`, added `USE_CALL_PCT_FILTER` + `CALL_PCT_MAX`
- `paper_trading/signal_filter.py` — Added `call_pct` to filter_reasons dict, added call_pct gate in `apply()` after notional check

---

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

## Filter Chain Reference (as of v57)

The signal filter applies these **11 checks** in `apply()`:

| # | Filter | Threshold | Rejection Tag | Source |
|---|--------|-----------|---------------|--------|
| 1 | ETF exclusion | Hardcoded list (55 ETFs) | `etf` | Hardcoded |
| 2 | Score threshold | >= 10 | `score` | Signal |
| 3 | Uptrend SMA20 | price > SMA20 (trend = 1) | `trend` | TA cache |
| 4 | RSI filter | < 55 (S5; adaptive bounce disabled) | `rsi` | `ta_daily_close` / `ta_snapshots_v2` |
| 5 | SMA50 momentum | price > SMA50 | `sma50` | `ta_daily_close` |
| 6 | Notional baseline | >= $50K per-symbol baseline | `notional` | `intraday_baselines_30m` |
| 7 | ADV filter (v57) | >= 1,000 contracts/day (D-1) | `adv_below_1000` | `orats_daily` bulk cache |
| 8 | Call% gate (S4) | <= 95% — **DISABLED** (`USE_CALL_PCT_FILTER=False`) | `call_pct` | Signal |
| 9 | GEX dead zone (S5+GEX) | Skip 2-5% above gamma flip | `gex_dead_zone` | `gex_metrics_snapshot` cache |
| 10 | Sentiment filter | mentions < 5 AND sentiment >= 0 | `sentiment_mentions` / `sentiment_negative` | `vw_media_daily_features` (T-1) |
| 11 | Earnings proximity | no earnings within ±2 days | `earnings` | `earnings_calendar` cache |

**Exit rules (outside `apply()`):**
- **Hard stop**: -5% (v57), checked every 30s via Alpaca positions API + Alpaca SIP WebSocket (real-time)
- **EOD exit**: 3:55 PM ET, all positions liquidated
- **Market regime**: pause new entries if SPY < -0.5% from open (checked in PositionManager, not apply())

---

## Database Tables (as of v57)

### Written by Live Trading
| Table | Purpose | Write Point |
|-------|---------|-------------|
| `active_signals` | Signals that passed all filters | INSERT on pass, UPDATE on trade/close |
| `paper_trades_log` | Account A trades (open/close with DB IDs) | `log_trade_open()` / `log_trade_close()` |
| `paper_trades_log_b` | Account B trades (same schema, separate sequence) | Same as above, Account B path |
| `signal_evaluations` | All evaluations with pass/fail + `metadata` JSONB | Every signal (passed or rejected) |
| `tracked_tickers_v2` | Symbols added for intraday TA updates | Every UOA trigger |
| `intraday_baselines_30m` | Live bucket aggregation from firehose | BucketAggregator flush at 30-min boundary |
| `spot_prices_1m` | 1-min OHLCV bars for ~1,800 symbols (SIP, 60s cycle, 21-day retention) | IntradayBarCollector |

### Written by ORATS Ingest (nightly)
| Table | Purpose | Volume |
|-------|---------|--------|
| `orats_daily` | Symbol-level options activity | ~5,831 rows/day |
| `gex_metrics_snapshot` | Per-symbol GEX/DEX/walls/gamma flip | ~5,570 rows/day, 2.85M total |

### Read by Live Trading
| Table/View | Purpose | Loaded As |
|------------|---------|-----------|
| `orats_daily` | ADV filter — D-1 avg_daily_volume (v57) | Bulk cache at startup (`_load_adv_cache`, 7,391 symbols) |
| `gex_metrics_snapshot` | GEX dead zone filter + shadow metadata | Bulk cache at startup (`_load_gex_cache`, ~5,500 symbols) |
| `ta_daily_close` | Prior-day RSI, SMA20, SMA50 (before 9:35 AM) | Pre-loaded by premarket_ta_cache |
| `ta_snapshots_v2` | Intraday RSI/SMA20 (after 9:35 AM, 5-min refresh) | Lazy refresh every 5 min |
| `vw_media_daily_features` | Crowded trade + sentiment filter (T-1) | Per-query (psycopg2) |
| `master_tickers` | Sector lookup for concentration limit | Bulk cache at startup (`_load_sector_cache`) |
| `earnings_calendar` | Earnings proximity filter (±2 days) | Bulk cache at startup (`_load_earnings_cache`) |
| `intraday_baselines_30m` | Per-symbol notional baselines | Bulk load at startup (`load_baselines`) |
| `engulfing_scores` | Account B engulfing watchlist (daily + 5-min) | Daily watchlist cache + per-query fallback |

## [2026-02-23 11:30 PST] — DuckDB Backtest Infrastructure + S4 Signal Quality Analysis

### Done
- **Signal quality analysis** on 16-day live data (6,079 signals, Jan 30 – Feb 23):
  - Group A (pass S4: RSI<50, call_pct≤95%): Intraday +0.15%, 51.7% WR, Sharpe +0.97. Reverses hard by D+5 (-2.43%). EOD exit confirmed correct.
  - Group B (RSI<50, call_pct>95% — currently rejected by S4 call_pct gate): Intraday -0.50% but D+1 +1.39% mean, 57.0% WR, PF 2.85, Sharpe +2.64. Appears to be institutional accumulation via pure-call sweeps with multi-day follow-through.
  - RSI 50-55 band (just above S4 cutoff) has best sustained multi-day returns across all horizons — current RSI cutoff is wrong if extending to multi-day holds.
  - Group A is correctly configured for intraday. Group B is a buried multi-day opportunity requiring separate account + exit rules.
  - Validation concerns noted: need deduplication, outlier check (Group B max D+1 gain = +71.58%), sector concentration analysis, 3-year confirmation.
- **Phase 0 data exploration** for 3-year backtest:
  - Signal JSON (D: drive): 2.3M signals, 9,746 score≥10, has call_pct + trend — confirmed NO RSI
  - `orats_daily`: full 3-year coverage, has stock_price + price_momentum_20d — confirmed NO RSI, NO SMA50
  - `orats_daily_returns`: full 3-year coverage, join on ticker+trade_date (not symbol/asof_date), returns are decimals
  - `ta_daily_close`: only Jul 2025+ — only 7 months, not 3 years
  - RSI must be computed from `orats_daily.stock_price` rolling 14-day for 2023–mid-2025
  - Trend proxy: `price_momentum_20d > 0` already exists in orats_daily — no recompute needed
  - Polygon stock bars: 780 files, 15 GB, full 3 years available locally
- **DuckDB adopted as core backtest engine**:
  - Reads CSV.gz flat files natively (no unzip), parallelizes across all CPU cores
  - Estimated 20-40 min for full 3-year scan vs 4+ hours sequential Python
  - Persistent cache at `D:\backtest_cache\` (DuckDB + Parquet) — build once, query in seconds
  - Auto-detects Cloud SQL proxy on port 5433; falls back to Polygon stock bars if unavailable
  - All future backtest hypotheses = WHERE clause changes, not re-processing
- **Backtest plan v2** written: `temp/BACKTEST_PLAN_S4_MULTIDAY_v2.md`
  - Full CLI implementation instructions with `--phase` argument
  - Phase 1: Build DuckDB cache (RSI/SMA computation, signal enrichment, deduplication, Parquet export)
  - Phase 2: Intraday return computation (fast proxy or full 1-min bars via DuckDB)
  - Phase 3: Portfolio simulation (Strategy A intraday + Strategy B multi-day Group B hypothesis)
  - Phase 4: Analysis queries (6 DuckDB SQL queries covering group comparison, RSI sensitivity, year-by-year, outlier check, regime breakdown, sector concentration)
  - Phase 5: 11-tab Excel output
- **Bible and CLAUDE.md updated** with DuckDB infrastructure, Phase 0 findings, signal quality results

### State
- DuckDB not yet installed. Plan ready for CLI execution.
- Next step: `pip install duckdb pyarrow fastparquet` then `python temp/run_s4_backtest.py --phase=build`
- Group B multi-day hypothesis requires 3-year validation before any live strategy changes

### Next
- Install DuckDB and run `--phase=build` to create enriched signal cache (~20 min)
- Run Phase 4 analysis queries to validate Group B edge over 3 years
- Check for outlier concentration in Group B (top-5 trades as % of total return)
- Confirm year-by-year consistency (2023/2024/2025 separately)
- If validated: design Group B multi-day account (separate Alpaca account, D+1 to D+5 exits, -5% stop)

### Files Changed
- `temp/BACKTEST_PLAN_S4_MULTIDAY_v2.md` (new)
- `temp/analyze_signal_quality.py` (new — 16-day signal quality analysis)
- `temp/signal_quality_report.xlsx` (new — 6-tab Excel output)
- `CLAUDE.md` (updated — DuckDB backtest section, Current Status)
- `C:\Users\levir\Documents\FL3_ECOSYSTEM_BIBLE.md` (updated — v2.6, backtest infrastructure section)


## [2026-02-23 11:45 PST] — S5: RSI<55 + Remove call_pct Gate

### Done
- **Raised RSI_THRESHOLD from 50 → 55** (`paper_trading/config.py`):
  - 3-year year-by-year comparison (with -2% hard stop, SPY filter, sector cap):
    - RSI<50: Sharpe 3.68→1.79→1.55→-5.50 — degrading YoY, weak in recent periods
    - RSI<55: Sharpe 1.59→3.23→3.27→5.38 — improving YoY, strengthening trajectory
    - RSI<60: Sharpe 2.39→2.41→2.60→3.66 — flat/consistent, less sharp
  - RSI<55 selected: improving edge means the signal type is becoming more relevant, not less
- **Disabled call_pct gate** (`USE_CALL_PCT_FILTER = False`):
  - S4's `CALL_PCT_MAX = 0.95` passed only 34/7,985 score>=10 signals (0.4%) over 3 years
  - Not a discriminating filter — a near-total block
  - Root cause of S4 underperformance identified: gate was correct that call_pct>95% signals
    are poor intraday, but wrong conclusion — they're a different signal type (Group B multi-day),
    not inherently bad signals
- **No signal_filter.py code changes needed** — RSI check already uses `RSI_THRESHOLD` from
  config; call_pct gate already gated on `USE_CALL_PCT_FILTER` flag (both wired correctly in S4)
- **Updated signal_filter.py docstring** to reflect S5 filter rules
- **Smoke test passed**: `RSI_THRESHOLD=55.0, USE_CALL_PCT_FILTER=False` confirmed
- **Decisions documented**: `temp/S5_DEPLOY_DECISIONS.md`
- **Full simulation report**: `D:\backtest_cache\S5_Full_Sim_Report.xlsx` (10 tabs)

### Expected Live Performance (S5)
| Metric | Value |
|--------|-------|
| Trades/month | ~46 |
| Annual trades | ~350-400 |
| 3yr Sharpe | 2.97 |
| 3yr Win Rate | 51.0% |
| Mean return/trade | +0.55% |
| 2023 Sharpe | 1.59 (weakest year — choppy macro) |
| 2024 Sharpe | 3.23 |
| 2025 Sharpe | 3.27 |

### State
- Config changes committed, ready for Docker build + Cloud Run deploy
- v54d still running in prod (`paper-trading-live-00098-twj`) — S4 config
- Group B multi-day strategy (D+3, RSI<50, call_pct>0.95, Sharpe 3.42 ex-REAL) validated
  but deferred — requires separate Alpaca account

### Next
- Docker build → push → deploy to Cloud Run (new revision)
- Monitor first live session: target WR >49%, mean >+0.35%, ~8-12 trades/day
- If 2023-like regime returns (choppy, SPY downtrend), consider tightening to RSI<50
- Group B multi-day: set up separate Alpaca account, D+3 exit, -5% stop, max 5 positions

### Files Changed
- `paper_trading/config.py` — RSI_THRESHOLD 50→55, USE_CALL_PCT_FILTER True→False, updated comments
- `paper_trading/signal_filter.py` — docstring updated to reflect S5 rules
- `temp/S5_DEPLOY_DECISIONS.md` (new — full decision rationale + deploy checklist)
- `temp/run_s5_full_sim.py` (new — targeted simulation script)
- `D:\backtest_cache\S5_Full_Sim_Report.xlsx` (new — 10-tab simulation report)
- `CLAUDE.md` — Current Status updated

---

## [2026-02-23 14:44 PST] — v55 Deployed: S5 Filter Live

### Done
- **Docker build + push + deploy** of `paper-trading:v55` to Cloud Run
  - Revision: `paper-trading-live-00100-7tl`, serving 100% traffic
  - Docker context: 547 KB (well under 1 MB limit)
- **S5 filter now live**: RSI threshold 55 (up from 50), call_pct gate disabled
- **Startup verified**: 10/10 positions restored (ARE, NRIX, BHF, BBBY, NTAP, DNA, BUG, CTRI, BJ, EQH)
- **All subsystems healthy**: Polygon firehose, Alpaca SIP WebSocket, bar collector (1,984 bars/cycle), TA cache (2,671 symbols), engulfing watchlist (50 symbols)
- **No errors**: One normal Polygon WS ping timeout, auto-reconnected in 400ms

### State
- v55 live in prod, S5 filter active
- Replaces v54d (`paper-trading-live-00098-twj`)

### Files Changed
- `paper_trading/config.py` — RSI_THRESHOLD 50->55, USE_CALL_PCT_FILTER True->False
- `paper_trading/signal_filter.py` — docstring updated

## [2026-02-23 12:55 PST] — S5+GEX: Dead Zone Filter

### Done
- **Added GEX dead zone filter** (`USE_GEX_DEAD_ZONE_FILTER = True`):
  - Skips signals where spot price is 2-5% above gamma flip level
  - Rationale: in this band dealers are marginally long gamma — neither the amplification
    of short gamma (below flip) nor the stabilizing floor of deep long gamma. The band
    has structurally weak intraday dynamics for UOA signals.
  - 3-year backtest result: Sharpe 0.91 in dead zone vs 2.54 baseline
  - Combined filter (S5 + skip dead zone): Sharpe 3.30 → 3.50, mean +0.655% → +0.737%
  - Cost: ~17% fewer trades (~38/mo vs ~46/mo). Consistent improvement 2024, 2025, 2026.
- **No new DB queries at runtime**: GEX cache already loaded once at startup via
  `_load_gex_cache()`. Dead zone check is a pure dict lookup + arithmetic.
- Added `gex_dead_zone` to `filter_reasons` counter
- Updated signal_filter.py docstring

### Config Changes (`paper_trading/config.py`)
```python
USE_GEX_DEAD_ZONE_FILTER: bool = True
GEX_DEAD_ZONE_MIN_PCT: float = 2.0
GEX_DEAD_ZONE_MAX_PCT: float = 5.0
```

### Deferred GEX Filters (need more data)
- `S5_combined`: also skip `near_call_wall` zone — D+3 uplift real but 2026 Sharpe drops.
  Revisit after 6 months live data.
- `S5_below_flip_only`: Sharpe 4.06 but only ~7 trades/month — too thin for live.

### State
- Ready for Docker build + Cloud Run deploy (combine with S5 deploy)
- Smoke test passed: all 5 config assertions green, `gex_dead_zone` in filter_reasons

### Files Changed
- `paper_trading/config.py` — 3 new GEX config fields
- `paper_trading/signal_filter.py` — dead zone check added after call_pct block,
  gex_dead_zone added to filter_reasons, docstring updated
- `temp/run_gex_analysis.py` (new) — full GEX hypothesis analysis script
- `temp/_gex_combined_test.py` (new) — combined filter comparison script
- `D:\backtest_cache\GEX_Analysis_Report.xlsx` (new) — 13-tab GEX analysis report
- `D:\backtest_cache\signals_gex_enriched.parquet` (new) — GEX-enriched signal cache

---

## [2026-02-23 19:54 PST] — v56 Deployed: S5 + GEX Dead Zone Filter

### Done
- **Docker build + push + deploy** of `paper-trading:v56` to Cloud Run
  - Revision: `paper-trading-live-00101-htx`, serving 100% traffic
  - Includes both S5 (RSI<55, no call_pct gate) and GEX dead zone filter
- **v56 startup verified**: zero errors, RSI < 55.0 confirmed in logs, TA cache loaded (2,612 symbols)
- Replaces v55 (`paper-trading-live-00100-7tl`) which had S5 but not GEX filter

### Live Config Summary (v56)
| Filter | Setting | Backtest Impact |
|--------|---------|-----------------|
| RSI threshold | < 55 | Sharpe improving YoY: 1.59/3.23/3.27 |
| call_pct gate | DISABLED | Was blocking 99.6% of signals |
| GEX dead zone | 2-5% above flip | Sharpe 3.30 -> 3.50, -17% volume |
| Hard stop | -2% | Unchanged |
| Max positions | 10 | Unchanged |

### Files Changed
- `paper_trading/config.py` — GEX dead zone config (3 new fields)
- `paper_trading/signal_filter.py` — dead zone check, gex_dead_zone counter, docstring
- `CHANGELOG.md` — v56 deploy entry
