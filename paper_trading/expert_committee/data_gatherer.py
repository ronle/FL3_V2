"""
Data Gatherer — Collects market context for each expert domain.

This is the Python plumbing that queries DB tables and assembles
structured data payloads for the AI expert agents. Each method
returns a formatted string ready to inject into an expert's prompt.
"""

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import psycopg2
import requests

logger = logging.getLogger(__name__)

ALPACA_BASE = "https://paper-api.alpaca.markets"


class DataGatherer:
    """Gather domain-specific data from DB for expert prompts."""

    def __init__(self, db_url: str):
        self._db_url = db_url.strip()
        self._alpaca_key = os.environ.get("ALPACA_API_KEY_E", "")
        self._alpaca_secret = os.environ.get("ALPACA_SECRET_KEY_E", "")

    def _query(self, sql: str, params=None) -> list[dict]:
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params or ())
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[DataGatherer] Query failed: {e}")
            return []

    def _query_one(self, sql: str, params=None) -> Optional[dict]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Flow Analyst context
    # ------------------------------------------------------------------

    def gather_flow_context(self) -> str:
        """Hot options, GEX snapshots, flow signals for today."""
        triggers = self._query("""
            SELECT symbol, detected_at AS trigger_ts, volume_ratio, notional,
                   contracts, call_volume, put_volume,
                   top_contract, top_contract_volume,
                   CASE
                       WHEN put_volume > call_volume * 2 THEN 'bearish'
                       WHEN call_volume > put_volume * 2 THEN 'bullish'
                       ELSE 'neutral'
                   END AS signal_direction
            FROM hot_options
            WHERE detected_at > NOW() - INTERVAL '30 minutes'
              AND volume_ratio >= 5
            ORDER BY volume_ratio DESC
            LIMIT 25
        """)

        flow_signals = self._query("""
            SELECT symbol, direction, iv_rank, volume_zscore,
                   put_call_ratio, flow_aligned, pattern_date
            FROM flow_signals
            WHERE pattern_date = CURRENT_DATE
              AND flow_aligned = TRUE
            ORDER BY volume_zscore DESC
            LIMIT 20
        """)

        gex = self._query("""
            SELECT DISTINCT ON (symbol)
                symbol, net_gex, net_dex, gamma_flip_level, spot_price,
                call_wall_strike, put_wall_strike, snapshot_ts
            FROM gex_metrics_snapshot
            WHERE snapshot_ts > NOW() - INTERVAL '1 hour'
            ORDER BY symbol, snapshot_ts DESC
            LIMIT 20
        """)

        lines = ["## FLOW ANALYST DATA CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        lines.append(f"### Hot Options (last 30 min, vol_ratio >= 5x): {len(triggers)} hits\n")
        if triggers:
            lines.append("| Symbol | Vol Ratio | Notional | Direction | Calls | Puts | Contracts | Top Contract |")
            lines.append("|--------|-----------|----------|-----------|-------|------|-----------|-------------|")
            for t in triggers:
                lines.append(
                    f"| {t['symbol']} | {float(t['volume_ratio']):.0f}x | "
                    f"${float(t['notional']):,.0f} | {t['signal_direction']} | "
                    f"{t['call_volume']} | {t['put_volume']} | {t['contracts']} | "
                    f"{t.get('top_contract', '')} |"
                )
        else:
            lines.append("*No hot options in last 30 minutes.*\n")

        lines.append(f"\n### Flow Signals (today): {len(flow_signals)} signals\n")
        for fs in flow_signals:
            lines.append(
                f"- **{fs['symbol']}** {fs['direction']} | vol_z={float(fs['volume_zscore'] or 0):.1f} "
                f"iv_rank={float(fs['iv_rank'] or 0):.0f} pc_ratio={float(fs['put_call_ratio'] or 0):.2f}"
            )

        lines.append(f"\n### GEX Snapshots: {len(gex)} symbols\n")
        for g in gex:
            lines.append(
                f"- **{g['symbol']}**: net_gex={float(g['net_gex'] or 0):,.0f}, "
                f"gamma_flip={float(g['gamma_flip_level'] or 0):.2f}, "
                f"spot={float(g['spot_price'] or 0):.2f}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Technical Analyst context
    # ------------------------------------------------------------------

    def gather_ta_context(self) -> str:
        """Intraday TA snapshots, daily TA, engulfing patterns.

        Gap fixes:
        - Gap 1: Target flow/engulfing symbols instead of alphabetical LIMIT 25
        - Gap 5: Fallback to ta_daily_close when intraday snapshots are empty (after hours)
        """
        # Gap 1 fix: target symbols from today's flow + recent engulfing patterns
        ta_intraday = self._query("""
            WITH target_symbols AS (
                SELECT DISTINCT symbol FROM flow_signals
                WHERE pattern_date = CURRENT_DATE
                UNION
                SELECT DISTINCT symbol FROM engulfing_scores
                WHERE scan_ts > NOW() - INTERVAL '30 minutes'
                  AND timeframe = '5min' AND volume_confirmed = TRUE
            )
            SELECT DISTINCT ON (ts.symbol)
                ts.symbol, ts.price, ts.rsi_14, ts.sma_20, ts.ema_9, ts.vwap,
                ts.atr_14, ts.snapshot_ts
            FROM ta_snapshots_v2 ts
            JOIN target_symbols t ON t.symbol = ts.symbol
            WHERE ts.snapshot_ts > NOW() - INTERVAL '10 minutes'
              AND ts.rsi_14 IS NOT NULL
            ORDER BY ts.symbol, ts.snapshot_ts DESC
        """)

        # Gap 5 fix: fallback to ta_daily_close if intraday is empty (after hours)
        if not ta_intraday:
            ta_intraday = self._query("""
                WITH target_symbols AS (
                    SELECT DISTINCT symbol FROM flow_signals
                    WHERE pattern_date = CURRENT_DATE
                    UNION
                    SELECT DISTINCT symbol FROM engulfing_scores
                    WHERE scan_ts > NOW() - INTERVAL '24 hours'
                      AND timeframe = '5min' AND volume_confirmed = TRUE
                )
                SELECT DISTINCT ON (tdc.symbol)
                    tdc.symbol, tdc.close_price AS price, tdc.rsi_14,
                    tdc.sma_20, tdc.ema_9, NULL::numeric AS vwap,
                    NULL::numeric AS atr_14, tdc.trade_date::text AS snapshot_ts
                FROM ta_daily_close tdc
                JOIN target_symbols t ON t.symbol = tdc.symbol
                ORDER BY tdc.symbol, tdc.trade_date DESC
            """)

        ta_daily = self._query("""
            WITH target_symbols AS (
                SELECT DISTINCT symbol FROM flow_signals
                WHERE pattern_date = CURRENT_DATE
                UNION
                SELECT DISTINCT symbol FROM engulfing_scores
                WHERE scan_ts > NOW() - INTERVAL '30 minutes'
                  AND timeframe = '5min' AND volume_confirmed = TRUE
            )
            SELECT DISTINCT ON (tdc.symbol)
                tdc.symbol, tdc.close_price, tdc.rsi_14, tdc.macd,
                tdc.macd_signal, tdc.macd_histogram,
                tdc.sma_20, tdc.sma_50, tdc.ema_9, tdc.trade_date
            FROM ta_daily_close tdc
            JOIN target_symbols t ON t.symbol = tdc.symbol
            ORDER BY tdc.symbol, tdc.trade_date DESC
        """)

        engulfing = self._query("""
            SELECT symbol, direction, score, pattern_strength, entry_price,
                   stop_loss, target_1, candle_range, volume_confirmed, scan_ts
            FROM engulfing_scores
            WHERE scan_ts > NOW() - INTERVAL '30 minutes'
              AND timeframe = '5min'
              AND volume_confirmed = TRUE
              AND pattern_strength != 'weak'
            ORDER BY score DESC
            LIMIT 20
        """)

        lines = ["## TECHNICAL ANALYST DATA CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        lines.append(f"### Intraday TA (latest 5-min snapshot): {len(ta_intraday)} symbols\n")
        if ta_intraday:
            lines.append("| Symbol | Price | RSI | SMA20 | SMA50 | EMA9 | VWAP | ATR |")
            lines.append("|--------|-------|-----|-------|-------|------|------|-----|")
            for t in ta_intraday[:30]:
                daily = next((d for d in ta_daily if d['symbol'] == t['symbol']), {})
                lines.append(
                    f"| {t['symbol']} | {float(t['price'] or 0):.2f} | "
                    f"{float(t['rsi_14'] or 0):.0f} | {float(t['sma_20'] or 0):.2f} | "
                    f"{float(daily.get('sma_50') or 0):.2f} | "
                    f"{float(t['ema_9'] or 0):.2f} | {float(t['vwap'] or 0):.2f} | "
                    f"{float(t['atr_14'] or 0):.2f} |"
                )

        lines.append(f"\n### Engulfing Patterns (last 30 min): {len(engulfing)} patterns\n")
        for e in engulfing:
            lines.append(
                f"- **{e['symbol']}** {e['direction']} | strength={e['pattern_strength']} | "
                f"entry={float(e['entry_price'] or 0):.2f} stop={float(e['stop_loss'] or 0):.2f} "
                f"target={float(e['target_1'] or 0):.2f}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Sentiment Analyst context
    # ------------------------------------------------------------------

    def gather_sentiment_context(self) -> str:
        """Sentiment data from precomputed views + filtered Discord mentions.

        Gap fixes:
        - Gap 2: Discord mentions now JOIN master_tickers to filter out English words
        - Gap 3: Uses sentiment_daily (rich precomputed view) instead of raw 3-table join
        - Added discord_sentiment_hourly for real-time social signal
        """
        # Gap 3 fix: use sentiment_daily (precomputed, has sentiment_index, momentum)
        sentiment = self._query("""
            SELECT ticker AS symbol, sentiment_index, mentions_total,
                   mentions_mom_1d, mentions_mom_3d,
                   pos_score, neg_score, doc_count_media, doc_count_social
            FROM sentiment_daily
            WHERE asof_date = (SELECT MAX(asof_date) FROM sentiment_daily)
            ORDER BY ABS(sentiment_index) DESC
            LIMIT 25
        """)

        # Gap 2 fix: filter discord_mentions through master_tickers
        discord = self._query("""
            SELECT dm.symbol, SUM(dm.mention_count) AS mentions
            FROM discord_mentions dm
            JOIN master_tickers mt ON mt.symbol = dm.symbol
            WHERE dm.mention_date >= CURRENT_DATE - 1
            GROUP BY dm.symbol
            HAVING SUM(dm.mention_count) >= 3
            ORDER BY SUM(dm.mention_count) DESC
            LIMIT 25
        """)

        # Real-time social sentiment from discord
        discord_sentiment = self._query("""
            SELECT symbol, sentiment, sentiment_score, confidence, message_count
            FROM discord_sentiment_hourly
            WHERE sentiment_date = CURRENT_DATE
              AND message_count >= 3
            ORDER BY message_count DESC
            LIMIT 15
        """)

        lines = ["## SENTIMENT ANALYST DATA CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        lines.append(f"### Sentiment Daily (latest): {len(sentiment)} symbols\n")
        if sentiment:
            lines.append("| Symbol | Sentiment Index | Mentions | Mom 1d | Mom 3d | Media | Social |")
            lines.append("|--------|----------------|----------|--------|--------|-------|--------|")
            for s in sentiment:
                lines.append(
                    f"| {s['symbol']} | {float(s['sentiment_index'] or 0):.3f} | "
                    f"{s['mentions_total'] or 0} | {float(s['mentions_mom_1d'] or 0):+.1f} | "
                    f"{float(s['mentions_mom_3d'] or 0):+.1f} | "
                    f"{s['doc_count_media'] or 0} | {s['doc_count_social'] or 0} |"
                )

        lines.append(f"\n### Discord Mentions (filtered, last 2 days): {len(discord)} symbols\n")
        for d in discord:
            lines.append(f"- **{d['symbol']}**: {d['mentions']} mentions")

        lines.append(f"\n### Discord Sentiment (today, real-time): {len(discord_sentiment)} symbols\n")
        for ds in discord_sentiment:
            lines.append(
                f"- **{ds['symbol']}**: {ds['sentiment']} (score={float(ds['sentiment_score'] or 0):.2f}, "
                f"conf={float(ds['confidence'] or 0):.2f}, msgs={ds['message_count']})"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Macro Strategist context
    # ------------------------------------------------------------------

    def gather_macro_context(self) -> str:
        """SPY/QQQ/VIX data, sector ETF momentum."""
        spy = self._query_one("""
            SELECT symbol, stock_price, price_momentum_20d, iv_rank,
                   iv_30day, asof_date
            FROM orats_daily
            WHERE symbol = 'SPY'
            ORDER BY asof_date DESC LIMIT 1
        """)

        qqq = self._query_one("""
            SELECT symbol, stock_price, price_momentum_20d, iv_rank
            FROM orats_daily
            WHERE symbol = 'QQQ'
            ORDER BY asof_date DESC LIMIT 1
        """)

        sector_etfs = self._query("""
            SELECT symbol, stock_price, price_momentum_20d, iv_rank
            FROM orats_daily
            WHERE symbol IN ('XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'IWM')
              AND asof_date = (SELECT MAX(asof_date) FROM orats_daily WHERE symbol = 'SPY')
            ORDER BY price_momentum_20d DESC
        """)

        lines = ["## MACRO STRATEGIST DATA CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        if spy:
            lines.append(f"### SPY (as of {spy['asof_date']})")
            lines.append(f"- Price: ${float(spy['stock_price']):.2f}")
            lines.append(f"- 20d Momentum: {float(spy['price_momentum_20d'] or 0):.1%}")
            lines.append(f"- IV Rank: {float(spy['iv_rank'] or 0):.0f}")
            lines.append(f"- IV 30d: {float(spy['iv_30day'] or 0):.1%}")

        if qqq:
            lines.append(f"\n### QQQ")
            lines.append(f"- Price: ${float(qqq['stock_price']):.2f}")
            lines.append(f"- 20d Momentum: {float(qqq['price_momentum_20d'] or 0):.1%}")
            lines.append(f"- IV Rank: {float(qqq['iv_rank'] or 0):.0f}")

        lines.append(f"\n### Sector ETFs (by momentum):\n")
        lines.append("| ETF | Price | 20d Momentum | IV Rank |")
        lines.append("|-----|-------|-------------|---------|")
        for s in sector_etfs:
            lines.append(
                f"| {s['symbol']} | ${float(s['stock_price']):.2f} | "
                f"{float(s['price_momentum_20d'] or 0):.1%} | "
                f"{float(s['iv_rank'] or 0):.0f} |"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Alpha Scorecard (portfolio vs SPY)
    # ------------------------------------------------------------------

    def _fetch_alpaca_equity_series(self) -> Optional[dict]:
        """Fetch daily equity timeseries from Alpaca portfolio_history.

        Returns dict mapping ISO date string -> equity float, plus 'base_value'
        and 'current_equity' keys. Returns None if creds missing or API fails.
        """
        if not self._alpaca_key or not self._alpaca_secret:
            return None
        try:
            r = requests.get(
                f"{ALPACA_BASE}/v2/account/portfolio/history",
                headers={
                    "APCA-API-KEY-ID": self._alpaca_key,
                    "APCA-API-SECRET-KEY": self._alpaca_secret,
                },
                params={"period": "1M", "timeframe": "1D", "extended_hours": "false"},
                timeout=10,
            )
            if not r.ok:
                logger.warning(f"[alpha_scorecard] portfolio_history HTTP {r.status_code}")
                return None
            data = r.json()
            timestamps = data.get("timestamp", []) or []
            equity = data.get("equity", []) or []
            if not timestamps or not equity or len(timestamps) != len(equity):
                return None
            by_date: dict[str, float] = {}
            for ts, eq in zip(timestamps, equity):
                if eq is None:
                    continue
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                by_date[d] = float(eq)
            # Also fetch current equity (intraday, not yet in daily history)
            try:
                acct = requests.get(
                    f"{ALPACA_BASE}/v2/account",
                    headers={
                        "APCA-API-KEY-ID": self._alpaca_key,
                        "APCA-API-SECRET-KEY": self._alpaca_secret,
                    },
                    timeout=5,
                )
                current_equity = float(acct.json().get("equity", 0)) if acct.ok else 0.0
            except Exception:
                current_equity = 0.0
            return {
                "by_date": by_date,
                "base_value": float(data.get("base_value") or 100000.0),
                "current_equity": current_equity or (equity[-1] if equity else 100000.0),
            }
        except Exception as e:
            logger.warning(f"[alpha_scorecard] fetch failed: {e}")
            return None

    def _fetch_spy_series(self, start_date: date) -> dict[str, float]:
        """Fetch SPY daily close prices from orats_daily (keyed by ISO date)."""
        rows = self._query(
            """
            SELECT asof_date, stock_price
            FROM orats_daily
            WHERE symbol = 'SPY' AND asof_date >= %s
            ORDER BY asof_date ASC
            """,
            (start_date,),
        )
        return {r["asof_date"].isoformat(): float(r["stock_price"]) for r in rows if r.get("stock_price") is not None}

    def _fetch_spy_live(self) -> Optional[float]:
        """Fetch current intraday SPY price from vw_spot_prices_latest."""
        row = self._query_one("SELECT underlying FROM vw_spot_prices_latest WHERE ticker = 'SPY'")
        if row and row.get("underlying") is not None:
            return float(row["underlying"])
        return None

    def gather_alpha_scorecard(self) -> str:
        """Build the Alpha Scorecard (portfolio return vs SPY) markdown block."""
        equity_data = self._fetch_alpaca_equity_series()
        if not equity_data:
            return (
                "### Alpha Scorecard (vs SPY)\n"
                "*Unavailable — Alpaca creds missing or portfolio_history API failed. "
                "Focus on absolute profit this cycle; alpha tracking will resume when data returns.*\n"
            )

        equity_by_date = equity_data["by_date"]
        current_equity = equity_data["current_equity"]
        if not equity_by_date:
            return "### Alpha Scorecard (vs SPY)\n*No equity history available yet.*\n"

        sorted_dates = sorted(equity_by_date.keys())
        earliest = date.fromisoformat(sorted_dates[0])
        spy_by_date = self._fetch_spy_series(earliest - timedelta(days=5))
        if not spy_by_date:
            return "### Alpha Scorecard (vs SPY)\n*SPY price history unavailable in orats_daily.*\n"

        spy_sorted = sorted(spy_by_date.keys())
        spy_live = self._fetch_spy_live()

        def _eq_on_or_before(target_date: date) -> Optional[float]:
            """Return the most recent portfolio equity snapshot on or before target_date."""
            candidate = None
            for d in sorted_dates:
                if d <= target_date.isoformat():
                    candidate = equity_by_date[d]
                else:
                    break
            return candidate

        def _spy_on_or_before(target_date: date) -> Optional[float]:
            candidate = None
            for d in spy_sorted:
                if d <= target_date.isoformat():
                    candidate = spy_by_date[d]
                else:
                    break
            return candidate

        today = datetime.now(timezone.utc).date()
        spy_today = spy_live if spy_live is not None else _spy_on_or_before(today)

        periods = [
            ("Today",         today - timedelta(days=1)),
            ("Trailing 5d",   today - timedelta(days=5)),
            ("Trailing 20d",  today - timedelta(days=20)),
            ("MTD",           date(today.year, today.month, 1) - timedelta(days=1)),
        ]

        lines = ["### Alpha Scorecard (Account E vs SPY)"]
        lines.append("| Period | Portfolio | SPY | Alpha |")
        lines.append("|--------|-----------|-----|-------|")
        for label, ref_date in periods:
            eq_start = _eq_on_or_before(ref_date)
            spy_start = _spy_on_or_before(ref_date)
            if eq_start is None or spy_start is None or spy_today is None or eq_start == 0:
                lines.append(f"| {label} | n/a | n/a | n/a |")
                continue
            port_ret = (current_equity - eq_start) / eq_start
            spy_ret = (spy_today - spy_start) / spy_start
            alpha = port_ret - spy_ret
            marker = "  <— NEGATIVE" if alpha < 0 else ""
            lines.append(
                f"| {label} | {port_ret:+.2%} | {spy_ret:+.2%} | {alpha:+.2%}{marker} |"
            )

        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Risk Manager context
    # ------------------------------------------------------------------

    def gather_risk_context(self) -> str:
        """Open positions with current prices & unrealized P&L, plus closed trades."""
        positions = self._query("""
            SELECT symbol, direction, instrument, holding_period,
                   entry_price, shares, entry_date, stop_price, target_price,
                   weighted_score, expert_votes,
                   EXTRACT(DAY FROM now() - entry_time) AS days_held
            FROM paper_trades_log_e
            WHERE exit_time IS NULL
            ORDER BY entry_time DESC
        """)

        # Fetch current spot prices for open positions from the latest-price view
        spot_prices = {}
        if positions:
            syms = [p["symbol"] for p in positions]
            placeholders = ",".join(["%s"] * len(syms))
            spots = self._query(
                f"SELECT ticker, underlying FROM vw_spot_prices_latest WHERE ticker IN ({placeholders})",
                syms,
            )
            spot_prices = {s["ticker"]: float(s["underlying"]) for s in spots}

        closed_today = self._query("""
            SELECT symbol, direction, pnl, pnl_pct, exit_reason,
                   entry_price, exit_price, actual_holding_days
            FROM paper_trades_log_e
            WHERE exit_time IS NOT NULL
              AND entry_date = CURRENT_DATE
            ORDER BY exit_time DESC
        """)

        recent_failures = self._query("""
            SELECT symbol, direction, decision_ts, execution_notes
            FROM pm_decisions_e
            WHERE executed = TRUE
              AND execution_notes LIKE %s
              AND decision_ts > NOW() - INTERVAL '48 hours'
            ORDER BY decision_ts DESC
            LIMIT 20
        """, ("FAILED:%",))

        total_stats = self._query_one("""
            SELECT
                COUNT(*) AS total_trades,
                COUNT(*) FILTER (WHERE pnl > 0) AS wins,
                COUNT(*) FILTER (WHERE pnl <= 0) AS losses,
                COALESCE(SUM(pnl), 0) AS total_pnl,
                COALESCE(AVG(pnl_pct), 0) AS avg_pnl_pct
            FROM paper_trades_log_e
            WHERE exit_time IS NOT NULL
        """)

        lines = ["## PORTFOLIO CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        # Alpha scorecard — portfolio return vs SPY for today/5d/20d/MTD
        lines.append(self.gather_alpha_scorecard())

        lines.append(f"### Open Positions: {len(positions)} (review EACH for EXIT or hold)\n")
        if positions:
            lines.append("| Symbol | Dir | Period | Entry | Now | Unreal P&L | Days | Shares | Stop | Score |")
            lines.append("|--------|-----|--------|-------|-----|------------|------|--------|------|-------|")
            total_unrealized = 0.0
            for p in positions:
                entry = float(p["entry_price"])
                shares = p["shares"]
                current = spot_prices.get(p["symbol"], entry)
                days_held = int(p.get("days_held") or 0)

                if p["direction"] == "long":
                    pnl = (current - entry) * shares
                    pnl_pct = (current - entry) / entry if entry else 0
                else:
                    pnl = (entry - current) * shares
                    pnl_pct = (entry - current) / entry if entry else 0
                total_unrealized += pnl

                lines.append(
                    f"| {p['symbol']} | {p['direction']} | {p['holding_period']} | "
                    f"${entry:.2f} | ${current:.2f} | "
                    f"${pnl:+,.2f} ({pnl_pct:+.1%}) | {days_held}d | "
                    f"{shares} | ${float(p['stop_price'] or 0):.2f} | "
                    f"{float(p['weighted_score'] or 0):.0f} |"
                )
            lines.append(f"\n**Total unrealized P&L: ${total_unrealized:+,.2f}**")
        else:
            lines.append("*No open positions — full capital available for new entries.*\n")

        lines.append(f"\n### Closed Today: {len(closed_today)} trades\n")
        for c in closed_today:
            lines.append(
                f"- **{c['symbol']}** {c['direction']} | P&L: ${float(c['pnl'] or 0):+,.2f} "
                f"({float(c['pnl_pct'] or 0):+.1%}) | Reason: {c['exit_reason']}"
            )

        if recent_failures:
            lines.append(f"\n### Recent Order Failures (last 48h): {len(recent_failures)}\n")
            lines.append("*These decisions did NOT execute. Learn from them — avoid repeating.*\n")
            for f in recent_failures:
                ts = f["decision_ts"].strftime("%Y-%m-%d %H:%M") if f.get("decision_ts") else "?"
                notes = (f.get("execution_notes") or "").replace("FAILED: ", "")
                lines.append(
                    f"- **{f['symbol']}** {f['direction']} ({ts}) → {notes}"
                )

        if total_stats:
            lines.append(f"\n### Lifetime Stats:")
            lines.append(f"- Total trades: {total_stats['total_trades']}")
            lines.append(f"- Win rate: {total_stats['wins']}/{total_stats['total_trades']} "
                         f"({total_stats['wins']/max(total_stats['total_trades'],1)*100:.0f}%)")
            lines.append(f"- Total P&L: ${float(total_stats['total_pnl']):+,.2f}")
            lines.append(f"- Avg P&L%: {float(total_stats['avg_pnl_pct']):+.2%}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Quant Analyst context
    # ------------------------------------------------------------------

    def gather_quant_context(self, symbols: list[str] = None) -> str:
        """Historical returns, expert performance, signal accuracy.

        Gap fix:
        - Gap 4: Instead of depending on expert_signals_e (empty within same cycle),
          get today's active symbols from flow_signals + engulfing_scores directly.
        - Historical returns fetched in batch (not per-symbol loop).
        """
        # Gap 4 fix: get today's active symbols from actual data sources
        returns_data = self._query("""
            WITH today_symbols AS (
                SELECT DISTINCT symbol FROM flow_signals
                WHERE pattern_date = CURRENT_DATE
                UNION
                SELECT DISTINCT symbol FROM engulfing_scores
                WHERE scan_ts > NOW() - INTERVAL '60 minutes'
                  AND volume_confirmed = TRUE
            )
            SELECT r.ticker AS symbol, COUNT(*) AS sample_size,
                   AVG(r.r_p1) AS mean_d1, AVG(r.r_p5) AS mean_d5,
                   STDDEV(r.r_p1) AS std_d1,
                   AVG(CASE WHEN r.r_p1 > 0 THEN 1.0 ELSE 0.0 END) AS wr_d1
            FROM orats_daily_returns r
            JOIN (SELECT DISTINCT symbol FROM flow_signals WHERE pattern_date = CURRENT_DATE
                  UNION
                  SELECT DISTINCT symbol FROM engulfing_scores
                  WHERE scan_ts > NOW() - INTERVAL '60 minutes' AND volume_confirmed = TRUE
            ) ts ON ts.symbol = r.ticker
            GROUP BY r.ticker
        """)

        # Expert performance (may be empty during cold start — that's OK)
        expert_perf = self._query("""
            SELECT expert_id,
                   SUM(wins) AS wins, SUM(losses) AS losses,
                   COALESCE(SUM(total_pnl), 0) AS total_pnl,
                   AVG(trailing_sharpe) AS avg_sharpe
            FROM expert_performance_e
            WHERE trade_date > CURRENT_DATE - 30
            GROUP BY expert_id
        """)

        # Today's flow + engulfing activity for context
        today_activity = self._query("""
            SELECT symbol, direction, volume_zscore, iv_rank
            FROM flow_signals
            WHERE pattern_date = CURRENT_DATE AND flow_aligned = TRUE
            ORDER BY volume_zscore DESC
            LIMIT 15
        """)

        lines = ["## QUANT ANALYST DATA CONTEXT\n"]
        lines.append(f"**Timestamp:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

        lines.append(f"### Today's Active Symbols (flow + engulfing): {len(today_activity)}\n")
        for s in today_activity:
            lines.append(
                f"- **{s['symbol']}** {s['direction']} | vol_z={float(s['volume_zscore'] or 0):.1f} "
                f"iv_rank={float(s['iv_rank'] or 0):.0f}"
            )

        lines.append(f"\n### Historical Returns for Active Symbols:\n")
        if returns_data:
            lines.append("| Symbol | Samples | D+1 Mean | D+5 Mean | D+1 Std | D+1 WR |")
            lines.append("|--------|---------|----------|----------|---------|--------|")
            for r in returns_data:
                lines.append(
                    f"| {r['symbol']} | {r['sample_size']} | "
                    f"{float(r['mean_d1'] or 0)*100:+.2f}% | "
                    f"{float(r['mean_d5'] or 0)*100:+.2f}% | "
                    f"{float(r['std_d1'] or 0)*100:.2f}% | "
                    f"{float(r['wr_d1'] or 0)*100:.0f}% |"
                )
        else:
            lines.append("*No active symbols today or no historical return data available.*\n")

        lines.append(f"\n### Expert Performance (30d):\n")
        if expert_perf:
            for ep in expert_perf:
                total = (ep['wins'] or 0) + (ep['losses'] or 0)
                wr = (ep['wins'] or 0) / max(total, 1) * 100
                lines.append(
                    f"- **{ep['expert_id']}**: {ep['wins']}W/{ep['losses']}L ({wr:.0f}% WR), "
                    f"P&L: ${float(ep['total_pnl']):+,.2f}, Sharpe: {float(ep['avg_sharpe'] or 0):.2f}"
                )
        else:
            lines.append("*No expert performance data yet (cold start).*\n")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Gather all at once
    # ------------------------------------------------------------------

    def gather_all(self) -> dict[str, str]:
        """Gather all expert contexts. Returns dict of expert_id -> context string."""
        return {
            "flow_analyst": self.gather_flow_context(),
            "technical_analyst": self.gather_ta_context(),
            "sentiment_analyst": self.gather_sentiment_context(),
            "macro_strategist": self.gather_macro_context(),
            "risk_manager": self.gather_risk_context(),
            "quant_analyst": self.gather_quant_context(),
        }

    def gather_for_agent(self, agent_id: str) -> str:
        """Gather combined context for a specific agent (which may cover multiple experts)."""
        agent_contexts = {
            "flow_macro": [self.gather_flow_context(), self.gather_macro_context()],
            "technical": [self.gather_ta_context()],
            "sentiment_risk": [self.gather_sentiment_context(), self.gather_risk_context()],
            "quant": [self.gather_quant_context()],
            "pm": [self.gather_risk_context()],  # PM gets portfolio state
        }
        parts = agent_contexts.get(agent_id, [])
        return "\n\n---\n\n".join(parts)
