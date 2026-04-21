"""
PM Synthesizer — Aggregates expert signals into trade decisions.

Implements direction-aware weighted consensus scoring:
  1. Collect active (non-expired) signals per symbol
  2. Split into bullish/bearish buckets
  3. Score each bucket using expert weights × conviction
  4. Apply opposition penalty and conflict discount
  5. If net score >= threshold → emit TradeDecision to pm_decisions_e

Called periodically (every 30s) by the Account E orchestrator.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    """A PM-level trade decision ready for execution."""
    decision_id: str
    symbol: str
    direction: str          # 'long' or 'short'
    instrument: str         # 'stock' or 'option'
    holding_period: str     # 'intraday' or 'swing_2to5'
    weighted_score: float
    position_size_pct: float
    expert_votes: list[dict]
    suggested_entry: Optional[float] = None
    suggested_stop: Optional[float] = None
    suggested_target: Optional[float] = None
    rationale: str = ""


class PMSynthesizer:
    """Portfolio Manager signal synthesis engine."""

    def __init__(self, db_url: str, config):
        self._db_url = db_url.strip()
        self._config = config
        self._recent_decisions: set[str] = set()  # symbol dedup within cycle

    def synthesize_all(self) -> list[TradeDecision]:
        """Main entry point: scan active signals, produce trade decisions.

        Returns list of TradeDecision objects that cleared the threshold.
        """
        self._recent_decisions.clear()
        signals_by_symbol = self._fetch_active_signals()
        decisions = []

        for symbol, signals in signals_by_symbol.items():
            decision = self._evaluate_symbol(symbol, signals)
            if decision and symbol not in self._recent_decisions:
                # Check if we already have an open position or pending decision
                if not self._has_open_position(symbol) and not self._has_pending_decision(symbol):
                    self._persist_decision(decision)
                    decisions.append(decision)
                    self._recent_decisions.add(symbol)

        if decisions:
            logger.info(f"[PM] Synthesized {len(decisions)} trade decisions")
        return decisions

    def _evaluate_symbol(self, symbol: str, signals: list[dict]) -> Optional[TradeDecision]:
        """Apply direction-aware weighted consensus for a single symbol."""
        weights = self._get_expert_weights()

        # 1. Separate by direction
        bullish = [s for s in signals if s["direction"] == "BULLISH"]
        bearish = [s for s in signals if s["direction"] == "BEARISH"]
        # NEUTRAL signals are advisory — don't vote

        if not bullish and not bearish:
            return None

        # 2. Score each bucket: weight × conviction
        bull_score = sum(
            weights.get(s["expert_id"], 0.0) * s["conviction"]
            for s in bullish
        )
        bear_score = sum(
            weights.get(s["expert_id"], 0.0) * s["conviction"]
            for s in bearish
        )

        # 3. Net conviction = stronger side minus opposition penalty
        opposition_penalty = self._config.ACCOUNT_E_OPPOSITION_PENALTY
        if bull_score >= bear_score:
            direction = "long"
            net_score = bull_score - (bear_score * opposition_penalty)
            winning_signals = bullish
        else:
            direction = "short"
            net_score = bear_score - (bull_score * opposition_penalty)
            winning_signals = bearish

        # 4. Conflict penalty: both sides have >= 2 experts
        if len(bullish) >= 2 and len(bearish) >= 2:
            net_score *= self._config.ACCOUNT_E_CONFLICT_DISCOUNT

        # 5. Check Risk Manager veto
        veto = self._check_veto(symbol, signals)
        if veto:
            logger.info(f"[PM] {symbol} vetoed by risk_manager: {veto}")
            self._persist_vetoed_decision(symbol, direction, net_score, signals, veto)
            return None

        # 6. Threshold check (cold-start uses higher threshold)
        min_score = self._get_effective_min_score()
        if net_score < min_score:
            return None

        # 7. Determine holding period and instrument from consensus
        holding_period = self._consensus_holding_period(winning_signals)
        instrument = self._consensus_instrument(winning_signals)

        # 8. Position sizing based on score
        size_pct = self._compute_position_size(net_score)

        # 9. Aggregate entry/stop/target from signals that provide them
        entry = self._best_suggested_price(winning_signals, "suggested_entry")
        stop = self._best_suggested_price(winning_signals, "suggested_stop")
        target = self._best_suggested_price(winning_signals, "suggested_target")

        # 10. Build expert votes array
        expert_votes = []
        for s in signals:
            expert_votes.append({
                "expert": s["expert_id"],
                "signal_id": s["signal_id"],
                "conviction": s["conviction"],
                "weight": weights.get(s["expert_id"], 0.0),
                "direction": s["direction"],
            })

        rationale = self._build_rationale(symbol, direction, net_score, winning_signals)

        return TradeDecision(
            decision_id=str(uuid.uuid4()),
            symbol=symbol,
            direction=direction,
            instrument=instrument,
            holding_period=holding_period,
            weighted_score=round(net_score, 2),
            position_size_pct=size_pct,
            expert_votes=expert_votes,
            suggested_entry=entry,
            suggested_stop=stop,
            suggested_target=target,
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Threshold & sizing
    # ------------------------------------------------------------------

    def _get_effective_min_score(self) -> float:
        """Return higher threshold during cold-start (first 20 trades)."""
        trade_count = self._get_total_trade_count()
        if trade_count < self._config.ACCOUNT_E_MIN_TRADES_FOR_RECAL:
            return self._config.ACCOUNT_E_COLD_START_MIN_SCORE
        return self._config.ACCOUNT_E_MIN_WEIGHTED_SCORE

    def _compute_position_size(self, score: float) -> float:
        """Scale position size by score and cold-start multiplier.

        Score 60-70: 50% of max | 70-80: 75% | 80+: 100%
        Cold-start: additional 50% multiplier for first 10 trades.
        """
        if score >= 80:
            base_pct = 1.0
        elif score >= 70:
            base_pct = 0.75
        else:
            base_pct = 0.50

        # Cold-start sizing reduction
        trade_count = self._get_total_trade_count()
        if trade_count < 10:
            base_pct *= self._config.ACCOUNT_E_COLD_START_SIZE_MULT
        elif trade_count < 20:
            base_pct *= 0.75

        return round(base_pct, 2)

    # ------------------------------------------------------------------
    # Veto check
    # ------------------------------------------------------------------

    def _check_veto(self, symbol: str, signals: list[dict]) -> Optional[str]:
        """Check if Risk Manager has emitted a BEARISH/veto signal for this symbol."""
        for s in signals:
            if s["expert_id"] == "risk_manager" and s["direction"] == "BEARISH":
                return s.get("rationale", "Risk Manager veto")
        return None

    # ------------------------------------------------------------------
    # Consensus helpers
    # ------------------------------------------------------------------

    def _consensus_holding_period(self, signals: list[dict]) -> str:
        """Pick holding period from highest-conviction signal."""
        for s in sorted(signals, key=lambda x: x["conviction"], reverse=True):
            if s.get("holding_period"):
                return s["holding_period"]
        return "intraday"

    def _consensus_instrument(self, signals: list[dict]) -> str:
        """Pick instrument from highest-conviction signal."""
        for s in sorted(signals, key=lambda x: x["conviction"], reverse=True):
            if s.get("instrument"):
                return s["instrument"]
        return "stock"

    def _best_suggested_price(self, signals: list[dict], field: str) -> Optional[float]:
        """Return the suggested price from the highest-conviction signal that has one."""
        for s in sorted(signals, key=lambda x: x["conviction"], reverse=True):
            if s.get(field) is not None:
                return float(s[field])
        return None

    def _build_rationale(self, symbol: str, direction: str, score: float,
                         winning_signals: list[dict]) -> str:
        """Build human-readable rationale for the decision."""
        experts = [s["expert_id"] for s in winning_signals]
        return (
            f"{symbol} {direction.upper()} — score {score:.1f} — "
            f"experts: {', '.join(experts)}"
        )

    # ------------------------------------------------------------------
    # Expert weights
    # ------------------------------------------------------------------

    def _get_expert_weights(self) -> dict[str, float]:
        """Load dynamic weights from expert_state_e, fall back to config base weights."""
        base = dict(self._config.ACCOUNT_E_BASE_WEIGHTS)
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT expert_id, current_weight FROM expert_state_e "
                        "WHERE state_date = (SELECT MAX(state_date) FROM expert_state_e)"
                    )
                    for row in cur.fetchall():
                        base[row[0]] = float(row[1])
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[PM] Could not load dynamic weights, using base: {e}")
        return base

    # ------------------------------------------------------------------
    # DB queries
    # ------------------------------------------------------------------

    def _fetch_active_signals(self) -> dict[str, list[dict]]:
        """Fetch all active (non-expired, no outcome) signals grouped by symbol."""
        signals_by_symbol: dict[str, list[dict]] = {}
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT signal_id, expert_id, symbol, direction, conviction, "
                        "holding_period, instrument, suggested_entry, suggested_stop, "
                        "suggested_target, rationale, signal_ts "
                        "FROM expert_signals_e "
                        "WHERE outcome IS NULL AND expires_at > NOW() "
                        "AND symbol IS NOT NULL "
                        "ORDER BY symbol, conviction DESC"
                    )
                    cols = [d[0] for d in cur.description]
                    for row in cur.fetchall():
                        rec = dict(zip(cols, row))
                        sym = rec["symbol"]
                        signals_by_symbol.setdefault(sym, []).append(rec)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[PM] Failed to fetch active signals: {e}")
        return signals_by_symbol

    def _has_open_position(self, symbol: str) -> bool:
        """Check if Account E already has an open position in this symbol."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM paper_trades_log_e "
                        "WHERE symbol = %s AND exit_time IS NULL LIMIT 1",
                        (symbol,),
                    )
                    return cur.fetchone() is not None
            finally:
                conn.close()
        except Exception:
            return False

    def _has_pending_decision(self, symbol: str) -> bool:
        """Check if there's already a pending (non-executed) decision for this symbol."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM pm_decisions_e "
                        "WHERE symbol = %s AND NOT executed AND NOT vetoed "
                        "AND decision_ts > NOW() - INTERVAL '1 hour' LIMIT 1",
                        (symbol,),
                    )
                    return cur.fetchone() is not None
            finally:
                conn.close()
        except Exception:
            return False

    def _get_total_trade_count(self) -> int:
        """Count total closed trades for cold-start logic."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM paper_trades_log_e WHERE exit_time IS NOT NULL"
                    )
                    row = cur.fetchone()
                    return row[0] if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_decision(self, d: TradeDecision) -> None:
        """Write a trade decision to pm_decisions_e."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO pm_decisions_e (
                            decision_id, decision_ts, symbol, direction, instrument,
                            holding_period, weighted_score, position_size_pct,
                            expert_votes, suggested_entry, suggested_stop,
                            suggested_target, rationale
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s::jsonb, %s, %s,
                            %s, %s
                        )
                        ON CONFLICT (decision_id) DO NOTHING
                        """,
                        (
                            d.decision_id,
                            datetime.now(timezone.utc),
                            d.symbol,
                            d.direction,
                            d.instrument,
                            d.holding_period,
                            d.weighted_score,
                            d.position_size_pct,
                            psycopg2.extras.Json(d.expert_votes),
                            d.suggested_entry,
                            d.suggested_stop,
                            d.suggested_target,
                            d.rationale,
                        ),
                    )
                conn.commit()
                logger.info(
                    f"[PM] Decision persisted: {d.symbol} {d.direction} "
                    f"score={d.weighted_score} size={d.position_size_pct}"
                )
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[PM] Failed to persist decision: {e}")

    def _persist_vetoed_decision(self, symbol: str, direction: str,
                                 score: float, signals: list[dict],
                                 veto_reason: str) -> None:
        """Record a vetoed decision for audit trail."""
        expert_votes = [
            {
                "expert": s["expert_id"],
                "signal_id": s["signal_id"],
                "conviction": s["conviction"],
                "direction": s["direction"],
            }
            for s in signals
        ]
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO pm_decisions_e (
                            decision_id, decision_ts, symbol, direction, instrument,
                            holding_period, weighted_score, position_size_pct,
                            expert_votes, vetoed, veto_expert, veto_reason, rationale
                        ) VALUES (
                            %s, %s, %s, %s, 'stock',
                            'intraday', %s, 0,
                            %s::jsonb, TRUE, 'risk_manager', %s, %s
                        )
                        """,
                        (
                            str(uuid.uuid4()),
                            datetime.now(timezone.utc),
                            symbol,
                            direction,
                            score,
                            psycopg2.extras.Json(expert_votes),
                            veto_reason,
                            f"VETOED: {symbol} {direction} score={score:.1f}",
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[PM] Failed to persist vetoed decision: {e}")
