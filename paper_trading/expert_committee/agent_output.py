"""
Agent Output Parser — Convert Claude CLI JSON output to structured results.

Handles both expert agent outputs (signals) and PM agent outputs (decisions).
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ParsedSignal:
    """A signal extracted from an expert agent's JSON output."""
    expert_id: str
    symbol: str
    direction: str          # BULLISH, BEARISH, NEUTRAL
    conviction: int         # 0-100
    holding_period: str     # intraday, swing_2to5
    instrument: str         # stock, option
    suggested_entry: Optional[float] = None
    suggested_stop: Optional[float] = None
    suggested_target: Optional[float] = None
    rationale: str = ""
    confidence_breakdown: dict = field(default_factory=dict)
    ttl_minutes: int = 120


@dataclass
class ExpertResult:
    """Parsed result from one expert agent invocation."""
    agent_id: str
    signals: list[ParsedSignal]
    market_regime: Optional[str] = None   # Only from flow_macro
    risk_vetoes: list[dict] = field(default_factory=list)  # Only from sentiment_risk
    cost_usd: float = 0.0
    reasoning_summary: str = ""
    self_notes: str = ""                  # Agent's self-observations for memory
    self_rules: list[str] = field(default_factory=list)  # Durable rules for Self-Rules section
    raw_output: str = ""


@dataclass
class PMDecision:
    """A single trade decision from the PM agent."""
    symbol: str
    action: str               # BUY, SHORT, HOLD, PASS
    conviction: int           # 0-100
    size_pct: float           # Position size as % of equity
    expert_votes: dict = field(default_factory=dict)
    rationale: str = ""
    risk_notes: str = ""


@dataclass
class PMResult:
    """Parsed result from the PM agent invocation."""
    decisions: list[PMDecision]
    market_regime: str = "UNKNOWN"
    risk_summary: str = ""
    cost_usd: float = 0.0
    reasoning_summary: str = ""
    self_notes: str = ""                  # PM's self-observations for memory
    self_rules: list[str] = field(default_factory=list)  # Durable rules for Self-Rules section
    raw_output: str = ""


def _extract_json_from_result(raw_output: dict) -> dict:
    """Extract the JSON payload from claude --print --output-format json output.

    The claude CLI returns: {"result": "...", "total_cost_usd": 0.12, ...}
    The "result" field contains the agent's text output which should contain a JSON block.
    """
    import re

    result_text = raw_output.get("result", "")
    if not result_text:
        raise ValueError("Empty result from agent")

    # 1. Try to parse result_text directly as JSON first
    try:
        return json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Look for JSON block in ```json fences
    if "```json" in result_text:
        try:
            start = result_text.index("```json") + 7
            end = result_text.index("```", start)
            return json.loads(result_text[start:end].strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. Look for JSON in plain ``` fences (no language tag)
    if "```" in result_text:
        fence_match = re.search(r'```\s*\n?\s*(\{.*?\})\s*\n?\s*```', result_text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

    # 4. Brace-matching: find first { and its matching }
    if "{" in result_text:
        start = result_text.index("{")
        depth = 0
        for i in range(start, len(result_text)):
            if result_text[i] == "{":
                depth += 1
            elif result_text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(result_text[start:i + 1])
                    except json.JSONDecodeError:
                        break  # Fall through to reverse scan

    # 5. Reverse scan: find last '}' that forms valid JSON with first '{'
    if "{" in result_text:
        start = result_text.index("{")
        for i in range(len(result_text) - 1, start, -1):
            if result_text[i] == "}":
                try:
                    return json.loads(result_text[start:i + 1])
                except json.JSONDecodeError:
                    continue

    raise ValueError(f"Could not extract JSON from agent output: {result_text[:200]}")


def parse_expert_output(agent_id: str, raw_output: dict) -> ExpertResult:
    """Parse an expert agent's claude --print JSON output into ExpertResult."""
    cost = raw_output.get("total_cost_usd", 0.0) or 0.0
    raw_text = raw_output.get("result", "")

    try:
        data = _extract_json_from_result(raw_output)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"[{agent_id}] Failed to parse JSON: {e}")
        return ExpertResult(
            agent_id=agent_id, signals=[], cost_usd=cost,
            reasoning_summary=f"PARSE_ERROR: {e}", raw_output=raw_text,
        )

    signals = []
    for s in data.get("signals", []):
        try:
            sig = ParsedSignal(
                expert_id=s.get("expert_id", agent_id),
                symbol=s["symbol"].upper().strip(),
                direction=s["direction"].upper().strip(),
                conviction=max(0, min(100, int(s.get("conviction", 50)))),
                holding_period=s.get("holding_period", "intraday"),
                instrument=s.get("instrument", "stock"),
                suggested_entry=_safe_float(s.get("suggested_entry")),
                suggested_stop=_safe_float(s.get("suggested_stop")),
                suggested_target=_safe_float(s.get("suggested_target")),
                rationale=s.get("rationale", ""),
                confidence_breakdown=s.get("confidence_breakdown", {}),
                ttl_minutes=int(s.get("ttl_minutes", 120)),
            )
            signals.append(sig)
        except (KeyError, ValueError) as e:
            logger.warning(f"[{agent_id}] Skipping malformed signal: {e} — {s}")

    vetoes = []
    for v in data.get("risk_vetoes", []):
        if isinstance(v, dict) and v.get("symbol"):
            vetoes.append(v)

    # Parse self_rules: accept list of strings or single string
    raw_rules = data.get("self_rules", [])
    if isinstance(raw_rules, str):
        raw_rules = [raw_rules] if raw_rules.strip() else []
    self_rules = [r.strip() for r in raw_rules if isinstance(r, str) and r.strip()]

    return ExpertResult(
        agent_id=agent_id,
        signals=signals,
        market_regime=data.get("market_regime"),
        risk_vetoes=vetoes,
        cost_usd=cost,
        reasoning_summary=data.get("reasoning_summary", ""),
        self_notes=data.get("self_notes", ""),
        self_rules=self_rules,
        raw_output=raw_text,
    )


def parse_pm_output(raw_output: dict) -> PMResult:
    """Parse the PM agent's claude --print JSON output into PMResult."""
    cost = raw_output.get("total_cost_usd", 0.0) or 0.0
    raw_text = raw_output.get("result", "")

    try:
        data = _extract_json_from_result(raw_output)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"[PM] Failed to parse JSON: {e}")
        return PMResult(
            decisions=[], cost_usd=cost,
            reasoning_summary=f"PARSE_ERROR: {e}", raw_output=raw_text,
        )

    decisions = []
    for d in data.get("decisions", []):
        try:
            dec = PMDecision(
                symbol=d["symbol"].upper().strip(),
                action=d["action"].upper().strip(),
                conviction=max(0, min(100, int(d.get("conviction", 0)))),
                size_pct=max(0.0, min(0.10, float(d.get("size_pct", 0.05)))),
                expert_votes=d.get("expert_votes", {}),
                rationale=d.get("rationale", ""),
                risk_notes=d.get("risk_notes", ""),
            )
            if dec.action in ("BUY", "SHORT", "EXIT"):
                decisions.append(dec)
            else:
                logger.info(f"[PM] Non-actionable decision for {dec.symbol}: {dec.action}")
        except (KeyError, ValueError) as e:
            logger.warning(f"[PM] Skipping malformed decision: {e} — {d}")

    # Parse self_rules: accept list of strings or single string
    raw_rules = data.get("self_rules", [])
    if isinstance(raw_rules, str):
        raw_rules = [raw_rules] if raw_rules.strip() else []
    pm_self_rules = [r.strip() for r in raw_rules if isinstance(r, str) and r.strip()]

    return PMResult(
        decisions=decisions,
        market_regime=data.get("market_regime", "UNKNOWN"),
        risk_summary=data.get("risk_summary", ""),
        cost_usd=cost,
        reasoning_summary=data.get("reasoning_summary", ""),
        self_notes=data.get("self_notes", ""),
        self_rules=pm_self_rules,
        raw_output=raw_text,
    )


def _safe_float(val) -> Optional[float]:
    """Convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
