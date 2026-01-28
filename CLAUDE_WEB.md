# CLAUDE_WEB.md — Web/Chrome Claude Session Protocol

> **Audience**: Claude operating via web interface (claude.ai) or Claude in Chrome
> **Purpose**: Meta-orchestration, CLI delegation, documentation rules, session management

---

## Session Startup (MANDATORY)

Every new session MUST begin with:

1. **Check current date/time**
2. **State market status** (pre-market, RTH, after-hours, weekend)
3. **Read this file** to load session protocols
4. **Check `context/` folder** for recent session summaries
5. **Check git status** to understand current state

---

## Three Golden Rules

### Rule 1: Always Document Everything

- Update relevant `.md` files with decisions and changes
- Update `prd.json` step status (`passes: true/false/null`)
- Document architectural decisions with rationale
- Keep `CHANGELOG.md` current

### Rule 2: Maintain Chat Context

All session summaries go in `FL3_V2/context/` folder:

```
context/
├── 2026-01-28_session1_prd-creation.md
├── 2026-01-28_session2_cli-delegation.md
└── ...
```

**Session summary format:**
```markdown
# Session: [DATE] [TITLE]

## Summary
[2-3 sentence overview]

## Key Decisions
- [Decision 1]
- [Decision 2]

## Files Changed
- [file1]: [what changed]
- [file2]: [what changed]

## Next Steps
- [ ] [Step 1]
- [ ] [Step 2]

## Raw Transcript Location
[If available, path to full transcript]
```

### Rule 3: Git Commit Every Change

After each logical unit of work:
```powershell
git add -A
git commit -m "[component] description of change"
```

Commit message prefixes:
- `[docs]` — Documentation updates
- `[schema]` — Database schema changes
- `[core]` — Core component code
- `[pipeline]` — Pipeline/orchestration code
- `[config]` — Configuration changes
- `[fix]` — Bug fixes

---

## CLI Delegation Protocol

### When to Delegate to CLI

| Task Type | Delegate? | Reason |
|-----------|-----------|--------|
| File analysis (>5KB) | ✅ Yes | CLI can read files directly |
| Code generation | ✅ Yes | CLI can write files |
| Large refactoring | ✅ Yes | CLI handles multi-file edits |
| Simple questions | ❌ No | Overhead not worth it |
| Conversation/planning | ❌ No | Need context continuity |

### CLI Invocation Pattern (PowerShell)

**Single-line prompt:**
```powershell
'Your prompt here' | claude -p --dangerously-skip-permissions
```

**Multi-line prompt (heredoc):**
```powershell
@'
Your multi-line prompt here.
Can span multiple lines.
Include file paths, instructions, etc.
'@ | claude -p --dangerously-skip-permissions
```

### Key CLI Flags

| Flag | Purpose |
|------|---------|
| `-p` / `--print` | Non-interactive mode (required) |
| `--dangerously-skip-permissions` | Bypass file access prompts |
| `--model sonnet` | Use Sonnet (faster, cheaper) |
| `--model opus` | Use Opus (complex tasks) |

### CLI Limitations

- **Stateless**: No memory between calls
- **No conversation context**: Must include all relevant info in prompt
- **Timeout risk**: Long tasks may timeout
- **Output only**: Returns text, doesn't maintain state

### Delegation Template

```powershell
@'
## Task
[What you want done]

## Context
[Relevant background - CLI has no memory]

## Files to Read
- [path1]
- [path2]

## Output Format
[How to structure the response]

## Constraints
- Be concise
- [Other constraints]
'@ | claude -p --dangerously-skip-permissions
```

---

## Project Structure

```
FL3_V2/
├── CLAUDE.md           # CLI Claude instructions (project architecture)
├── CLAUDE_WEB.md       # Web Claude instructions (this file)
├── prd.json            # Task tracker with step-level status
├── context/            # Session summaries (git-tracked)
│   └── YYYY-MM-DD_sessionN_description.md
├── src/                # Source code (future)
├── sql/                # Database migrations (future)
├── tests/              # Test files (future)
└── docs/               # Additional documentation (future)
```

---

## File Locations Reference

| What | Where |
|------|-------|
| V2 Project | `C:\Users\levir\Documents\FL3_V2\` |
| V1 Project | `C:\Users\levir\Documents\FL3\` |
| Session transcripts (Claude's computer) | `/mnt/transcripts/` |
| PRD document | `FL3_V2/prd.json` |

---

## Integration with V1

V1 (`FL3/`) remains operational for:
- ORATS daily ingest
- Spot price ingest
- Existing UOA detection (will be disabled when V2 ready)

V2 shares PostgreSQL database with V1. Coordinate changes carefully.

---

*Last updated: 2026-01-28*
