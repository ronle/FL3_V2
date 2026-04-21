"""
Technical Analyst Expert — Price action, indicators, and pattern analysis.

Data sources:
  - ta_snapshots_v2: Intraday TA (5-min refresh) — RSI, SMA20, EMA9, VWAP, ATR
  - ta_daily_close: Prior-day TA — RSI, MACD, SMA20, SMA50, EMA9
  - engulfing_scores: Candlestick patterns (5-min timeframe)

Decision cadence: Every 5 minutes (aligned with TA pipeline refresh).

Signal logic:
  - Multi-timeframe trend alignment (daily + intraday)
  - RSI regime detection (oversold bounce, overbought reversal)
  - MACD crossover signals
  - Price vs VWAP positioning
  - Engulfing pattern confirmation
"""

import logging
from datetime import timedelta
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class TechnicalAnalyst(ExpertBase):
    """Analyze price action and technical indicators to generate trade signals."""

    @property
    def expert_id(self) -> str:
        return "technical_analyst"

    def analyze(self) -> list[Signal]:
        """Run TA analysis across tracked symbols. Returns signals for actionable setups."""
        signals = []
        symbols = self._get_symbols_with_fresh_ta()

        for sym_data in symbols:
            signal = self._evaluate_symbol(sym_data)
            if signal:
                signals.append(signal)

        if signals:
            logger.info(f"[TA] Emitted {len(signals)} signals from {len(symbols)} symbols")
        return signals

    def _evaluate_symbol(self, data: dict) -> Optional[Signal]:
        """Score a single symbol across TA dimensions."""
        symbol = data["symbol"]

        # Load thresholds (DB-tunable)
        rsi_oversold = self.get_parameter("rsi_oversold", 30.0)
        rsi_overbought = self.get_parameter("rsi_overbought", 70.0)
        min_alignment = self.get_parameter("min_alignment_score", 0.6)

        rsi = data.get("rsi_14")
        sma_20 = data.get("sma_20")
        sma_50 = data.get("sma_50")
        ema_9 = data.get("ema_9")
        vwap = data.get("vwap")
        price = data.get("price")
        macd_hist = data.get("macd_histogram")

        if not all([rsi, sma_20, price]):
            return None

        # --- Scoring dimensions ---
        conviction = 0
        direction_votes = {"BULLISH": 0, "BEARISH": 0}
        breakdown = {}

        # 1. RSI regime
        if rsi <= rsi_oversold:
            direction_votes["BULLISH"] += 1
            conviction += 20
            breakdown["rsi_regime"] = f"oversold ({rsi:.0f})"
        elif rsi >= rsi_overbought:
            direction_votes["BEARISH"] += 1
            conviction += 20
            breakdown["rsi_regime"] = f"overbought ({rsi:.0f})"
        elif 40 <= rsi <= 60:
            breakdown["rsi_regime"] = f"neutral ({rsi:.0f})"
        else:
            # Moderate zone — mild signal
            if rsi < 50:
                direction_votes["BULLISH"] += 1
                conviction += 10
            else:
                direction_votes["BEARISH"] += 1
                conviction += 10
            breakdown["rsi_regime"] = f"moderate ({rsi:.0f})"

        # 2. Trend alignment (price vs SMAs)
        alignment = 0
        if price > sma_20:
            alignment += 1
        if sma_50 and price > sma_50:
            alignment += 1
        if sma_50 and sma_20 > sma_50:
            alignment += 1
        if ema_9 and price > ema_9:
            alignment += 1

        alignment_score = alignment / 4.0
        breakdown["trend_alignment"] = round(alignment_score, 2)

        if alignment_score >= 0.75:
            direction_votes["BULLISH"] += 1
            conviction += 20
        elif alignment_score <= 0.25:
            direction_votes["BEARISH"] += 1
            conviction += 20
        elif alignment_score >= 0.5:
            direction_votes["BULLISH"] += 1
            conviction += 10

        # 3. VWAP position (intraday bias)
        if vwap and price:
            vwap_dist_pct = (price - vwap) / vwap * 100
            breakdown["vwap_dist_pct"] = round(vwap_dist_pct, 2)
            if vwap_dist_pct > 1.0:
                direction_votes["BULLISH"] += 1
                conviction += 10
            elif vwap_dist_pct < -1.0:
                direction_votes["BEARISH"] += 1
                conviction += 10

        # 4. MACD histogram (daily momentum)
        if macd_hist is not None:
            if macd_hist > 0:
                direction_votes["BULLISH"] += 1
                conviction += 15
                breakdown["macd"] = f"positive ({float(macd_hist):.3f})"
            elif macd_hist < 0:
                direction_votes["BEARISH"] += 1
                conviction += 15
                breakdown["macd"] = f"negative ({float(macd_hist):.3f})"

        # 5. Engulfing pattern confirmation
        engulfing = data.get("engulfing_direction")
        if engulfing:
            if engulfing == "bullish":
                direction_votes["BULLISH"] += 1
                conviction += 15
            elif engulfing == "bearish":
                direction_votes["BEARISH"] += 1
                conviction += 15
            breakdown["engulfing"] = engulfing

        # --- Direction decision ---
        bull = direction_votes["BULLISH"]
        bear = direction_votes["BEARISH"]
        if bull == bear or conviction < 30:
            return None  # No clear signal

        direction = "BULLISH" if bull > bear else "BEARISH"

        # Scale conviction: need alignment above threshold
        if alignment_score < min_alignment and direction == "BULLISH":
            conviction = int(conviction * 0.7)  # Penalize bullish without trend support

        conviction = min(conviction, 95)  # Cap

        if conviction < 40:
            return None  # Below minimum useful conviction

        # --- Build signal ---
        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction=direction,
            conviction=conviction,
            ttl_minutes=30,  # TA signals valid for 30 min
            rationale=self._build_rationale(symbol, direction, breakdown),
            holding_period="intraday",
            instrument="stock",
            suggested_entry=float(price) if price else None,
            suggested_stop=self._compute_stop(price, data.get("atr_14"), direction),
            suggested_target=self._compute_target(price, data.get("atr_14"), direction),
            confidence_breakdown=breakdown,
            metadata={
                "rsi": float(rsi) if rsi else None,
                "sma_20": float(sma_20) if sma_20 else None,
                "sma_50": float(sma_50) if sma_50 else None,
                "alignment_score": alignment_score,
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _get_symbols_with_fresh_ta(self) -> list[dict]:
        """Fetch symbols with recent TA data (last 10 min intraday + daily)."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    # Intraday TA (latest snapshot per symbol, last 10 min)
                    cur.execute("""
                        WITH latest_intraday AS (
                            SELECT DISTINCT ON (symbol)
                                symbol, price, rsi_14, sma_20, ema_9, vwap, atr_14,
                                snapshot_ts
                            FROM ta_snapshots_v2
                            WHERE snapshot_ts > NOW() - INTERVAL '10 minutes'
                            ORDER BY symbol, snapshot_ts DESC
                        ),
                        latest_daily AS (
                            SELECT DISTINCT ON (symbol)
                                symbol, sma_50, macd_histogram
                            FROM ta_daily_close
                            ORDER BY symbol, trade_date DESC
                        ),
                        latest_engulfing AS (
                            SELECT DISTINCT ON (symbol)
                                symbol, direction AS engulfing_direction
                            FROM engulfing_scores
                            WHERE scan_ts > NOW() - INTERVAL '30 minutes'
                                AND timeframe = '5min'
                                AND volume_confirmed = TRUE
                                AND pattern_strength != 'weak'
                            ORDER BY symbol, scan_ts DESC
                        )
                        SELECT
                            i.symbol, i.price, i.rsi_14, i.sma_20, i.ema_9,
                            i.vwap, i.atr_14,
                            d.sma_50, d.macd_histogram,
                            e.engulfing_direction
                        FROM latest_intraday i
                        LEFT JOIN latest_daily d ON d.symbol = i.symbol
                        LEFT JOIN latest_engulfing e ON e.symbol = i.symbol
                        WHERE i.rsi_14 IS NOT NULL
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[TA] Failed to fetch TA data: {e}")
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_stop(self, price, atr, direction) -> Optional[float]:
        if not price or not atr:
            return None
        atr_f = float(atr)
        price_f = float(price)
        if direction == "BULLISH":
            return round(price_f - 1.5 * atr_f, 2)
        else:
            return round(price_f + 1.5 * atr_f, 2)

    def _compute_target(self, price, atr, direction) -> Optional[float]:
        if not price or not atr:
            return None
        atr_f = float(atr)
        price_f = float(price)
        if direction == "BULLISH":
            return round(price_f + 2.0 * atr_f, 2)
        else:
            return round(price_f - 2.0 * atr_f, 2)

    def _build_rationale(self, symbol: str, direction: str, breakdown: dict) -> str:
        parts = [f"{symbol} {direction}"]
        for k, v in breakdown.items():
            parts.append(f"{k}={v}")
        return " | ".join(parts)
