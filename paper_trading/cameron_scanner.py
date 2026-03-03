"""
Cameron Scanner — Real-Time Pattern Detection for Account C

Pre-market: Load candidates from orats_daily (gap>=4%, rvol>=10, $1-$20)
9:45-11:00 AM ET: Every 60s, fetch 5-min bars from Alpaca, run pattern
detectors, UPSERT qualified patterns into cameron_scores table.

Runs as a coroutine inside the main event loop.
"""

import logging
from datetime import datetime, time as dt_time, date, timedelta
from typing import Dict, List, Optional

import aiohttp
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Alpaca bars endpoint
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


class CameronScanner:
    """
    Scans for Cameron patterns (bull flag, consolidation breakout, VWAP reclaim)
    on pre-filtered gap candidates during the 9:45-11:00 AM window.

    Writes qualifying patterns to cameron_scores table.
    """

    def __init__(
        self,
        db_pool,
        alpaca_key: str,
        alpaca_secret: str,
        scan_start: dt_time = dt_time(9, 45),
        scan_end: dt_time = dt_time(11, 0),
        rvol_min: float = 10.0,
        max_candidates: int = 30,
    ):
        self.db_pool = db_pool
        self.alpaca_key = alpaca_key
        self.alpaca_secret = alpaca_secret
        self.scan_start = scan_start
        self.scan_end = scan_end
        self.rvol_min = rvol_min
        self.max_candidates = max_candidates

        self._candidates: List[Dict] = []
        self._candidates_loaded = False
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "APCA-API-KEY-ID": self.alpaca_key,
                    "APCA-API-SECRET-KEY": self.alpaca_secret,
                }
            )
        return self._session

    def reset_daily(self):
        """Reset for new trading day."""
        self._candidates = []
        self._candidates_loaded = False
        logger.info("CameronScanner: reset for new day")

    def is_scan_window(self, now_et: Optional[datetime] = None) -> bool:
        """True if within 9:45-11:00 AM ET scan window."""
        if now_et is None:
            now_et = datetime.now(ET)
        t = now_et.time()
        return self.scan_start <= t < self.scan_end

    async def load_candidates(self) -> int:
        """
        Load Cameron candidates from orats_daily.

        Selects yesterday's gappers: gap>=4%, rvol>=10x, $1-$20 stock price.
        Returns count of candidates loaded.
        """
        if self._candidates_loaded:
            return len(self._candidates)

        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    WITH latest AS (
                        SELECT MAX(asof_date) AS d FROM orats_daily
                    ),
                    prev_date AS (
                        SELECT MAX(asof_date) AS d
                        FROM orats_daily
                        WHERE asof_date < (SELECT d FROM latest)
                    ),
                    prev AS (
                        SELECT symbol, stock_price AS prev_close
                        FROM orats_daily
                        WHERE asof_date = (SELECT d FROM prev_date)
                    ),
                    avg_vol AS (
                        SELECT symbol, AVG(total_volume) AS avg_30d_vol
                        FROM orats_daily
                        WHERE asof_date >= (SELECT d FROM latest) - INTERVAL '30 days'
                          AND asof_date <  (SELECT d FROM latest)
                        GROUP BY symbol
                    )
                    SELECT
                        o.symbol,
                        o.stock_price,
                        CASE WHEN p.prev_close > 0
                             THEN (o.stock_price - p.prev_close) / p.prev_close
                             ELSE 0 END AS gap_pct,
                        CASE WHEN NULLIF(av.avg_30d_vol, 0) > 0
                             THEN o.total_volume::float / av.avg_30d_vol
                             ELSE 0 END AS rvol
                    FROM orats_daily o
                    JOIN latest ON o.asof_date = latest.d
                    LEFT JOIN prev p ON p.symbol = o.symbol
                    LEFT JOIN avg_vol av ON av.symbol = o.symbol
                    WHERE o.stock_price BETWEEN 1 AND 20
                    ORDER BY gap_pct DESC
                """)

            self._candidates = []
            for r in rows:
                gap = float(r["gap_pct"]) if r["gap_pct"] else 0
                rvol = float(r["rvol"]) if r["rvol"] else 0
                if gap >= 0.04 and rvol >= self.rvol_min:
                    self._candidates.append({
                        "symbol": r["symbol"],
                        "price": float(r["stock_price"]),
                        "gap_pct": gap,
                        "rvol": rvol,
                    })

            # Cap at max_candidates, sorted by gap_pct DESC
            self._candidates = self._candidates[:self.max_candidates]
            self._candidates_loaded = True

            symbols = [c["symbol"] for c in self._candidates]
            logger.info(
                f"CameronScanner: loaded {len(self._candidates)} candidates "
                f"(gap>=4%, rvol>={self.rvol_min}x, $1-$20): "
                f"{symbols[:10]}{'...' if len(symbols) > 10 else ''}"
            )

            # Publish candidates to coordination table for V1 article fetch
            await self._publish_candidates()

            return len(self._candidates)

        except Exception as e:
            logger.warning(f"CameronScanner: failed to load candidates: {e}")
            return 0

    async def _publish_candidates(self):
        """
        Publish today's candidates to cameron_candidates_daily table.

        This coordination table is read by the V1 article fetch job
        so articles are pre-loaded before the 9:45 AM scan window.
        Non-fatal: failure here does not affect scanning.
        """
        if not self._candidates:
            return

        try:
            today = datetime.now(ET).date()
            async with self.db_pool.acquire() as conn:
                # Clear today's candidates and re-insert
                await conn.execute(
                    "DELETE FROM cameron_candidates_daily WHERE trade_date = $1",
                    today,
                )
                await conn.executemany("""
                    INSERT INTO cameron_candidates_daily
                    (trade_date, symbol, gap_pct, rvol)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (trade_date, symbol) DO NOTHING
                """, [
                    (today, c["symbol"], c["gap_pct"], c["rvol"])
                    for c in self._candidates
                ])
            logger.info(
                f"CameronScanner: published {len(self._candidates)} candidates "
                f"to cameron_candidates_daily"
            )
        except Exception as e:
            logger.warning(f"CameronScanner: failed to publish candidates: {e}")

    async def _fetch_bars(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Fetch 5-min bars from Alpaca REST for multiple symbols.

        Returns dict of symbol -> DataFrame with columns: ts, open, high, low, close, volume
        """
        if not symbols:
            return {}

        session = await self._get_session()
        now_et = datetime.now(ET)
        today = now_et.date()

        # Fetch from market open to now
        start = ET.localize(datetime.combine(today, dt_time(9, 30))).isoformat()
        end = now_et.isoformat()

        params = {
            "symbols": ",".join(symbols),
            "timeframe": "5Min",
            "start": start,
            "end": end,
            "limit": 10000,
            "feed": "sip",
            "adjustment": "raw",
        }

        result: Dict[str, pd.DataFrame] = {}

        try:
            async with session.get(ALPACA_BARS_URL, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"CameronScanner: Alpaca bars failed: {resp.status} {text[:200]}")
                    return {}

                data = await resp.json()
                bars = data.get("bars", {})

                for sym, bar_list in bars.items():
                    if not bar_list:
                        continue
                    rows = []
                    for b in bar_list:
                        rows.append({
                            "ts": b["t"],
                            "open": float(b["o"]),
                            "high": float(b["h"]),
                            "low": float(b["l"]),
                            "close": float(b["c"]),
                            "volume": int(b["v"]),
                        })
                    df = pd.DataFrame(rows)
                    df["ts"] = pd.to_datetime(df["ts"])
                    df.sort_values("ts", inplace=True)
                    df.reset_index(drop=True, inplace=True)
                    result[sym] = df

        except Exception as e:
            logger.warning(f"CameronScanner: bar fetch error: {e}")

        return result

    async def scan_tick(self) -> int:
        """
        Run one scan cycle: fetch bars, detect patterns, UPSERT to DB.

        Returns count of new patterns found.
        """
        if not self._candidates:
            return 0

        symbols = [c["symbol"] for c in self._candidates]
        candidate_map = {c["symbol"]: c for c in self._candidates}

        # Fetch bars in batches of 30 (Alpaca limit ~100 per request)
        all_bars: Dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), 30):
            batch = symbols[i:i + 30]
            bars = await self._fetch_bars(batch)
            all_bars.update(bars)

        if not all_bars:
            return 0

        # Import pattern detectors
        from scripts.patterns.bull_flag import detect_bull_flag
        from scripts.patterns.consolidation_breakout import detect_consolidation_breakout
        from scripts.patterns.vwap_reclaim import detect_vwap_reclaim, compute_vwap

        patterns_found = []
        today_str = datetime.now(ET).date()

        for sym, df in all_bars.items():
            if len(df) < 5:
                continue  # need minimum bars for detection

            cand = candidate_map.get(sym, {})
            gap_pct = cand.get("gap_pct", 0)
            rvol = cand.get("rvol", 0)

            # Run all three detectors
            detectors = [
                ("consolidation_breakout", lambda: detect_consolidation_breakout(sym, df, "5min", gap_pct=gap_pct)),
                ("vwap_reclaim", lambda: detect_vwap_reclaim(sym, df, "5min")),
                ("bull_flag", lambda: detect_bull_flag(sym, df, "5min")),
            ]

            for pattern_name, detect_fn in detectors:
                try:
                    result = detect_fn()
                except Exception as e:
                    logger.debug(f"CameronScanner: {pattern_name} error on {sym}: {e}")
                    continue

                if result is None:
                    continue

                # Only pass moderate strength (B2 filter)
                if result.pattern_strength != "moderate":
                    continue

                patterns_found.append({
                    "symbol": sym,
                    "pattern_type": result.pattern_type,
                    "pattern_strength": result.pattern_strength,
                    "entry_price": result.entry_price,
                    "stop_loss": result.stop_loss,
                    "target_1": result.target_1,
                    "target_2": getattr(result, "target_2", None),
                    "gap_pct": gap_pct,
                    "rvol": rvol,
                    "pattern_date": today_str,
                })

        # UPSERT patterns to cameron_scores
        if patterns_found:
            await self._upsert_patterns(patterns_found)
            logger.info(
                f"CameronScanner: {len(patterns_found)} patterns found "
                f"({len(all_bars)} symbols scanned)"
            )

        return len(patterns_found)

    async def _upsert_patterns(self, patterns: List[Dict]):
        """UPSERT patterns into cameron_scores table."""
        try:
            async with self.db_pool.acquire() as conn:
                for p in patterns:
                    await conn.execute("""
                        INSERT INTO cameron_scores
                        (symbol, pattern_type, pattern_strength, entry_price,
                         stop_loss, target_1, target_2, gap_pct, rvol,
                         interval, pattern_date)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        ON CONFLICT (symbol, pattern_date, pattern_type, interval)
                        DO UPDATE SET
                            scan_ts = NOW(),
                            pattern_strength = EXCLUDED.pattern_strength,
                            entry_price = EXCLUDED.entry_price,
                            stop_loss = EXCLUDED.stop_loss,
                            target_1 = EXCLUDED.target_1,
                            target_2 = EXCLUDED.target_2,
                            gap_pct = EXCLUDED.gap_pct,
                            rvol = EXCLUDED.rvol
                    """, p["symbol"], p["pattern_type"], p["pattern_strength"],
                        p["entry_price"], p["stop_loss"], p["target_1"],
                        p["target_2"], p["gap_pct"], p["rvol"],
                        "5min", p["pattern_date"])
        except Exception as e:
            logger.warning(f"CameronScanner: UPSERT failed: {e}")

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
