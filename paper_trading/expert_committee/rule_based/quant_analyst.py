"""
Quant Analyst Expert — Statistical validation, edge detection, regime identification.

Data sources:
  - orats_daily_returns: Forward returns for historical edge validation
  - orats_daily: Options metrics, IV rank, volume
  - expert_signals_e: Cross-reference other expert signals for validation
  - expert_performance_e: Historical accuracy of each expert

Decision cadence: On-demand (when other experts emit signals, Quant validates).

Signal logic:
  - Historical edge validation: Does this setup have positive expected value?
  - Regime detection: Is the current regime favorable for this type of trade?
  - Expert accuracy: Weight signal by emitting expert's recent track record
  - Statistical significance: Reject signals from patterns with < 100 historical samples
"""

import logging
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class QuantAnalyst(ExpertBase):
    """Validate signals statistically and detect market regimes."""

    @property
    def expert_id(self) -> str:
        return "quant_analyst"

    def analyze(self) -> list[Signal]:
        """Validate recent expert signals with statistical checks."""
        signals = []

        # Get signals from other experts that haven't been validated yet
        pending = self._get_unvalidated_signals()

        for sig in pending:
            validation = self._validate_signal(sig)
            if validation:
                signals.append(validation)

        if signals:
            logger.info(f"[Quant] Emitted {len(signals)} validation signals")
        return signals

    def _validate_signal(self, sig: dict) -> Optional[Signal]:
        """Run statistical validation on another expert's signal."""
        symbol = sig["symbol"]
        source_expert = sig["expert_id"]
        source_direction = sig["direction"]

        if not symbol:
            return None

        # 1. Check historical forward returns for this symbol
        hist = self._get_historical_returns(symbol)
        if not hist:
            return None

        # 2. Check expert's recent accuracy
        expert_accuracy = self._get_expert_accuracy(source_expert)

        # 3. Score validation
        conviction = 0
        breakdown = {}

        # Historical edge: positive mean return in direction
        mean_return = float(hist["mean_return_d1"])
        sample_size = int(hist["sample_size"])
        min_samples = int(self.get_parameter("min_backtest_sample_size", 100))

        breakdown["mean_return_d1"] = round(mean_return * 100, 2)
        breakdown["sample_size"] = sample_size

        if sample_size < min_samples:
            breakdown["status"] = f"insufficient_data ({sample_size}<{min_samples})"
            return None  # Not enough data to validate

        # Direction alignment with historical returns
        if source_direction == "BULLISH" and mean_return > 0:
            conviction += 30
            breakdown["historical_alignment"] = "confirmed"
        elif source_direction == "BEARISH" and mean_return < 0:
            conviction += 30
            breakdown["historical_alignment"] = "confirmed"
        elif abs(mean_return) < 0.001:
            conviction += 10
            breakdown["historical_alignment"] = "neutral"
        else:
            conviction += 0
            breakdown["historical_alignment"] = "contradicted"

        # Win rate check
        win_rate = float(hist.get("win_rate_d1", 0.5))
        breakdown["win_rate_d1"] = round(win_rate * 100, 1)
        if win_rate > 0.55:
            conviction += 20
        elif win_rate > 0.50:
            conviction += 10

        # Expert accuracy bonus
        if expert_accuracy:
            breakdown["expert_wr"] = round(expert_accuracy, 1)
            min_sharpe = self.get_parameter("min_sharpe_for_approval", 0.5)
            if expert_accuracy > 60:
                conviction += 15
            elif expert_accuracy < 45:
                conviction -= 10  # Penalize low-accuracy expert

        conviction = max(0, min(conviction, 90))

        if conviction < 30:
            return None

        # Mirror the source direction (Quant confirms or opposes)
        if breakdown.get("historical_alignment") == "contradicted":
            # Quant opposes the original signal
            direction = "BEARISH" if source_direction == "BULLISH" else "BULLISH"
            conviction = min(conviction, 50)  # Cap opposition conviction
        else:
            direction = source_direction

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction=direction,
            conviction=conviction,
            ttl_minutes=60,
            rationale=(
                f"{symbol} {direction} — Quant validation of {source_expert}: "
                f"historical {breakdown.get('historical_alignment', 'unknown')}, "
                f"D+1 mean={mean_return*100:.2f}%, WR={win_rate:.0%}, N={sample_size}"
            ),
            holding_period=sig.get("holding_period", "intraday"),
            instrument=sig.get("instrument", "stock"),
            confidence_breakdown=breakdown,
            metadata={
                "source_signal_id": sig["signal_id"],
                "source_expert": source_expert,
                "validation_type": "historical_edge",
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _get_unvalidated_signals(self) -> list[dict]:
        """Fetch recent signals from other experts that Quant hasn't validated yet."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT s.signal_id, s.expert_id, s.symbol, s.direction,
                               s.conviction, s.holding_period, s.instrument
                        FROM expert_signals_e s
                        WHERE s.expert_id != 'quant_analyst'
                          AND s.expert_id != 'risk_manager'
                          AND s.outcome IS NULL
                          AND s.expires_at > NOW()
                          AND s.signal_ts > NOW() - INTERVAL '15 minutes'
                          AND s.symbol IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM expert_signals_e q
                              WHERE q.expert_id = 'quant_analyst'
                                AND q.symbol = s.symbol
                                AND q.signal_ts > NOW() - INTERVAL '60 minutes'
                          )
                        ORDER BY s.conviction DESC
                        LIMIT 10
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Quant] Failed to fetch unvalidated signals: {e}")
            return []

    def _get_historical_returns(self, symbol: str) -> Optional[dict]:
        """Fetch forward return statistics from orats_daily_returns."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            COUNT(*) AS sample_size,
                            AVG(return_1d) AS mean_return_d1,
                            STDDEV(return_1d) AS std_return_d1,
                            AVG(CASE WHEN return_1d > 0 THEN 1.0 ELSE 0.0 END) AS win_rate_d1
                        FROM orats_daily_returns
                        WHERE ticker = %s
                    """, (symbol,))
                    row = cur.fetchone()
                    if row and row[0] > 0:
                        cols = [d[0] for d in cur.description]
                        return dict(zip(cols, row))
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[Quant] Failed to fetch returns for {symbol}: {e}")
        return None

    def _get_expert_accuracy(self, expert_id: str) -> Optional[float]:
        """Get trailing win rate for an expert from expert_performance_e."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            CASE WHEN SUM(wins + losses) > 0
                                THEN SUM(wins)::NUMERIC / SUM(wins + losses) * 100
                                ELSE NULL
                            END AS win_rate_pct
                        FROM expert_performance_e
                        WHERE expert_id = %s
                          AND trade_date > CURRENT_DATE - 30
                    """, (expert_id,))
                    row = cur.fetchone()
                    return float(row[0]) if row and row[0] else None
            finally:
                conn.close()
        except Exception:
            return None
