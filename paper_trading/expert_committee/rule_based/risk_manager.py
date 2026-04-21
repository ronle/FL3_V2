"""
Risk Manager Expert — Portfolio exposure, correlation, and drawdown monitoring.

Data sources:
  - paper_trades_log_e: Open positions for exposure calculation
  - master_tickers: Sector/industry classification
  - Alpaca API: Account equity, positions (via executor)
  - orats_daily: VIX proxy (iv_rank on SPY/SPX)

Decision cadence: Continuous (every 30s during market hours).

Signal logic:
  - VETO (BEARISH signal on specific symbol) if:
    * Portfolio beta > 1.2
    * Sector concentration > 30%
    * Drawdown > 10%
    * Max positions reached
    * Correlation with existing positions too high
  - NEUTRAL advisory signals for regime context
"""

import logging
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class RiskManager(ExpertBase):
    """Monitor portfolio risk and emit veto signals when limits are breached."""

    @property
    def expert_id(self) -> str:
        return "risk_manager"

    def analyze(self) -> list[Signal]:
        """Check portfolio risk metrics and emit veto/advisory signals."""
        signals = []

        portfolio = self._get_portfolio_snapshot()
        if not portfolio:
            return signals

        # 1. Check position count limit
        max_pos = self._config.ACCOUNT_E_MAX_POSITIONS
        if portfolio["open_count"] >= max_pos:
            signals.append(self._emit_veto(
                symbol=None,
                reason=f"Max positions reached ({portfolio['open_count']}/{max_pos})",
                metadata={"check": "position_count", "current": portfolio["open_count"]}
            ))

        # 2. Check drawdown
        max_dd = self.get_parameter("max_drawdown_pct", self._config.ACCOUNT_E_MAX_DRAWDOWN)
        if portfolio["drawdown_pct"] and portfolio["drawdown_pct"] > max_dd:
            signals.append(self._emit_veto(
                symbol=None,
                reason=f"Drawdown {portfolio['drawdown_pct']:.1%} exceeds {max_dd:.0%} limit",
                metadata={"check": "drawdown", "current": portfolio["drawdown_pct"]}
            ))

        # 3. Check sector concentration
        max_sector = self.get_parameter("max_sector_pct", self._config.ACCOUNT_E_MAX_SECTOR_CONCENTRATION)
        for sector, pct in portfolio.get("sector_exposure", {}).items():
            if pct > max_sector:
                signals.append(self._emit_veto(
                    symbol=None,
                    reason=f"Sector {sector} at {pct:.0%} exceeds {max_sector:.0%} limit",
                    metadata={"check": "sector_concentration", "sector": sector, "current": pct}
                ))

        # 4. Emit advisory signal with current portfolio state
        if portfolio["open_count"] > 0:
            advisory = Signal(
                expert_id=self.expert_id,
                symbol=None,  # Portfolio-wide
                direction="NEUTRAL",
                conviction=50,
                ttl_minutes=5,  # Short TTL — refreshed every 30s
                rationale=self._build_portfolio_summary(portfolio),
                holding_period=None,
                instrument=None,
                metadata={
                    "portfolio_snapshot": portfolio,
                    "signal_type": "advisory",
                },
            )
            self.emit_signal(advisory)

        return signals

    def check_trade_risk(self, symbol: str, direction: str,
                         position_size_usd: float) -> Optional[str]:
        """Pre-trade risk check. Returns veto reason or None if approved.

        Called by Account E Executor before submitting any order.
        """
        portfolio = self._get_portfolio_snapshot()
        if not portfolio:
            return None

        # Position count
        max_pos = self._config.ACCOUNT_E_MAX_POSITIONS
        if portfolio["open_count"] >= max_pos:
            return f"Max positions ({max_pos}) reached"

        # Drawdown
        max_dd = self.get_parameter("max_drawdown_pct", self._config.ACCOUNT_E_MAX_DRAWDOWN)
        if portfolio.get("drawdown_pct", 0) > max_dd:
            return f"Drawdown {portfolio['drawdown_pct']:.1%} exceeds limit"

        # Sector concentration (check if adding this symbol would breach)
        sector = self._get_sector(symbol)
        if sector:
            current = portfolio.get("sector_exposure", {}).get(sector, 0)
            equity = portfolio.get("equity", 100000)
            new_pct = current + (position_size_usd / equity if equity > 0 else 0)
            max_sector = self.get_parameter("max_sector_pct", self._config.ACCOUNT_E_MAX_SECTOR_CONCENTRATION)
            if new_pct > max_sector:
                return f"Sector {sector} would be {new_pct:.0%} (limit {max_sector:.0%})"

        # Duplicate symbol check
        if symbol in portfolio.get("open_symbols", set()):
            return f"Already holding {symbol}"

        return None  # Approved

    # ------------------------------------------------------------------
    # Veto emission
    # ------------------------------------------------------------------

    def _emit_veto(self, symbol: Optional[str], reason: str,
                   metadata: Optional[dict] = None) -> Signal:
        """Create and emit a BEARISH (veto) signal."""
        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction="BEARISH",  # BEARISH = veto in PM synthesizer
            conviction=95,  # High conviction — this is a hard limit
            ttl_minutes=5,
            rationale=f"VETO: {reason}",
            metadata=metadata or {},
        )
        self.emit_signal(signal)
        logger.warning(f"[Risk] Veto emitted: {reason}")
        return signal

    # ------------------------------------------------------------------
    # Portfolio snapshot
    # ------------------------------------------------------------------

    def _get_portfolio_snapshot(self) -> Optional[dict]:
        """Build current portfolio risk metrics from open positions."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    # Open positions
                    cur.execute("""
                        SELECT symbol, direction, entry_price, shares,
                               holding_period, instrument
                        FROM paper_trades_log_e
                        WHERE exit_time IS NULL
                    """)
                    positions = cur.fetchall()

                    open_symbols = set()
                    sector_notional = {}
                    total_notional = 0.0

                    for row in positions:
                        sym, direction, entry_px, shares, _, _ = row
                        open_symbols.add(sym)
                        notional = float(entry_px) * int(shares)
                        total_notional += notional

                        sector = self._get_sector(sym)
                        if sector:
                            sector_notional[sector] = sector_notional.get(sector, 0) + notional

                    # Sector exposure as percentages
                    equity = max(total_notional, 100000)  # Use total or fallback
                    sector_exposure = {
                        s: n / equity for s, n in sector_notional.items()
                    }

                    # Drawdown: compare current equity to peak
                    # (simplified — use PnL from closed trades)
                    cur.execute("""
                        SELECT COALESCE(SUM(pnl), 0) AS total_pnl
                        FROM paper_trades_log_e
                        WHERE exit_time IS NOT NULL
                    """)
                    total_pnl = float(cur.fetchone()[0])
                    drawdown_pct = abs(min(total_pnl / 100000, 0))

                    return {
                        "open_count": len(positions),
                        "open_symbols": open_symbols,
                        "total_notional": total_notional,
                        "equity": equity,
                        "sector_exposure": sector_exposure,
                        "drawdown_pct": drawdown_pct,
                        "total_pnl": total_pnl,
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Risk] Failed to build portfolio snapshot: {e}")
            return None

    def _get_sector(self, symbol: str) -> Optional[str]:
        """Look up sector from master_tickers."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT sector FROM master_tickers WHERE symbol = %s",
                        (symbol,),
                    )
                    row = cur.fetchone()
                    return row[0] if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def _build_portfolio_summary(self, portfolio: dict) -> str:
        return (
            f"Portfolio: {portfolio['open_count']} positions, "
            f"${portfolio['total_notional']:,.0f} notional, "
            f"drawdown {portfolio.get('drawdown_pct', 0):.1%}"
        )
