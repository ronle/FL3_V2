"""
Sentiment Analyst Expert — News, social media, and Discord analysis.

Data sources:
  - articles + article_sentiment: News articles with NLP sentiment scores
  - discord_mentions: Discord ticker mention counts per hour
  - vw_media_daily_features: Aggregated media view (mentions, sentiment)

Decision cadence: Every 15 minutes (news/social doesn't change second-by-second).

Signal logic:
  - Contrarian: Extremely negative sentiment (< -0.5) on high volume = potential bounce
  - Momentum: Rising positive sentiment + increasing mentions = continuation
  - Crowded: Excessive mentions (> 10/day) = fade signal
  - Discord spike: Sudden mention surge = early mover detection
"""

import logging
from typing import Optional

import psycopg2

from .expert_base import ExpertBase, Signal

logger = logging.getLogger(__name__)


class SentimentAnalyst(ExpertBase):
    """Analyze news and social sentiment to generate trade signals."""

    @property
    def expert_id(self) -> str:
        return "sentiment_analyst"

    def analyze(self) -> list[Signal]:
        """Scan articles and Discord for sentiment extremes and crowding."""
        signals = []

        # 1. Check for contrarian sentiment (heavily negative + recent articles)
        contrarian = self._find_contrarian_setups()
        for setup in contrarian:
            signal = self._evaluate_contrarian(setup)
            if signal:
                signals.append(signal)

        # 2. Check for crowded trades (fade signal)
        crowded = self._find_crowded_symbols()
        for setup in crowded:
            signal = self._evaluate_crowded(setup)
            if signal:
                signals.append(signal)

        # 3. Check for Discord mention spikes
        spikes = self._find_discord_spikes()
        for setup in spikes:
            signal = self._evaluate_discord_spike(setup)
            if signal:
                signals.append(signal)

        if signals:
            logger.info(f"[Sentiment] Emitted {len(signals)} signals")
        return signals

    def _evaluate_contrarian(self, setup: dict) -> Optional[Signal]:
        """Extremely negative sentiment may signal a bounce."""
        symbol = setup["symbol"]
        avg_sentiment = float(setup["avg_sentiment"])
        article_count = int(setup["article_count"])

        contrarian_threshold = self.get_parameter("contrarian_threshold", -0.5)
        min_articles = int(self.get_parameter("min_article_count", 2))

        if avg_sentiment > contrarian_threshold or article_count < min_articles:
            return None

        # More negative = higher conviction (contrarian logic)
        conviction = 40
        if avg_sentiment < -0.7:
            conviction += 25
        elif avg_sentiment < -0.6:
            conviction += 15
        if article_count >= 5:
            conviction += 10

        conviction = min(conviction, 85)

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction="BULLISH",  # Contrarian: negative sentiment = potential long
            conviction=conviction,
            ttl_minutes=120,  # Sentiment signals persist longer
            rationale=(
                f"{symbol} CONTRARIAN — avg sentiment {avg_sentiment:.2f} "
                f"across {article_count} articles (potential bounce)"
            ),
            holding_period="swing_2to5",  # Sentiment reversals play out over days
            instrument="stock",
            confidence_breakdown={
                "avg_sentiment": avg_sentiment,
                "article_count": article_count,
                "signal_type": "contrarian",
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    def _evaluate_crowded(self, setup: dict) -> Optional[Signal]:
        """Excessive mentions signal crowding — bearish fade."""
        symbol = setup["symbol"]
        mention_count = int(setup["mention_count"])
        avg_sentiment = float(setup.get("avg_sentiment", 0))

        crowding_cap = int(self.get_parameter("crowding_mention_cap", 10))

        if mention_count < crowding_cap:
            return None

        # Crowded + very positive sentiment = strongest fade signal
        conviction = 40
        if mention_count >= 20:
            conviction += 20
        elif mention_count >= 15:
            conviction += 10
        if avg_sentiment > 0.5:
            conviction += 15  # Euphoria = stronger fade

        conviction = min(conviction, 80)

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction="BEARISH",  # Crowded = fade
            conviction=conviction,
            ttl_minutes=120,
            rationale=(
                f"{symbol} CROWDED — {mention_count} mentions, "
                f"sentiment={avg_sentiment:.2f} (fade)"
            ),
            holding_period="intraday",
            instrument="stock",
            confidence_breakdown={
                "mention_count": mention_count,
                "avg_sentiment": avg_sentiment,
                "signal_type": "crowded_fade",
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    def _evaluate_discord_spike(self, setup: dict) -> Optional[Signal]:
        """Sudden Discord mention spike = early mover detection."""
        symbol = setup["symbol"]
        current_mentions = int(setup["current_mentions"])
        avg_mentions = float(setup["avg_mentions"])

        if avg_mentions == 0:
            ratio = current_mentions
        else:
            ratio = current_mentions / avg_mentions

        if ratio < 3.0:  # Need at least 3x spike
            return None

        # Direction depends on if this is discovery (bullish) or panic (check articles)
        conviction = min(40 + int(ratio * 5), 75)
        direction = "BULLISH"  # Default: spike = interest = potential rally

        signal = Signal(
            expert_id=self.expert_id,
            symbol=symbol,
            direction=direction,
            conviction=conviction,
            ttl_minutes=60,
            rationale=(
                f"{symbol} DISCORD SPIKE — {current_mentions} mentions "
                f"({ratio:.1f}x avg) — early mover detection"
            ),
            holding_period="intraday",
            instrument="stock",
            confidence_breakdown={
                "current_mentions": current_mentions,
                "avg_mentions": avg_mentions,
                "spike_ratio": round(ratio, 1),
                "signal_type": "discord_spike",
            },
        )

        if self.emit_signal(signal):
            return signal
        return None

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _find_contrarian_setups(self) -> list[dict]:
        """Find symbols with very negative recent sentiment."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            ae.entity_value AS symbol,
                            AVG(s.sentiment) AS avg_sentiment,
                            COUNT(*) AS article_count
                        FROM articles a
                        JOIN article_entities ae ON ae.article_id = a.id
                        JOIN article_sentiment s ON s.article_id = a.id
                        WHERE a.published_at > NOW() - INTERVAL '24 hours'
                          AND ae.entity_type = 'ticker'
                          AND s.confidence > 0.6
                        GROUP BY ae.entity_value
                        HAVING AVG(s.sentiment) < -0.3
                           AND COUNT(*) >= 2
                        ORDER BY AVG(s.sentiment) ASC
                        LIMIT 20
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Sentiment] Failed to fetch contrarian setups: {e}")
            return []

    def _find_crowded_symbols(self) -> list[dict]:
        """Find symbols with excessive media/social mentions."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            symbol,
                            SUM(mention_count) AS mention_count
                        FROM discord_mentions
                        WHERE mention_date = CURRENT_DATE
                        GROUP BY symbol
                        HAVING SUM(mention_count) >= 8
                        ORDER BY SUM(mention_count) DESC
                        LIMIT 20
                    """)
                    cols = [d[0] for d in cur.description]
                    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

                    # Enrich with sentiment
                    for row in rows:
                        row["avg_sentiment"] = self._get_symbol_sentiment(
                            cur, row["symbol"]
                        )
                    return rows
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Sentiment] Failed to fetch crowded symbols: {e}")
            return []

    def _find_discord_spikes(self) -> list[dict]:
        """Find symbols with sudden mention spikes (current hour vs 7-day avg)."""
        try:
            conn = psycopg2.connect(self._db_url)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        WITH current_hour AS (
                            SELECT symbol, SUM(mention_count) AS current_mentions
                            FROM discord_mentions
                            WHERE mention_date = CURRENT_DATE
                              AND mention_hour = EXTRACT(HOUR FROM NOW())
                            GROUP BY symbol
                        ),
                        avg_hourly AS (
                            SELECT symbol, AVG(mention_count) AS avg_mentions
                            FROM discord_mentions
                            WHERE mention_date > CURRENT_DATE - 7
                            GROUP BY symbol
                        )
                        SELECT c.symbol, c.current_mentions,
                               COALESCE(a.avg_mentions, 0) AS avg_mentions
                        FROM current_hour c
                        LEFT JOIN avg_hourly a ON a.symbol = c.symbol
                        WHERE c.current_mentions >= 3
                          AND (a.avg_mentions IS NULL OR c.current_mentions > a.avg_mentions * 3)
                        ORDER BY c.current_mentions DESC
                        LIMIT 10
                    """)
                    cols = [d[0] for d in cur.description]
                    return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Sentiment] Failed to fetch Discord spikes: {e}")
            return []

    def _get_symbol_sentiment(self, cur, symbol: str) -> float:
        """Get average sentiment for a symbol from recent articles."""
        try:
            cur.execute("""
                SELECT AVG(s.sentiment)
                FROM articles a
                JOIN article_entities ae ON ae.article_id = a.id
                JOIN article_sentiment s ON s.article_id = a.id
                WHERE ae.entity_value = %s
                  AND ae.entity_type = 'ticker'
                  AND a.published_at > NOW() - INTERVAL '48 hours'
            """, (symbol,))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] else 0.0
        except Exception:
            return 0.0
