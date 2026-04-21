"""
Feedback Manager — Outcome scoring, performance tracking, and memory updates.

Three core functions:
  A. score_closed_trade()    — called when a trade closes (by executor)
  B. update_performance_sections() — called daily at 4:15 PM ET
  C. recalibrate_weights()   — called weekly or every 20 closed trades

Updates ONLY the ## Performance and ## Recent Outcomes sections of memory files.
Agent-owned sections (## Self-Rules, ## Lessons Learned) are untouched.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

MEMORY_DIR = Path(__file__).resolve().parent / "memory"

# Memory file section markers
PERF_SECTION = "## Performance"
OUTCOMES_SECTION = "## Recent Outcomes"
RULES_SECTION = "## Self-Rules"


def score_closed_trade(trade_id: str, db_url: str) -> None:
    """Score a closed trade and attribute P&L to contributing experts.

    Called by account_e_executor when a trade closes.
    """
    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                # Get the closed trade
                cur.execute("""
                    SELECT symbol, direction, pnl, pnl_pct, exit_reason,
                           weighted_score, expert_votes
                    FROM paper_trades_log_e
                    WHERE trade_id = %s AND exit_time IS NOT NULL
                """, (trade_id,))
                trade = cur.fetchone()
                if not trade:
                    return

                symbol, direction, pnl, pnl_pct, exit_reason, score, votes = trade
                pnl = float(pnl or 0)
                is_win = pnl > 0

                # Parse expert_votes JSON
                if isinstance(votes, str):
                    import json
                    votes = json.loads(votes)
                if not votes or not isinstance(votes, list):
                    return

                # Attribute P&L to each expert proportionally
                total_weight = sum(v.get("weight", 0) for v in votes)
                if total_weight <= 0:
                    return

                for vote in votes:
                    expert_id = vote.get("expert")
                    if not expert_id:
                        continue
                    weight = vote.get("weight", 0)
                    attributed_pnl = pnl * (weight / total_weight)

                    # Insert to expert_performance_e
                    cur.execute("""
                        INSERT INTO expert_performance_e (
                            expert_id, trade_date, symbol, direction,
                            wins, losses, total_pnl, attributed_pnl,
                            conviction_at_signal, weighted_score_at_decision
                        ) VALUES (
                            %s, CURRENT_DATE, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                    """, (
                        expert_id, symbol, direction,
                        1 if is_win else 0,
                        0 if is_win else 1,
                        attributed_pnl, attributed_pnl,
                        vote.get("conviction", 0),
                        float(score or 0),
                    ))

                # Update outcome on matching signals
                outcome = "win" if is_win else "loss"
                cur.execute("""
                    UPDATE expert_signals_e
                    SET outcome = %s
                    WHERE symbol = %s
                      AND outcome IS NULL
                      AND expires_at > NOW() - INTERVAL '24 hours'
                """, (outcome, symbol))

            conn.commit()
            logger.info(f"Scored trade {trade_id}: {symbol} {'WIN' if is_win else 'LOSS'} ${pnl:+,.2f}")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to score trade {trade_id}: {e}")


def update_performance_sections(db_url: str) -> None:
    """Update ## Performance and ## Recent Outcomes in all memory files.

    Called daily at 4:15 PM ET. Preserves agent-owned sections.
    """
    expert_ids = [
        ("flow_analyst", "flow_analyst.md"),
        ("technical_analyst", "technical_analyst.md"),
        ("sentiment_analyst", "sentiment_analyst.md"),
        ("quant_analyst", "quant_analyst.md"),
        ("portfolio_manager", "pm.md"),
    ]

    for expert_id, filename in expert_ids:
        try:
            stats = _get_expert_stats(expert_id, db_url)
            recent = _get_recent_outcomes(expert_id, db_url)
            weight = _get_current_weight(expert_id, db_url)

            perf_text = _format_performance(stats, weight)
            outcomes_text = _format_recent_outcomes(recent)

            _update_memory_sections(filename, perf_text, outcomes_text)
            logger.info(f"Updated memory for {expert_id}")
        except Exception as e:
            logger.error(f"Failed to update memory for {expert_id}: {e}")


def score_replay_day(scored_signals: list[dict], replay_date, db_url: str) -> None:
    """Aggregate replay day outcomes into expert_performance_e (one row per expert).

    Each scored_signal dict has: expert_id, symbol, direction, conviction,
    r_p1, r_p5, pnl_d1, is_win.

    Args:
        scored_signals: List of scored signal dicts from replay scoring.
        replay_date: The replay date (date object).
        db_url: Database connection URL.
    """
    from collections import defaultdict

    if not scored_signals:
        return

    by_expert = defaultdict(list)
    for s in scored_signals:
        by_expert[s["expert_id"]].append(s)

    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                for expert_id, signals in by_expert.items():
                    wins = sum(1 for s in signals if s["is_win"])
                    losses = len(signals) - wins
                    total_pnl = sum(s["pnl_d1"] * 100 for s in signals)  # decimal → pct
                    avg_pnl = total_pnl / len(signals) if signals else 0
                    wr = wins / len(signals) if signals else 0
                    avg_conviction = sum(s["conviction"] for s in signals) / len(signals)

                    cur.execute("""
                        INSERT INTO expert_performance_e
                            (expert_id, trade_date, total_signals, trades_triggered,
                             wins, losses, win_rate, avg_pnl, total_pnl,
                             conviction, current_weight, base_weight)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        expert_id, replay_date, len(signals), len(signals),
                        wins, losses, round(wr, 4), round(avg_pnl, 4),
                        round(total_pnl, 4), int(avg_conviction), 0.20, 0.20,
                    ))
            conn.commit()
            total = sum(len(v) for v in by_expert.values())
            logger.info(
                f"Scored replay day {replay_date}: {total} signals across "
                f"{len(by_expert)} experts"
            )
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to score replay day {replay_date}: {e}")


def recalibrate_weights(db_url: str, config=None) -> None:
    """Recalibrate expert weights based on trailing performance.

    Called weekly or after every 20 closed trades.
    """
    if config is None:
        from paper_trading.config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                # Get trailing performance per expert
                cur.execute("""
                    SELECT expert_id,
                           COALESCE(SUM(wins), 0) AS wins,
                           COALESCE(SUM(losses), 0) AS losses,
                           COALESCE(AVG(trailing_sharpe), 0) AS sharpe
                    FROM expert_performance_e
                    WHERE trade_date > CURRENT_DATE - 30
                    GROUP BY expert_id
                """)
                rows = cur.fetchall()

                if not rows:
                    logger.info("No performance data for recalibration")
                    return

                # Compute new weights based on Sharpe ratio
                sharpes = {}
                for expert_id, wins, losses, sharpe in rows:
                    sharpes[expert_id] = max(float(sharpe), 0.01)

                total_sharpe = sum(sharpes.values())
                if total_sharpe <= 0:
                    return

                floor = config.ACCOUNT_E_WEIGHT_MIN
                ceiling = config.ACCOUNT_E_WEIGHT_MAX

                new_weights = {}
                for eid, s in sharpes.items():
                    raw = s / total_sharpe
                    new_weights[eid] = max(floor, min(ceiling, raw))

                # Normalize to sum to 1.0
                total = sum(new_weights.values())
                new_weights = {k: v / total for k, v in new_weights.items()}

                # Persist to expert_state_e
                for eid, w in new_weights.items():
                    cur.execute("""
                        INSERT INTO expert_state_e (expert_id, state_date, current_weight)
                        VALUES (%s, CURRENT_DATE, %s)
                        ON CONFLICT (expert_id, state_date)
                        DO UPDATE SET current_weight = EXCLUDED.current_weight
                    """, (eid, round(w, 4)))

            conn.commit()
            logger.info(f"Recalibrated weights: {new_weights}")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Weight recalibration failed: {e}")


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _get_expert_stats(expert_id: str, db_url: str) -> dict:
    """Get lifetime and trailing-20 stats for an expert."""
    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                # Lifetime stats
                cur.execute("""
                    SELECT COALESCE(SUM(wins), 0), COALESCE(SUM(losses), 0),
                           COALESCE(SUM(total_pnl), 0)
                    FROM expert_performance_e
                    WHERE expert_id = %s
                """, (expert_id,))
                row = cur.fetchone()
                lifetime_wins = row[0]
                lifetime_losses = row[1]
                lifetime_pnl = float(row[2])

                # Trailing 20 (by most recent trade dates)
                cur.execute("""
                    SELECT wins, losses, total_pnl, trailing_sharpe
                    FROM expert_performance_e
                    WHERE expert_id = %s
                    ORDER BY trade_date DESC
                    LIMIT 20
                """, (expert_id,))
                trailing = cur.fetchall()
                t20_wins = sum(r[0] or 0 for r in trailing)
                t20_losses = sum(r[1] or 0 for r in trailing)
                t20_pnl = sum(float(r[2] or 0) for r in trailing)
                t20_sharpe = float(trailing[0][3] or 0) if trailing and trailing[0][3] else 0

                # Signal count
                cur.execute("""
                    SELECT COUNT(*) FROM expert_signals_e WHERE expert_id = %s
                """, (expert_id,))
                signal_count = cur.fetchone()[0]

                return {
                    "signal_count": signal_count,
                    "lifetime_wins": lifetime_wins,
                    "lifetime_losses": lifetime_losses,
                    "lifetime_pnl": lifetime_pnl,
                    "t20_wins": t20_wins,
                    "t20_losses": t20_losses,
                    "t20_pnl": t20_pnl,
                    "t20_sharpe": t20_sharpe,
                }
        finally:
            conn.close()
    except Exception:
        return {}


def _get_recent_outcomes(expert_id: str, db_url: str) -> list[dict]:
    """Get last 20 trade outcomes attributed to this expert."""
    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT trade_date, symbol, direction,
                           wins, losses, attributed_pnl
                    FROM expert_performance_e
                    WHERE expert_id = %s
                    ORDER BY trade_date DESC
                    LIMIT 20
                """, (expert_id,))
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def _get_current_weight(expert_id: str, db_url: str) -> Optional[float]:
    """Get the expert's current dynamic weight."""
    try:
        conn = psycopg2.connect(db_url.strip())
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT current_weight FROM expert_state_e
                    WHERE expert_id = %s
                    ORDER BY state_date DESC LIMIT 1
                """, (expert_id,))
                row = cur.fetchone()
                return float(row[0]) if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _format_performance(stats: dict, weight: Optional[float]) -> str:
    """Format the ## Performance section content."""
    if not stats:
        return (
            "## Performance (auto-updated by feedback_manager.py — DO NOT EDIT)\n"
            "- Lifetime: 0 signals, 0 trades, N/A WR\n"
            "- Trailing 20: N/A (cold start)\n"
            "- Current weight: N/A (base)"
        )

    lt_total = stats["lifetime_wins"] + stats["lifetime_losses"]
    lt_wr = stats["lifetime_wins"] / max(lt_total, 1) * 100

    t20_total = stats["t20_wins"] + stats["t20_losses"]
    if t20_total > 0:
        t20_wr = stats["t20_wins"] / t20_total * 100
        t20_line = (
            f"- Trailing 20: {stats['t20_wins']}W/{stats['t20_losses']}L "
            f"({t20_wr:.0f}% WR), P&L: ${stats['t20_pnl']:+,.2f}, "
            f"Sharpe: {stats['t20_sharpe']:.2f}"
        )
    else:
        t20_line = "- Trailing 20: N/A (cold start)"

    weight_line = f"- Current weight: {weight:.2f}" if weight else "- Current weight: N/A (base)"

    return (
        "## Performance (auto-updated by feedback_manager.py — DO NOT EDIT)\n"
        f"- Lifetime: {stats['signal_count']} signals, {lt_total} trades, "
        f"{lt_wr:.0f}% WR, P&L: ${stats['lifetime_pnl']:+,.2f}\n"
        f"{t20_line}\n"
        f"{weight_line}"
    )


def _format_recent_outcomes(outcomes: list[dict]) -> str:
    """Format the ## Recent Outcomes section content."""
    header = "## Recent Outcomes (auto-updated by feedback_manager.py — DO NOT EDIT)"
    if not outcomes:
        return f"{header}\n(none yet)"

    lines = [header]
    for o in outcomes:
        result = "WIN" if (o.get("wins") or 0) > 0 else "LOSS"
        pnl = float(o.get("attributed_pnl") or 0)
        lines.append(
            f"- [{o['trade_date']}] {o['symbol']} {o['direction']} "
            f"→ {result} ${pnl:+,.2f}"
        )
    return "\n".join(lines)


def _update_memory_sections(filename: str, perf_text: str, outcomes_text: str) -> None:
    """Update ## Performance and ## Recent Outcomes in a memory file.

    Preserves all other sections (## Self-Rules, ## Lessons Learned, header).
    """
    filepath = MEMORY_DIR / filename
    if not filepath.exists():
        logger.warning(f"Memory file not found: {filepath}")
        return

    content = filepath.read_text(encoding="utf-8")

    # Replace ## Performance section
    content = _replace_section(content, PERF_SECTION, perf_text, OUTCOMES_SECTION)

    # Replace ## Recent Outcomes section
    content = _replace_section(content, OUTCOMES_SECTION, outcomes_text, RULES_SECTION)

    filepath.write_text(content, encoding="utf-8")


def _replace_section(content: str, section_header: str,
                     new_text: str, next_section: str) -> str:
    """Replace content between section_header and next_section."""
    # Find start of section
    start_idx = content.find(section_header)
    if start_idx == -1:
        return content

    # Find start of next section
    end_idx = content.find(next_section, start_idx + len(section_header))
    if end_idx == -1:
        # Section runs to end of file
        return content[:start_idx] + new_text + "\n\n"

    return content[:start_idx] + new_text + "\n\n" + content[end_idx:]
