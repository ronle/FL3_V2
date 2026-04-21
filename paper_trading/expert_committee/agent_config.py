"""
Agent Configuration — Settings for Claude CLI expert agents.

Each expert is a Claude Code CLI subprocess with MCP tool access.
The PM is an Opus agent that synthesizes expert signals.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentDef:
    """Definition of a single agent."""
    agent_id: str
    expert_ids: list[str]  # Which expert_id(s) this agent covers
    prompt_module: str      # e.g. "prompts.flow_macro"
    model: str              # "sonnet" or "opus"
    budget_usd: float       # Hard cap per invocation
    memory_file: str        # Filename in memory_dir (e.g. "flow_analyst.md")


# Project root (FL3_V2)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = Path(__file__).resolve().parent / "memory"
MCP_CONFIG = PROJECT_ROOT / ".mcp.json"


@dataclass
class AgentConfig:
    """Configuration for the agent-based expert committee."""

    claude_exe: str = "claude"
    mcp_config: Path = MCP_CONFIG
    project_root: Path = PROJECT_ROOT
    memory_dir: Path = MEMORY_DIR

    # Per-agent timeouts
    agent_timeout_sec: int = 420  # 7 min per agent (Technical needs time for deep TA queries)

    # Daily cost ceiling (all agents combined)
    max_daily_cost: float = 30.00

    # Cost tracking file
    cost_log: Path = field(default_factory=lambda: PROJECT_ROOT / "temp" / "expert_costs.jsonl")

    # Agent definitions
    agents: dict = field(default_factory=lambda: {
        "flow_macro": AgentDef(
            agent_id="flow_macro",
            expert_ids=["flow_analyst", "macro_strategist"],
            prompt_module="prompts.flow_macro",
            model="sonnet",
            budget_usd=0.25,
            memory_file="flow_analyst.md",
        ),
        "technical": AgentDef(
            agent_id="technical",
            expert_ids=["technical_analyst"],
            prompt_module="prompts.technical",
            model="sonnet",
            budget_usd=0.25,
            memory_file="technical_analyst.md",
        ),
        "sentiment_risk": AgentDef(
            agent_id="sentiment_risk",
            expert_ids=["sentiment_analyst", "risk_manager"],
            prompt_module="prompts.sentiment_risk",
            model="sonnet",
            budget_usd=0.25,
            memory_file="sentiment_analyst.md",
        ),
        "quant": AgentDef(
            agent_id="quant",
            expert_ids=["quant_analyst"],
            prompt_module="prompts.quant",
            model="sonnet",
            budget_usd=0.25,
            memory_file="quant_analyst.md",
        ),
        "pm": AgentDef(
            agent_id="pm",
            expert_ids=["portfolio_manager"],
            prompt_module="prompts.pm",
            model="opus",
            budget_usd=0.50,
            memory_file="pm.md",
        ),
    })

    # MCP tools each agent gets access to
    allowed_tools: str = "mcp__pg__pg_query_ro,mcp__repo_fs__fs_read,mcp__repo_fs__fs_write"


DEFAULT_AGENT_CONFIG = AgentConfig()
