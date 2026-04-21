"""
Flow Analyst Expert — Options flow, UOA, and dark pool analysis.

Data sources:
  - uoa_triggers_v2: Unusual options activity triggers (real-time from firehose)
  - intraday_baselines_30m: Per-symbol notional baselines
  - gex_metrics_snapshot: Gamma/delta exposure positioning
  - flow_signals: Combined flow + pattern alignment signals (72.5% WR)

Decision cadence: Event-driven (on new UOA triggers, checked every 30s).

Signal logic:
  - Volume ratio vs baseline (strength of unusual activity)
  - Notional size (conviction scales with dollar flow)
  - Call/put bias from signal direction
  - GEX positioning (gamma flip distance, dealer hedging direction)
  - Flow signal alignment (flow_signals table cross-reference)
"""

import logging
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class FlowAnalyst(ExpertBase):
    """Analyze options flow and unusual activity to generate trade signals."""

    @property
    def expert_id(self) -> str:
        return "flow_analyst"

    def analyze(self) -> list[Signal]:
        """Scan recent UOA triggers and flow signals for actionable opportunities."""
        signals = []

        # 1. Check recent UOA triggers (last 10 min)
        triggers = self._fetch_recent_triggers()
        for trig in triggers:
            signal = self._evaluate_trigger(trig)
            if signal:
                signals.append(signal)

        # 2. Check flow_signals for high-confidence aligned signals
        flow_signals = self._fetch_aligned_flow_signals()
        for fs in flow_signals:
            signal = self._evaluate_flow_signal(fs)
            if signal:
                signals.append(signal)

        if signals:
            logger.info(f"[Flow] Emitted {len(signals)} signals from "
                        f"{len(triggers)} triggers + {len(flow_signals)} flow signals")
        return signals

    def _evaluate_trigger(self, trig: dict) -> Optional[Signal]:
        """Evaluate a UOA trigger for signal emission."""
        symbol = trig["symbol"]
        volume_ratio = float(trig["volume_ratio"]) if trig["volume_ratio"] else 0
        notional = float(trig["notional"]) if trig["notional"] else 0

        # Thresholds (DB-tunable)
        min_vol_ratio = self.get_parameter("uoa_volume_ratio_threshold", 3.0)
        min_notional = self.get_parameter("min_notional_usd", 50000)

        if volume_ratio < min_vol_ratio or notional < min_notional:
            return None

        # Direction from trigger
        sig_dir = trig.get("signal_direction", "").upper()
        entry_side = trig.get("entry_side", "").lower()

        if sig_dir == "BULLISH" or entry_side == "call":
            direction = "BULLISH"
        elif sig_dir == "BEARISH" or entry_side == "put":
            direction = "BEARISH"
        else:
            return None  # Ambiguous — skip

        # Skip if near earnings
        if trig.get("earnings_flag") and trig.get("earnings_days", 999) <= 2:
            return None

        # Conviction scoring
        conviction = 40  # Base for passing thresholds

        # Volume ratio bonus (3x=+0, 5x=+15, 10x=+30)
        if volume_ratio >= 10:
            conviction += 30
        elif volume_ratio >= 5:
            conviction += 15
        elif volume_ratio >= 4:
            conviction += 5

        # Notional bonus ($50K=+0, $100K=+10, $250K=+20)
        if notional >= 250_000:
            conviction += 20
        elif notional >= 100_000:
            conviction += 10

        # GEX context
        gex = self._get_gex_context(symbol)
        gex_info = {}
        if gex:
            gex_info = {
                "net_gex": float(gex["net_gex"]) if gex["net_gex"] else None,
                "gamma_flip_level": float(gex["gamma_flip_level"]) if gex["gamma_flip_level"] else None,
            }
            # GEX alignment: bullish + positive GEX = dealer hedging supports rally
            if direction == "BULLISH" and gex["net_gex"] and float(gex["net_gex"]) > 0:
                conviction += 10
            elif direction == "BEARISH" and gex["net_gex"] and float(gex["net_gex"]) < 0:
                conviction += 10

        conviction = min(conviction, 95)

        if conviction < 45:
            return None

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction=direction,
            conviction=conviction,
            ttl_minutes=60,  # Flow signals valid for 60 min
            rationale=(
                f"{symbol} {direction} — UOA {volume_ratio:.1f}x baseline, "
                f"${notional:,.0f} notional, {entry_side}"
            ),
            holding_period="intraday",
            instrument="stock",
            confidence_breakdown={
                "volume_ratio": volume_ratio,
                "notional_usd": notional,
                "entry_side": entry_side,
                **gex_info,
            },
            metadata={
                "trigger_id": trig.get("id"),
                "trigger_type": trig.get("trigger_type"),
                "contracts": trig.get("contracts"),
                "prints": trig.get("prints"),
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    def _evaluate_flow_signal(self, fs: dict) -> Optional[Signal]:
        """Evaluate a flow_signals row (pre-aligned flow + pattern signal)."""
        symbol = fs["symbol"]
        score = float(fs.get("combined_score", 0))

        if score < 60:
            return None

        direction = "BULLISH" if fs.get("direction", "").lower() in ("bullish", "long") else "BEARISH"

        conviction = min(int(score * 0.9), 95)  # Scale down slightly

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction=direction,
            conviction=conviction,
            ttl_minutes=45,
            rationale=f"{symbol} {direction} — flow_signal score={score:.0f}",
            holding_period="intraday",
            instrument="stock",
            confidence_breakdown={"flow_signal_score": score},
            metadata={"source": "flow_signals"},
        )

        if self.emit_signal(signal):
            return signal
        return None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_recent_triggers(self) -> list[dict]:
        """Fetch UOA triggers from the last 10 minutes."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, symbol, trigger_ts, trigger_type, volume_ratio,
                               notional, contracts, prints, signal_direction,
                               entry_side, earnings_flag, earnings_days
                        FROM uoa_triggers_v2
                        WHERE trigger_ts > NOW() - INTERVAL '10 minutes'
                        ORDER BY volume_ratio DESC
                        LIMIT 50
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Flow] Failed to fetch UOA triggers: {e}")
            return []

    def _fetch_aligned_flow_signals(self) -> list[dict]:
        """Fetch recent high-quality flow signals."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT symbol, direction, combined_score, signal_date
                        FROM flow_signals
                        WHERE signal_date = CURRENT_DATE
                          AND combined_score >= 60
                        ORDER BY combined_score DESC
                        LIMIT 20
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[Flow] Failed to fetch flow_signals: {e}")
            return []

    def _get_gex_context(self, symbol: str) -> Optional[dict]:
        """Get latest GEX snapshot for a symbol."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT net_gex, net_dex, gamma_flip_level, spot_price
                        FROM gex_metrics_snapshot
                        WHERE symbol = %s
                        ORDER BY snapshot_ts DESC LIMIT 1
                    """, (symbol,))
                    row = cur.fetchone()
                    if row:
                        cols = [d[0] for d in cur.description]
                        return dict(zip(cols, row))
            finally:
                conn.close()
        except Exception:
            pass
        return None
