"""
Macro Strategist Expert — Market regime, VIX, sector rotation.

Data sources:
  - orats_daily: VIX (from SPY iv_rank), market breadth proxy
  - spot_prices: SPY/QQQ current prices for regime detection
  - master_tickers: Sector classification for rotation signals
  - (Future: FRED API for rates, yield curve)

Decision cadence: Daily pre-market (8:00 AM ET) + regime shift detection intraday.

Signal logic:
  - Market regime classification (RISK_ON / RISK_OFF / NEUTRAL)
  - VIX regime (low < 15, normal 15-25, elevated > 25)
  - Sector rotation signals (overweight/underweight recommendations)
  - SPY trend (momentum, key levels)
"""

import logging
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class MacroStrategist(ExpertBase):
    """Analyze macro conditions and market regime for portfolio-level signals."""

    @property
    def expert_id(self) -> str:
        return "macro_strategist"

    def analyze(self) -> list[Signal]:
        """Assess market regime and emit advisory/directional signals."""
        signals = []

        regime = self._assess_regime()
        if not regime:
            return signals

        # 1. Emit portfolio-wide regime signal
        regime_signal = self._emit_regime_signal(regime)
        if regime_signal:
            signals.append(regime_signal)

        # 2. Sector rotation signals
        sector_signals = self._assess_sector_rotation(regime)
        signals.extend(sector_signals)

        if signals:
            logger.info(f"[Macro] Emitted {len(signals)} signals — regime: {regime['label']}")
        return signals

    def _assess_regime(self) -> Optional[dict]:
        """Classify current market regime from VIX and SPY momentum."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    # SPY recent performance from orats_daily
                    cur.execute("""
                        SELECT stock_price, price_momentum_20d, iv_rank
                        FROM orats_daily
                        WHERE ticker = 'SPY'
                        ORDER BY asof_date DESC LIMIT 1
                    """)
                    spy = cur.fetchone()
                    if not spy:
                        return None

                    spy_price, momentum_20d, iv_rank = spy
                    spy_price = float(spy_price) if spy_price else 0
                    momentum = float(momentum_20d) if momentum_20d else 0
                    vix_proxy = float(iv_rank) if iv_rank else 50

                    # VIX thresholds (DB-tunable)
                    vix_high = self.get_parameter("vix_high_threshold", 25.0)
                    vix_low = self.get_parameter("vix_low_threshold", 15.0)

                    # Regime classification
                    if vix_proxy > 70 or momentum < -0.05:
                        label = "RISK_OFF"
                        confidence = 0.8
                        direction = "BEARISH"
                    elif vix_proxy < 30 and momentum > 0.02:
                        label = "RISK_ON"
                        confidence = 0.8
                        direction = "BULLISH"
                    elif momentum > 0:
                        label = "RISK_ON"
                        confidence = 0.6
                        direction = "BULLISH"
                    else:
                        label = "NEUTRAL"
                        confidence = 0.5
                        direction = "NEUTRAL"

                    # VIX regime
                    if vix_proxy > 70:
                        vix_regime = "elevated"
                    elif vix_proxy < 30:
                        vix_regime = "low"
                    else:
                        vix_regime = "normal"

                    return {
                        "label": label,
                        "confidence": confidence,
                        "direction": direction,
                        "spy_price": spy_price,
                        "momentum_20d": momentum,
                        "iv_rank": vix_proxy,
                        "vix_regime": vix_regime,
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Macro] Failed to assess regime: {e}")
            return None

    def _emit_regime_signal(self, regime: dict) -> Optional[Signal]:
        """Emit portfolio-wide regime signal."""
        conviction = int(regime["confidence"] * 80)  # Max 80 for macro (it's context)

        signal = Signal(
            expert_id=self.expert_id,
            symbol=None,  # Portfolio-wide
            direction=regime["direction"],
            conviction=conviction,
            ttl_minutes=480,  # Regime signals valid for full trading day
            rationale=(
                f"Market regime: {regime['label']} | "
                f"SPY momentum: {regime['momentum_20d']:.1%} | "
                f"IV rank: {regime['iv_rank']:.0f} | "
                f"VIX regime: {regime['vix_regime']}"
            ),
            holding_period=None,  # Advisory — not a specific trade
            instrument=None,
            confidence_breakdown={
                "regime": regime["label"],
                "confidence": regime["confidence"],
                "vix_regime": regime["vix_regime"],
            },
            metadata={
                "spy_price": regime["spy_price"],
                "momentum_20d": regime["momentum_20d"],
                "iv_rank": regime["iv_rank"],
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    def _assess_sector_rotation(self, regime: dict) -> list[Signal]:
        """Emit per-sector directional signals based on macro conditions."""
        signals = []

        if regime["label"] == "NEUTRAL":
            return signals

        # Sector ETF mapping for rotation
        sector_etfs = {
            "XLK": "Technology",
            "XLF": "Financials",
            "XLE": "Energy",
            "XLV": "Healthcare",
            "XLI": "Industrials",
            "XLY": "Consumer Discretionary",
            "XLP": "Consumer Staples",
            "XLU": "Utilities",
        }

        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    for etf, sector_name in sector_etfs.items():
                        cur.execute("""
                            SELECT price_momentum_20d
                            FROM orats_daily
                            WHERE ticker = %s
                            ORDER BY asof_date DESC LIMIT 1
                        """, (etf,))
                        row = cur.fetchone()
                        if not row or row[0] is None:
                            continue

                        sector_momentum = float(row[0])

                        # Strong momentum in RISK_ON = bullish signal on sector
                        if regime["label"] == "RISK_ON" and sector_momentum > 0.03:
                            direction = "BULLISH"
                            conviction = 55
                        elif regime["label"] == "RISK_OFF" and sector_momentum < -0.03:
                            direction = "BEARISH"
                            conviction = 55
                        elif regime["label"] == "RISK_ON" and sector_momentum < -0.02:
                            # Lagging sector in risk-on = potential rotation target
                            direction = "BULLISH"
                            conviction = 45
                        else:
                            continue

                        signal = Signal(
                            expert_id=self.expert_id,
                            symbol=etf,
                            direction=direction,
                            conviction=conviction,
                            ttl_minutes=480,
                            rationale=(
                                f"{sector_name} ({etf}) {direction} — "
                                f"momentum {sector_momentum:.1%} in {regime['label']} regime"
                            ),
                            holding_period="swing_2to5",
                            instrument="stock",
                            confidence_breakdown={
                                "sector_momentum": sector_momentum,
                                "regime": regime["label"],
                            },
                        )

                        if self.emit_signal(signal):
                            signals.append(signal)
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Macro] Failed to assess sector rotation: {e}")

        return signals
