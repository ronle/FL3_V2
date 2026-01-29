"""
Phase Scoring Engine (Component 5.4)

Combines phase detectors into unified scoring system with transition tracking.
Detects phase transitions: Setup -> Acceleration -> Reversal

Usage:
    scorer = PhaseScorer(db_pool)
    result = await scorer.evaluate(symbol, all_data)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable

from .setup import SetupPhaseDetector, SetupSignal
from .acceleration import AccelerationPhaseDetector, AccelerationSignal
from .reversal import ReversalPhaseDetector, ReversalSignal

logger = logging.getLogger(__name__)


@dataclass
class PhaseState:
    """Current phase state for a symbol."""
    symbol: str
    current_phase: str = "NONE"  # NONE, SETUP, ACCELERATION, REVERSAL
    phase_score: float = 0.0
    phase_start_ts: Optional[datetime] = None
    last_update_ts: Optional[datetime] = None

    # Historical phase scores
    setup_score: float = 0.0
    acceleration_score: float = 0.0
    reversal_score: float = 0.0

    # Transition tracking
    transitions: list = field(default_factory=list)


@dataclass
class PhaseTransition:
    """Phase transition event."""
    symbol: str
    timestamp: datetime
    from_phase: str
    to_phase: str
    score: float
    contributing_factors: list
    confidence: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "symbol": self.symbol,
            "signal_ts": self.timestamp,
            "phase": self.to_phase,
            "score": self.score,
            "from_phase": self.from_phase,
            "contributing_factors": self.contributing_factors,
            "confidence": self.confidence,
        }


@dataclass
class EvaluationResult:
    """Result of phase evaluation."""
    symbol: str
    timestamp: datetime

    # Current scores
    setup_score: float = 0.0
    acceleration_score: float = 0.0
    reversal_score: float = 0.0

    # Dominant phase
    dominant_phase: str = "NONE"
    dominant_score: float = 0.0

    # Transition detected
    transition: Optional[PhaseTransition] = None

    # All factors
    all_factors: list = field(default_factory=list)


class PhaseScorer:
    """
    Unified phase scoring engine.

    Coordinates the three phase detectors and tracks transitions:
    - Setup: UOA + IV elevation + OI building
    - Acceleration: Breakout + volume + GEX + RSI
    - Reversal: Vanna flip + neg GEX + RSI divergence + IV crush

    Phase Transition Rules:
    - NONE -> SETUP: Setup score >= 0.5
    - SETUP -> ACCELERATION: Acceleration score >= 0.5
    - ACCELERATION -> REVERSAL: Reversal score >= 0.5
    - Any -> NONE: No phase score >= 0.3 for extended period

    Usage:
        scorer = PhaseScorer(db_pool)
        result = await scorer.evaluate(
            symbol="AAPL",
            uoa_data={...},
            ta_data={...},
            gex_data={...},
            orats_data={...},
            volume_data={...}
        )
    """

    def __init__(
        self,
        db_pool=None,
        on_transition: Optional[Callable] = None,
        transition_threshold: float = 0.5,
        alert_threshold: float = 0.7,
    ):
        """
        Initialize scorer.

        Args:
            db_pool: Database connection pool
            on_transition: Callback for phase transitions
            transition_threshold: Min score to trigger transition
            alert_threshold: Score threshold for high-confidence alerts
        """
        self.db_pool = db_pool
        self.on_transition = on_transition
        self.transition_threshold = transition_threshold
        self.alert_threshold = alert_threshold

        # Phase detectors
        self.setup_detector = SetupPhaseDetector()
        self.acceleration_detector = AccelerationPhaseDetector()
        self.reversal_detector = ReversalPhaseDetector()

        # State tracking per symbol
        self._states: dict[str, PhaseState] = {}

        # Metrics
        self._total_evaluations = 0
        self._transitions_detected = 0
        self._alerts_generated = 0

    def get_state(self, symbol: str) -> PhaseState:
        """Get or create state for a symbol."""
        if symbol not in self._states:
            self._states[symbol] = PhaseState(symbol=symbol)
        return self._states[symbol]

    async def evaluate(
        self,
        symbol: str,
        uoa_data: Optional[dict] = None,
        ta_data: Optional[dict] = None,
        gex_data: Optional[dict] = None,
        orats_data: Optional[dict] = None,
        volume_data: Optional[dict] = None,
        snapshot_data: Optional[dict] = None,
        timestamp: Optional[datetime] = None,
    ) -> EvaluationResult:
        """
        Evaluate all phases for a symbol.

        Args:
            symbol: Stock symbol
            uoa_data: UOA trigger data
            ta_data: Technical analysis data
            gex_data: GEX/Greeks exposure data
            orats_data: ORATS options data
            volume_data: Volume data
            snapshot_data: Option chain snapshot
            timestamp: Evaluation timestamp

        Returns:
            EvaluationResult with scores and potential transition
        """
        self._total_evaluations += 1
        ts = timestamp or datetime.now()

        # Run all detectors
        setup_signal = self.setup_detector.detect(
            symbol=symbol,
            uoa_data=uoa_data,
            orats_data=orats_data,
            snapshot_data=snapshot_data,
            timestamp=ts,
        )

        accel_signal = self.acceleration_detector.detect(
            symbol=symbol,
            ta_data=ta_data,
            gex_data=gex_data,
            volume_data=volume_data,
            timestamp=ts,
        )

        reversal_signal = self.reversal_detector.detect(
            symbol=symbol,
            ta_data=ta_data,
            gex_data=gex_data,
            orats_data=orats_data,
            volume_data=volume_data,
            timestamp=ts,
        )

        # Build result
        result = EvaluationResult(
            symbol=symbol,
            timestamp=ts,
            setup_score=setup_signal.score,
            acceleration_score=accel_signal.score,
            reversal_score=reversal_signal.score,
        )

        # Collect all factors
        result.all_factors = (
            setup_signal.contributing_factors +
            accel_signal.contributing_factors +
            reversal_signal.contributing_factors
        )

        # Determine dominant phase
        scores = {
            "SETUP": setup_signal.score,
            "ACCELERATION": accel_signal.score,
            "REVERSAL": reversal_signal.score,
        }

        max_phase = max(scores, key=scores.get)
        max_score = scores[max_phase]

        if max_score >= self.transition_threshold:
            result.dominant_phase = max_phase
            result.dominant_score = max_score

        # Check for transition
        state = self.get_state(symbol)
        transition = self._check_transition(state, result, ts)

        if transition:
            result.transition = transition
            self._transitions_detected += 1

            # Store to database
            if self.db_pool:
                await self._store_transition(transition)

            # Alert callback
            if self.on_transition:
                await self._notify_transition(transition)

            # High-confidence alert
            if transition.score >= self.alert_threshold:
                self._alerts_generated += 1
                logger.warning(
                    f"HIGH CONFIDENCE {transition.to_phase}: {symbol} "
                    f"score={transition.score:.2f}"
                )

        # Update state
        state.setup_score = setup_signal.score
        state.acceleration_score = accel_signal.score
        state.reversal_score = reversal_signal.score
        state.last_update_ts = ts

        return result

    def _check_transition(
        self,
        state: PhaseState,
        result: EvaluationResult,
        timestamp: datetime,
    ) -> Optional[PhaseTransition]:
        """Check if a phase transition occurred."""
        current = state.current_phase
        new_phase = result.dominant_phase

        # No transition if same phase or no dominant phase
        if new_phase == "NONE" or new_phase == current:
            return None

        # Valid transitions
        valid_transitions = {
            "NONE": ["SETUP"],
            "SETUP": ["ACCELERATION", "REVERSAL"],  # Can skip acceleration
            "ACCELERATION": ["REVERSAL"],
            "REVERSAL": ["SETUP"],  # Can restart cycle
        }

        if new_phase not in valid_transitions.get(current, []):
            # Invalid transition - log but don't trigger
            logger.debug(f"Invalid transition {current} -> {new_phase} for {state.symbol}")
            return None

        # Create transition
        transition = PhaseTransition(
            symbol=state.symbol,
            timestamp=timestamp,
            from_phase=current,
            to_phase=new_phase,
            score=result.dominant_score,
            contributing_factors=result.all_factors,
            confidence=result.dominant_score,
        )

        # Update state
        state.current_phase = new_phase
        state.phase_score = result.dominant_score
        state.phase_start_ts = timestamp
        state.transitions.append(transition)

        logger.info(
            f"TRANSITION: {state.symbol} {current} -> {new_phase} "
            f"score={result.dominant_score:.2f}"
        )

        return transition

    async def _store_transition(self, transition: PhaseTransition) -> bool:
        """Store transition to database."""
        if not self.db_pool:
            return False

        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO pd_phase_signals
                    (symbol, signal_ts, phase, score, contributing_factors, meta_json)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    transition.symbol,
                    transition.timestamp,
                    transition.to_phase,
                    transition.score,
                    transition.contributing_factors,
                    {
                        "from_phase": transition.from_phase,
                        "confidence": transition.confidence,
                    },
                )

            logger.debug(f"Stored transition for {transition.symbol}")
            return True

        except Exception as e:
            logger.error(f"Failed to store transition: {e}")
            return False

    async def _notify_transition(self, transition: PhaseTransition) -> None:
        """Notify transition callback."""
        if self.on_transition:
            try:
                if asyncio.iscoroutinefunction(self.on_transition):
                    await self.on_transition(transition)
                else:
                    self.on_transition(transition)
            except Exception as e:
                logger.error(f"Transition callback failed: {e}")

    def get_all_states(self) -> dict[str, PhaseState]:
        """Get all tracked symbol states."""
        return self._states

    def get_symbols_in_phase(self, phase: str) -> list[str]:
        """Get all symbols currently in a specific phase."""
        return [
            s for s, state in self._states.items()
            if state.current_phase == phase
        ]

    def get_metrics(self) -> dict:
        """Get scorer metrics."""
        return {
            "total_evaluations": self._total_evaluations,
            "transitions_detected": self._transitions_detected,
            "alerts_generated": self._alerts_generated,
            "symbols_tracked": len(self._states),
            "setup_detector": self.setup_detector.get_metrics(),
            "acceleration_detector": self.acceleration_detector.get_metrics(),
            "reversal_detector": self.reversal_detector.get_metrics(),
        }


if __name__ == "__main__":
    print("Phase Scoring Engine Tests")
    print("=" * 60)

    async def test_scorer():
        scorer = PhaseScorer()

        # Simulate a full P&D cycle for a symbol
        symbol = "PUMP"

        # Phase 1: Setup detection
        print("\n--- Day 1: Setup Phase ---")
        result = await scorer.evaluate(
            symbol=symbol,
            uoa_data={"volume_ratio": 5.0, "triggered": True},
            orats_data={"iv_rank": 70},
            snapshot_data={"call_oi_change_pct": 0.15},
        )
        print(f"Setup: {result.setup_score:.2f}, Accel: {result.acceleration_score:.2f}, Rev: {result.reversal_score:.2f}")
        print(f"Dominant: {result.dominant_phase} ({result.dominant_score:.2f})")
        if result.transition:
            print(f"TRANSITION: {result.transition.from_phase} -> {result.transition.to_phase}")

        # Phase 2: Acceleration
        print("\n--- Day 2: Acceleration Phase ---")
        result = await scorer.evaluate(
            symbol=symbol,
            uoa_data={"volume_ratio": 3.0},
            ta_data={
                "price": 115,
                "prev_close": 100,
                "atr_14": 3.0,
                "rsi_14": 78,
                "vwap": 108,
            },
            gex_data={"net_gex": 8_000_000},
            volume_data={"volume_ratio": 4.0},
            orats_data={"iv_rank": 80},
        )
        print(f"Setup: {result.setup_score:.2f}, Accel: {result.acceleration_score:.2f}, Rev: {result.reversal_score:.2f}")
        print(f"Dominant: {result.dominant_phase} ({result.dominant_score:.2f})")
        if result.transition:
            print(f"TRANSITION: {result.transition.from_phase} -> {result.transition.to_phase}")

        # Phase 3: Reversal
        print("\n--- Day 3: Reversal Phase ---")
        result = await scorer.evaluate(
            symbol=symbol,
            ta_data={
                "price": 118,
                "prev_price": 115,
                "rsi_14": 55,
                "prev_rsi": 78,
            },
            gex_data={
                "net_gex": -6_000_000,
                "net_vex": -150_000,
                "prev_vex": 100_000,
            },
            orats_data={
                "iv_rank": 40,
                "prev_iv_rank": 80,
            },
            volume_data={
                "volume_ratio": 1.5,
                "peak_volume_ratio": 5.0,
            },
        )
        print(f"Setup: {result.setup_score:.2f}, Accel: {result.acceleration_score:.2f}, Rev: {result.reversal_score:.2f}")
        print(f"Dominant: {result.dominant_phase} ({result.dominant_score:.2f})")
        if result.transition:
            print(f"TRANSITION: {result.transition.from_phase} -> {result.transition.to_phase}")

        # Check state
        state = scorer.get_state(symbol)
        print(f"\n--- Final State ---")
        print(f"Symbol: {state.symbol}")
        print(f"Current Phase: {state.current_phase}")
        print(f"Transitions: {len(state.transitions)}")
        for t in state.transitions:
            print(f"  {t.from_phase} -> {t.to_phase} @ score {t.score:.2f}")

        print(f"\nMetrics: {scorer.get_metrics()}")

    asyncio.run(test_scorer())
