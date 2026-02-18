# Session: 2026-01-28 FL3_V2 Project Setup & CLI Delegation

## Summary

Initialized FL3_V2 project structure including CLAUDE.md (CLI instructions), prd.json (task tracker with 205 steps across 8 phases), and established CLI delegation protocol for Web Claude to orchestrate CLI Claude as a worker agent. Evaluated FMP API and confirmed Polygon/ORATS/Alpaca remain critical data sources.

## Key Decisions

1. **Separate instruction files**: CLAUDE.md for CLI Claude (project architecture), CLAUDE_WEB.md for Web Claude (session protocols)
2. **CLI delegation pattern**: Use PowerShell heredoc (`@'...'@`) piped to `claude -p --dangerously-skip-permissions`
3. **Three golden rules**: (1) Document everything, (2) Maintain context copies, (3) Git commit every change
4. **FMP API verdict**: Supplementary only — cannot replace Polygon (no options firehose) or Alpaca (no batching)
5. **Data stack finalized**: Polygon (firehose) + ORATS (baselines) + Alpaca (TA bars) + FMP (optional enrichment)

## Files Created

| File | Purpose | Size |
|------|---------|------|
| `CLAUDE.md` | CLI project instructions | 17 KB |
| `CLAUDE_WEB.md` | Web session protocols | ~5 KB |
| `prd.json` | Task tracker (205 steps, 8 phases) | 55 KB |
| `context/` folder | Session summaries | — |

## PRD Summary

- **8 Phases**: Infrastructure → Schema → Core → Firehose → TA → Detection → Backtest → Deploy
- **205 steps** across 37 components
- **Effort estimate**: 106-157 hours
- **Key checkpoints**: CP1 (baseline correlation >0.4), CP3 (triggers <1000/day), CP5 (signal vs random)

## Architecture Decisions

```
┌─────────────────────────────────────────────────────────────────┐
│  POLYGON (CRITICAL) — Options Firehose (T.*), Snapshots        │
├─────────────────────────────────────────────────────────────────┤
│  ORATS (CRITICAL) — Daily aggregates for baseline calibration  │
├─────────────────────────────────────────────────────────────────┤
│  ALPACA (CRITICAL) — Batched price bars for TA (1000 symbols)  │
├─────────────────────────────────────────────────────────────────┤
│  FMP (OPTIONAL) — On-demand TA, earnings calendar, news        │
└─────────────────────────────────────────────────────────────────┘
```

## CLI Delegation Discovery

**Working pattern:**
```powershell
@'
[multi-line prompt]
'@ | claude -p --dangerously-skip-permissions
```

**Tested successfully:**
- File reading and summarization (prd.json, CLAUDE.md)
- Concise responses for orchestration

## Next Steps

- [ ] Git init FL3_V2 repo and initial commit
- [ ] Start Phase 0.1: GCP Project Creation
- [ ] Validate V1 dependencies still running (ORATS, spot prices)

## Earlier Sessions Today

Transcripts available at `/mnt/transcripts/`:
1. `pump-dump-detection-framework.txt` — Greeks formulas, P&D theory
2. `pump-dump-data-gap-analysis.txt` — DB schema gap analysis
3. `fl3-v2-firehose-architecture-prd.txt` — Full architecture redesign
4. `fl3-v2-prd-iterations-v1.0-to-v1.3.txt` — PRD v1.0→v1.3 evolution

---

*Session end: 2026-01-28*
