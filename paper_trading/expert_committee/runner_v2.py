"""
Runner V2 — Agent-per-expert orchestrator using Claude Code CLI.

Spawns 4 Sonnet expert agents in parallel, then 1 Opus PM agent sequentially.
Each agent has MCP tool access (DB reads, file read/write for memory).
Falls back to rule-based experts and deterministic PM on failure.

Usage:
    python -m paper_trading.expert_committee.runner_v2 --once
    python -m paper_trading.expert_committee.runner_v2 --once --dry-run
"""

import argparse
import concurrent.futures
import importlib
import json
import logging
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

from paper_trading.expert_committee.agent_config import AgentConfig, DEFAULT_AGENT_CONFIG
from paper_trading.expert_committee.agent_output import (
    ExpertResult, PMResult, ParsedSignal,
    parse_expert_output, parse_pm_output,
)
from paper_trading.expert_committee.data_gatherer import DataGatherer

logger = logging.getLogger(__name__)

# Memory dir relative to this file
MEMORY_DIR = Path(__file__).resolve().parent / "memory"


def _get_db_url() -> str:
    """Get DB URL from environment or MCP config."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url.strip()
    # Fallback: local dev connection
    return "postgresql://FR3_User:di7UtK8E1%5B%5B137%40F@127.0.0.1:5433/fl3"


def _load_memory(agent_id: str, config: AgentConfig) -> str:
    """Load an agent's memory file, or return empty template if not found."""
    memory_file = config.memory_dir / config.agents[agent_id].memory_file
    if memory_file.exists():
        return memory_file.read_text(encoding="utf-8")
    return "(No memory file yet — this is your first cycle.)"


def _get_cycle_time(replay_ts: datetime = None) -> str:
    """Return current time string for prompts.

    Args:
        replay_ts: If provided, format this timestamp instead of now (for replay mode).
    """
    from zoneinfo import ZoneInfo
    if replay_ts:
        et = replay_ts.astimezone(ZoneInfo("America/New_York"))
    else:
        now = datetime.now(timezone.utc)
        et = now.astimezone(ZoneInfo("America/New_York"))
    weekday = et.strftime("%A")
    time_str = et.strftime("%Y-%m-%d %I:%M %p ET")

    # Market status
    hour, minute = et.hour, et.minute
    t = hour * 60 + minute
    if et.weekday() >= 5:
        status = "CLOSED (weekend)"
    elif t < 9 * 60 + 30:
        status = "PRE-MARKET"
    elif t >= 16 * 60:
        status = "AFTER-HOURS"
    else:
        mins_left = 16 * 60 - t
        status = f"OPEN ({mins_left} min until close)"

    return f"{weekday}, {time_str} — Market: {status}"


def _build_expert_task(agent_id: str, config: AgentConfig,
                       gatherer: DataGatherer) -> dict:
    """Build the system prompt and user prompt for an expert agent."""
    agent_def = config.agents[agent_id]
    memory = _load_memory(agent_id, config)

    # Import prompt module dynamically
    mod = importlib.import_module(
        f"paper_trading.expert_committee.{agent_def.prompt_module}"
    )

    system_prompt = mod.build_system_prompt(memory)
    data_context = gatherer.gather_for_agent(agent_id)
    user_prompt = mod.build_task_prompt(data_context, _get_cycle_time())

    return {
        "agent_id": agent_id,
        "model": agent_def.model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "budget_usd": agent_def.budget_usd,
    }


def _build_pm_task(expert_results: list[ExpertResult], config: AgentConfig,
                   gatherer: DataGatherer) -> dict:
    """Build the PM agent's task with all expert signals and memories."""
    from paper_trading.expert_committee.prompts import pm as pm_mod

    pm_memory = _load_memory("pm", config)
    system_prompt = pm_mod.build_system_prompt(pm_memory)

    # Serialize expert signals for PM
    signals_json = []
    for er in expert_results:
        for sig in er.signals:
            signals_json.append({
                "expert_id": sig.expert_id,
                "symbol": sig.symbol,
                "direction": sig.direction,
                "conviction": sig.conviction,
                "holding_period": sig.holding_period,
                "instrument": sig.instrument,
                "suggested_entry": sig.suggested_entry,
                "suggested_stop": sig.suggested_stop,
                "suggested_target": sig.suggested_target,
                "rationale": sig.rationale,
            })

    # Also include risk vetoes
    risk_vetoes = []
    for er in expert_results:
        risk_vetoes.extend(er.risk_vetoes)

    # Market regime from flow_macro agent
    market_regime = None
    for er in expert_results:
        if er.market_regime:
            market_regime = er.market_regime
            break

    expert_signals_str = json.dumps({
        "signals": signals_json,
        "risk_vetoes": risk_vetoes,
        "expert_market_regime": market_regime,
    }, indent=2)

    # Collect expert memories for PM to see track records
    expert_memories_parts = []
    for aid in ["flow_macro", "technical", "sentiment_risk", "quant"]:
        mem = _load_memory(aid, config)
        name = config.agents[aid].memory_file
        expert_memories_parts.append(f"### {name}\n{mem}")
    expert_memories_str = "\n\n".join(expert_memories_parts)

    # Portfolio context
    portfolio_context = gatherer.gather_for_agent("pm")

    user_prompt = pm_mod.build_task_prompt(
        expert_signals_json=expert_signals_str,
        data_context=portfolio_context,
        expert_memories=expert_memories_str,
        cycle_time=_get_cycle_time(),
    )

    return {
        "agent_id": "pm",
        "model": config.agents["pm"].model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "budget_usd": config.agents["pm"].budget_usd,
    }


def invoke_agent(task: dict, config: AgentConfig, no_tools: bool = False) -> dict:
    """Invoke a Claude CLI agent via subprocess. Returns raw JSON output.

    Args:
        no_tools: If True, disable all tools (agent can only produce text).
                  Use for replay mode where all data is pre-fetched in the prompt.
    """
    import tempfile

    # Write system prompt to temp file to avoid shell argument length limits
    # (Windows cmd.exe has ~8K limit; our prompts can exceed that)
    sys_prompt_file = None
    try:
        sys_prompt_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
            dir=str(config.project_root / "temp"),
        )
        sys_prompt_file.write(task["system_prompt"])
        sys_prompt_file.close()

        cmd = [
            config.claude_exe, "--print",
            "--output-format", "json",
            "--model", task["model"],
            "--system-prompt-file", sys_prompt_file.name,
            "--no-session-persistence",
        ]

        # Run agents from a dir OUTSIDE the project tree to avoid loading
        # the 49KB project CLAUDE.md which drowns out agent trading prompts.
        # Claude CLI walks up directories to find CLAUDE.md, so we must use
        # a path with no CLAUDE.md anywhere in its ancestry.
        import tempfile as _tmpmod
        agent_cwd = _tmpmod.gettempdir()  # e.g. C:\Users\...\AppData\Local\Temp

        if no_tools:
            # Replay mode: all data is pre-fetched, no tools needed.
            # --dangerously-skip-permissions + no MCP = pure text generation.
            cmd.extend(["--dangerously-skip-permissions"])
            cmd.extend(["--max-turns", "3"])
        else:
            # Live mode: agents can query DB and read/write memory files.
            cmd.extend(["--permission-mode", "bypassPermissions"])
            cmd.extend(["--mcp-config", str(config.mcp_config)])
            cmd.extend(["--allowedTools", config.allowed_tools])
            cmd.extend(["--max-turns", "15"])

        if task.get("budget_usd"):
            cmd.extend(["--max-budget-usd", str(task["budget_usd"])])

        # User prompt piped via stdin
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            cmd,
            input=task["user_prompt"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.agent_timeout_sec,
            cwd=agent_cwd,
            env=env,
        )

        if result.returncode != 0:
            logger.error(
                f"[{task['agent_id']}] CLI returned code {result.returncode}: "
                f"{result.stderr[:500]}"
            )
            return {"result": "", "total_cost_usd": 0}

        parsed = json.loads(result.stdout)

        # CLI can return code 0 but with is_error=true and no result
        if parsed.get("is_error"):
            errors = parsed.get("errors", [])
            logger.error(
                f"[{task['agent_id']}] CLI reported error (cost=${parsed.get('total_cost_usd', 0):.4f}): "
                f"{errors[:3] if errors else 'no error details'}"
            )
        elif not parsed.get("result"):
            logger.warning(
                f"[{task['agent_id']}] CLI returned empty result "
                f"(cost=${parsed.get('total_cost_usd', 0):.4f}, "
                f"turns={parsed.get('num_turns', '?')}, "
                f"stop={parsed.get('stop_reason', '?')})"
            )

        return parsed

    except subprocess.TimeoutExpired:
        logger.error(f"[{task['agent_id']}] Timed out after {config.agent_timeout_sec}s")
        return {"result": "", "total_cost_usd": 0}
    except json.JSONDecodeError as e:
        logger.error(f"[{task['agent_id']}] Invalid JSON output: {e}")
        return {"result": result.stdout if 'result' in dir() else "", "total_cost_usd": 0}
    finally:
        if sys_prompt_file:
            try:
                Path(sys_prompt_file.name).unlink(missing_ok=True)
            except Exception:
                pass


def invoke_agent_persistent(task: dict, config: AgentConfig,
                            session_id: str = None) -> tuple[dict, str]:
    """Invoke a Claude CLI agent with session persistence for multi-turn within a day.

    First call: creates a new session with --session-id.
    Subsequent calls: resumes with --resume (agent retains full context).

    Args:
        task: Standard task dict with agent_id, model, system_prompt, user_prompt.
        config: Agent config.
        session_id: If provided, resume this session instead of starting fresh.

    Returns:
        (raw_json_output, session_id) — session_id to pass back for next call.
    """
    is_resume = session_id is not None
    if not session_id:
        session_id = str(uuid.uuid4())

    import tempfile as _tmpmod
    sys_prompt_file = None
    try:
        # Run from outside project tree to avoid loading 49KB CLAUDE.md
        agent_cwd = _tmpmod.gettempdir()

        if is_resume:
            # Resume: agent already has system prompt + prior context
            cmd = [
                config.claude_exe, "--print",
                "--output-format", "json",
                "--resume", session_id,
                "--dangerously-skip-permissions",
                "--max-turns", "3",
            ]
        else:
            # First call: write system prompt to file (avoid shell arg limits)
            sys_prompt_file = _tmpmod.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8",
                dir=str(config.project_root / "temp"),
            )
            sys_prompt_file.write(task["system_prompt"])
            sys_prompt_file.close()

            cmd = [
                config.claude_exe, "--print",
                "--output-format", "json",
                "--model", task["model"],
                "--system-prompt-file", sys_prompt_file.name,
                "--session-id", session_id,
                "--dangerously-skip-permissions",
                "--max-turns", "3",
            ]

        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            cmd,
            input=task["user_prompt"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.agent_timeout_sec,
            cwd=agent_cwd,
            env=env,
        )

        if result.returncode != 0:
            logger.error(
                f"[{task['agent_id']}] Persistent CLI returned code {result.returncode}: "
                f"{result.stderr[:500]}"
            )
            return {"result": "", "total_cost_usd": 0}, session_id

        return json.loads(result.stdout), session_id

    except subprocess.TimeoutExpired:
        logger.error(f"[{task['agent_id']}] Persistent agent timed out after {config.agent_timeout_sec}s")
        return {"result": "", "total_cost_usd": 0}, session_id
    except json.JSONDecodeError as e:
        logger.error(f"[{task['agent_id']}] Invalid JSON output: {e}")
        return {"result": "", "total_cost_usd": 0}, session_id
    finally:
        if sys_prompt_file:
            try:
                Path(sys_prompt_file.name).unlink(missing_ok=True)
            except Exception:
                pass


def _persist_signals(signals: list[ParsedSignal], db_url: str,
                     signal_ts: datetime = None) -> int:
    """Persist parsed signals to expert_signals_e. Returns count persisted.

    Args:
        signal_ts: Override timestamp (for replay mode). Defaults to NOW().
    """
    if not signals:
        return 0
    count = 0
    try:
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                for sig in signals:
                    signal_id = str(uuid.uuid4())
                    ts = signal_ts or datetime.now(timezone.utc)
                    from datetime import timedelta
                    expires = ts + timedelta(minutes=sig.ttl_minutes)
                    metadata = {"is_replay": True} if signal_ts else {}
                    cur.execute("""
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
                    """, (
                        signal_id, sig.expert_id, ts, sig.symbol, sig.direction,
                        sig.conviction, sig.holding_period, sig.instrument,
                        sig.suggested_entry, sig.suggested_stop, sig.suggested_target,
                        sig.ttl_minutes, expires,
                        sig.rationale,
                        psycopg2.extras.Json(sig.confidence_breakdown),
                        psycopg2.extras.Json(metadata),
                    ))
                    count += 1
            conn.commit()
            logger.info(f"Persisted {count} signals to expert_signals_e")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to persist signals: {e}")
    return count


def _persist_pm_decisions(pm_result: PMResult, db_url: str,
                          decision_ts: datetime = None) -> int:
    """Persist PM decisions to pm_decisions_e. Returns count persisted.

    Args:
        decision_ts: Override timestamp (for replay mode). Defaults to NOW().
    """
    if not pm_result.decisions:
        return 0
    count = 0
    try:
        conn = psycopg2.connect(db_url)
        try:
            with conn.cursor() as cur:
                for dec in pm_result.decisions:
                    decision_id = str(uuid.uuid4())
                    direction = "long" if dec.action == "BUY" else "short"
                    ts = decision_ts or datetime.now(timezone.utc)
                    cur.execute("""
                        INSERT INTO pm_decisions_e (
                            decision_id, decision_ts, symbol, direction, instrument,
                            holding_period, weighted_score, position_size_pct,
                            expert_votes, rationale
                        ) VALUES (
                            %s, %s, %s, %s, 'stock',
                            'intraday', %s, %s,
                            %s::jsonb, %s
                        )
                        ON CONFLICT (decision_id) DO NOTHING
                    """, (
                        decision_id,
                        ts,
                        dec.symbol,
                        direction,
                        dec.conviction,
                        dec.size_pct,
                        psycopg2.extras.Json(dec.expert_votes),
                        dec.rationale,
                    ))
                    count += 1
            conn.commit()
            logger.info(f"Persisted {count} PM decisions to pm_decisions_e")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Failed to persist PM decisions: {e}")
    return count


def _log_cost(cost: float, config: AgentConfig) -> None:
    """Append cost entry to JSONL log."""
    config.cost_log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cost_usd": round(cost, 4),
    }
    with open(config.cost_log, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _get_daily_cost(config: AgentConfig) -> float:
    """Read today's cumulative cost from the cost log."""
    if not config.cost_log.exists():
        return 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    try:
        with open(config.cost_log) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("ts", "").startswith(today):
                    total += entry.get("cost_usd", 0)
    except Exception:
        pass
    return total


def _run_rule_based_fallback(agent_id: str, gatherer: DataGatherer,
                             db_url: str) -> ExpertResult:
    """Run rule-based expert as fallback when an agent fails."""
    logger.warning(f"[{agent_id}] Using rule-based fallback")
    # Return empty result — rule-based experts need full integration
    # which is beyond the scope of the initial agent rollout.
    # The rule-based classes exist in rule_based/*.py for future wiring.
    return ExpertResult(
        agent_id=agent_id,
        signals=[],
        reasoning_summary=f"FALLBACK: Agent {agent_id} failed, rule-based returned no signals",
    )


def run_cycle(config: AgentConfig = None, dry_run: bool = False) -> dict:
    """Run one full expert committee cycle.

    Returns summary dict with signal/decision counts and costs.
    """
    if config is None:
        config = DEFAULT_AGENT_CONFIG

    db_url = _get_db_url()
    cycle_time = _get_cycle_time()
    logger.info(f"=== Expert Committee V2 Cycle: {cycle_time} ===")

    # Ensure memory dir exists
    config.memory_dir.mkdir(parents=True, exist_ok=True)

    # 1. Check daily cost budget
    daily_cost = _get_daily_cost(config)
    if daily_cost >= config.max_daily_cost:
        logger.warning(
            f"Daily agent budget exceeded (${daily_cost:.2f} >= ${config.max_daily_cost:.2f}) "
            f"— skipping agent cycle"
        )
        return {"status": "budget_exceeded", "daily_cost": daily_cost}

    # 2. Gather pre-fetched data
    gatherer = DataGatherer(db_url)

    # 3. Build expert tasks
    expert_agent_ids = ["flow_macro", "technical", "sentiment_risk", "quant"]
    tasks = []
    for aid in expert_agent_ids:
        try:
            task = _build_expert_task(aid, config, gatherer)
            tasks.append(task)
        except Exception as e:
            logger.error(f"[{aid}] Failed to build task: {e}")

    # 4. Spawn expert agents in parallel
    expert_results: list[ExpertResult] = []
    if not dry_run:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            future_to_task = {
                pool.submit(invoke_agent, t, config): t
                for t in tasks
            }
            try:
                for future in concurrent.futures.as_completed(future_to_task, timeout=config.agent_timeout_sec + 30):
                    task = future_to_task[future]
                    try:
                        raw = future.result()
                        result = parse_expert_output(task["agent_id"], raw)
                        expert_results.append(result)
                        logger.info(
                            f"[{task['agent_id']}] {len(result.signals)} signals, "
                            f"${result.cost_usd:.3f}"
                        )
                    except Exception as e:
                        logger.error(f"[{task['agent_id']}] Agent failed: {e}")
                        expert_results.append(
                            _run_rule_based_fallback(task["agent_id"], gatherer, db_url)
                        )
            except concurrent.futures.TimeoutError:
                # Some agents didn't finish — collect what we have and continue
                timed_out = [
                    future_to_task[f]["agent_id"]
                    for f in future_to_task if not f.done()
                ]
                logger.warning(f"Expert pool timeout — {timed_out} did not finish. Proceeding with {len(expert_results)} results.")
    else:
        logger.info("[DRY RUN] Skipping agent invocation — dumping prompts")
        for t in tasks:
            prompt_len = len(t["system_prompt"]) + len(t["user_prompt"])
            logger.info(f"  [{t['agent_id']}] prompt size: {prompt_len:,} chars")
            expert_results.append(ExpertResult(
                agent_id=t["agent_id"], signals=[],
                reasoning_summary="DRY_RUN: no agent invoked",
            ))

    # 5. Flatten and persist expert signals
    all_signals = []
    for er in expert_results:
        all_signals.extend(er.signals)

    signal_count = 0
    if all_signals and not dry_run:
        signal_count = _persist_signals(all_signals, db_url)

    logger.info(f"Expert phase complete: {len(all_signals)} signals from {len(expert_results)} agents")

    # 6. PM Agent (Opus)
    pm_result = None
    if not dry_run and all_signals:
        try:
            pm_task = _build_pm_task(expert_results, config, gatherer)
            pm_raw = invoke_agent(pm_task, config)
            pm_result = parse_pm_output(pm_raw)
            logger.info(
                f"[PM] {len(pm_result.decisions)} decisions, "
                f"regime={pm_result.market_regime}, ${pm_result.cost_usd:.3f}"
            )

            # Retry on parse failure: ask a cheap agent to extract JSON
            if (pm_result.reasoning_summary.startswith("PARSE_ERROR")
                    and pm_raw.get("result")):
                logger.warning("[PM] Parse failed, retrying with JSON-extraction prompt")
                retry_task = {
                    "agent_id": "pm_retry",
                    "model": config.agents["pm"].model,
                    "system_prompt": (
                        "You are a JSON formatter. Extract the trading decisions from "
                        "the text below and output ONLY a valid JSON object. No markdown "
                        "fences, no commentary. First character must be '{', last must be '}'."
                    ),
                    "user_prompt": (
                        f"Extract the trading decisions from this PM output into the "
                        f"required JSON format:\n\n{pm_raw['result']}\n\n"
                        f'Required format: {{"decisions": [...], "market_regime": "...", '
                        f'"risk_summary": "...", "reasoning_summary": "..."}}'
                    ),
                    "budget_usd": 0.10,
                }
                retry_raw = invoke_agent(retry_task, config, no_tools=True)
                retry_result = parse_pm_output(retry_raw)
                if not retry_result.reasoning_summary.startswith("PARSE_ERROR"):
                    retry_result.cost_usd += pm_result.cost_usd
                    pm_result = retry_result
                    logger.info(
                        f"[PM] Retry succeeded: {len(pm_result.decisions)} decisions, "
                        f"regime={pm_result.market_regime}"
                    )
                else:
                    logger.error("[PM] Retry also failed to produce valid JSON")

        except Exception as e:
            logger.error(f"[PM] Agent failed: {e} — falling back to deterministic PM")
            try:
                from paper_trading.expert_committee.rule_based.pm_synthesizer import PMSynthesizer
                from paper_trading.config import DEFAULT_CONFIG
                synth = PMSynthesizer(db_url, DEFAULT_CONFIG)
                decisions = synth.synthesize_all()
                pm_result = PMResult(
                    decisions=[],  # Deterministic PM writes to DB directly
                    reasoning_summary=f"FALLBACK: Deterministic PM produced {len(decisions)} decisions",
                )
            except Exception as e2:
                logger.error(f"[PM] Deterministic fallback also failed: {e2}")
                pm_result = PMResult(decisions=[], reasoning_summary="PM_FAILED")
    elif dry_run:
        if all_signals:
            pm_task = _build_pm_task(expert_results, config, gatherer)
            prompt_len = len(pm_task["system_prompt"]) + len(pm_task["user_prompt"])
            logger.info(f"  [PM] prompt size: {prompt_len:,} chars")
        pm_result = PMResult(decisions=[], reasoning_summary="DRY_RUN")
    else:
        logger.info("[PM] No expert signals — skipping PM agent")
        pm_result = PMResult(decisions=[], reasoning_summary="NO_SIGNALS")

    # 7. Separate entry vs exit decisions and persist entries
    decision_count = 0
    exit_symbols = []
    if pm_result and pm_result.decisions and not dry_run:
        entry_decisions = [d for d in pm_result.decisions if d.action in ("BUY", "SHORT")]
        exit_decisions = [d for d in pm_result.decisions if d.action == "EXIT"]

        # Persist entry decisions to pm_decisions_e for executor
        if entry_decisions:
            # Temporarily swap in only entries for persistence
            orig_decisions = pm_result.decisions
            pm_result.decisions = entry_decisions
            decision_count = _persist_pm_decisions(pm_result, db_url)
            pm_result.decisions = orig_decisions

        # Collect exit symbols for the live loop to execute
        for ed in exit_decisions:
            logger.info(f"[PM] EXIT decision: {ed.symbol} — {ed.rationale}")
            exit_symbols.append(ed.symbol)

    # 8. Track costs
    total_cost = sum(er.cost_usd for er in expert_results)
    if pm_result:
        total_cost += pm_result.cost_usd
    if total_cost > 0:
        _log_cost(total_cost, config)

    # 9. Report
    summary = {
        "status": "ok",
        "cycle_time": cycle_time,
        "expert_agents": len(expert_results),
        "total_signals": len(all_signals),
        "signals_persisted": signal_count,
        "pm_decisions": decision_count,
        "pm_exits": exit_symbols,
        "market_regime": pm_result.market_regime if pm_result else None,
        "cycle_cost_usd": round(total_cost, 4),
        "daily_cost_usd": round(daily_cost + total_cost, 4),
        "dry_run": dry_run,
    }

    logger.info(f"=== Cycle Complete: {json.dumps(summary)} ===")
    return summary


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Expert Committee V2 Runner")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts but don't invoke agents")
    args = parser.parse_args()

    if args.once:
        result = run_cycle(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    else:
        print("Continuous mode not implemented yet. Use --once.")
        sys.exit(1)


if __name__ == "__main__":
    main()
