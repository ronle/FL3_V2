"""
ExpertBase — Abstract base class for all Expert Committee members.

Provides:
  - Signal emission with rate limiting
  - DB-tunable parameters via expert_state_e
  - Conviction scoring contract
  - Signal persistence to expert_signals_e
"""

import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# Per-expert rate limits: (max_signals_per_symbol, window_minutes)
RATE_LIMITS = {
    "flow_analyst": (5, 30),
    "technical_analyst": (3, 30),
    "macro_strategist": (1, 60),
    "sentiment_analyst": (2, 60),
    "risk_manager": (None, None),  # No limit (veto signals)
    "quant_analyst": (1, 60),
    "catalyst_analyst": (2, 60),
}

# Portfolio-wide cap
PORTFOLIO_SIGNAL_CAP_PER_HOUR = 50


class Signal:
    """A signal emitted by an expert."""

    __slots__ = (
        "signal_id", "expert_id", "signal_ts", "symbol", "direction",
        "conviction", "holding_period", "instrument", "suggested_entry",
        "suggested_stop", "suggested_target", "ttl_minutes", "expires_at",
        "rationale", "confidence_breakdown", "metadata",
    )

    def __init__(
        self,
        expert_id: str,
        symbol: Optional[str],
        direction: str,
        conviction: int,
        ttl_minutes: int,
        rationale: str = "",
        holding_period: Optional[str] = None,
        instrument: Optional[str] = None,
        suggested_entry: Optional[float] = None,
        suggested_stop: Optional[float] = None,
        suggested_target: Optional[float] = None,
        confidence_breakdown: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ):
        self.signal_id = str(uuid.uuid4())
        self.expert_id = expert_id
        self.signal_ts = datetime.now(timezone.utc)
        self.symbol = symbol
        self.direction = direction
        self.conviction = max(0, min(100, conviction))
        self.holding_period = holding_period
        self.instrument = instrument
        self.suggested_entry = suggested_entry
        self.suggested_stop = suggested_stop
        self.suggested_target = suggested_target
        self.ttl_minutes = ttl_minutes
        self.expires_at = self.signal_ts + timedelta(minutes=ttl_minutes)
        self.rationale = rationale
        self.confidence_breakdown = confidence_breakdown or {}
        self.metadata = metadata or {}


class ExpertBase(ABC):
    """Abstract base class for Expert Committee members.

    Subclasses must implement:
      - analyze() — run domain analysis, return list of Signal objects
      - expert_id (property) — unique identifier string
    """

    def __init__(self, db_url: str, config):
        self._db_url = db_url.strip()
        self._config = config
        self._signal_times: dict[str, deque] = defaultdict(deque)
        self._portfolio_signal_times: deque = deque()
        self._parameters_cache: Optional[dict] = None
        self._parameters_cache_ts: Optional[datetime] = None

        # Rate limit settings for this expert
        limits = RATE_LIMITS.get(self.expert_id, (3, 30))
        self._rate_limit_max = limits[0]
        self._rate_limit_window = (
            timedelta(minutes=limits[1]) if limits[1] else None
        )

    @property
    @abstractmethod
    def expert_id(self) -> str:
        """Unique identifier: 'flow_analyst', 'technical_analyst', etc."""
        ...

    @abstractmethod
    def analyze(self) -> list[Signal]:
        """Run domain analysis and return a list of Signal objects.

        Called periodically by the orchestrator at the expert's cadence.
        Should query relevant data sources, compute conviction, and emit signals.
        """
        ...

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _can_emit(self, symbol: str) -> bool:
        """Check per-symbol and portfolio-wide rate limits."""
        # Risk Manager is never rate-limited
        if self._rate_limit_max is None:
            return True

        now = datetime.now(timezone.utc)

        # Per-symbol check
        key = f"{self.expert_id}:{symbol}"
        window = self._rate_limit_window
        while self._signal_times[key] and self._signal_times[key][0] < now - window:
            self._signal_times[key].popleft()
        if len(self._signal_times[key]) >= self._rate_limit_max:
            logger.warning(
                f"[{self.expert_id}] Rate limited on {symbol} "
                f"({self._rate_limit_max} signals in {window})"
            )
            return False

        # Portfolio-wide check
        hour_ago = now - timedelta(hours=1)
        while self._portfolio_signal_times and self._portfolio_signal_times[0] < hour_ago:
            self._portfolio_signal_times.popleft()
        cap = getattr(self._config, "ACCOUNT_E_PORTFOLIO_SIGNAL_CAP", PORTFOLIO_SIGNAL_CAP_PER_HOUR)
        if len(self._portfolio_signal_times) >= cap:
            logger.warning(
                f"[{self.expert_id}] Portfolio signal cap reached "
                f"({cap}/hour)"
            )
            return False

        # Record emission
        self._signal_times[key].append(now)
        self._portfolio_signal_times.append(now)
        return True

    # ------------------------------------------------------------------
    # DB-tunable parameters
    # ------------------------------------------------------------------

    def get_parameter(self, key: str, default: float) -> float:
        """Read tunable param from expert_state_e, fall back to default.

        Caches the parameters dict for 5 minutes to avoid hammering DB.
        """
        now = datetime.now(timezone.utc)
        if (
            self._parameters_cache is not None
            and self._parameters_cache_ts
            and (now - self._parameters_cache_ts) < timedelta(minutes=5)
        ):
            return float(self._parameters_cache.get(key, default))

        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT parameters FROM expert_state_e "
                        "WHERE expert_id = %s ORDER BY state_date DESC LIMIT 1",
                        (self.expert_id,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        self._parameters_cache = row[0]
                    else:
                        self._parameters_cache = {}
                    self._parameters_cache_ts = now
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[{self.expert_id}] Failed to read parameters: {e}")
            self._parameters_cache = {}
            self._parameters_cache_ts = now

        return float(self._parameters_cache.get(key, default))

    # ------------------------------------------------------------------
    # Signal persistence
    # ------------------------------------------------------------------

    def emit_signal(self, signal: Signal) -> bool:
        """Persist a signal to expert_signals_e. Returns True on success."""
        if signal.symbol and not self._can_emit(signal.symbol):
            return False

        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO expert_signals_e (
                            signal_id, expert_id, signal_ts, symbol, direction,
                            conviction, holding_period, instrument,
                            suggested_entry, suggested_stop, suggested_target,
                            ttl_minutes, expires_at,
                            rationale, confidence_breakdown, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s::jsonb, %s::jsonb
                        )
                        ON CONFLICT (signal_id) DO NOTHING
                        """,
                        (
                            signal.signal_id,
                            signal.expert_id,
                            signal.signal_ts,
                            signal.symbol,
                            signal.direction,
                            signal.conviction,
                            signal.holding_period,
                            signal.instrument,
                            signal.suggested_entry,
                            signal.suggested_stop,
                            signal.suggested_target,
                            signal.ttl_minutes,
                            signal.expires_at,
                            signal.rationale,
                            psycopg2.extras.Json(signal.confidence_breakdown),
                            psycopg2.extras.Json(signal.metadata),
                        ),
                    )
                conn.commit()
                logger.info(
                    f"[{self.expert_id}] Signal emitted: {signal.symbol} "
                    f"{signal.direction} conviction={signal.conviction} "
                    f"ttl={signal.ttl_minutes}min"
                )
                return True
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[{self.expert_id}] Failed to emit signal: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_active_signals(self, symbol: Optional[str] = None) -> list[dict]:
        """Fetch active (non-expired, no outcome) signals for this expert."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    if symbol:
                        cur.execute(
                            "SELECT signal_id, symbol, direction, conviction, "
                            "signal_ts, expires_at, rationale "
                            "FROM expert_signals_e "
                            "WHERE expert_id = %s AND symbol = %s "
                            "AND outcome IS NULL AND expires_at > NOW() "
                            "ORDER BY signal_ts DESC",
                            (self.expert_id, symbol),
                        )
                    else:
                        cur.execute(
                            "SELECT signal_id, symbol, direction, conviction, "
                            "signal_ts, expires_at, rationale "
                            "FROM expert_signals_e "
                            "WHERE expert_id = %s "
                            "AND outcome IS NULL AND expires_at > NOW() "
                            "ORDER BY signal_ts DESC",
                            (self.expert_id,),
                        )
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[{self.expert_id}] Failed to fetch active signals: {e}")
            return []

    def get_current_weight(self) -> float:
        """Get this expert's current dynamic weight from expert_state_e."""
        base = self._config.ACCOUNT_E_BASE_WEIGHTS.get(self.expert_id, 0.10)
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT current_weight FROM expert_state_e "
                        "WHERE expert_id = %s ORDER BY state_date DESC LIMIT 1",
                        (self.expert_id,),
                    )
                    row = cur.fetchone()
                    return float(row[0]) if row else base
            finally:
                conn.close()
        except Exception:
            return base
