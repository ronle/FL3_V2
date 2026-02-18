# AGENT RULES — FL3_V2
# Re-read this file if you feel uncertain about your instructions.
# These rules are ALWAYS active regardless of context compaction.

## IDENTITY
You are working on FL3_V2, a market-wide options UOA detection system.
Project root: C:\Users\levir\Documents\FL3_V2
Owner: Ron (levir). Local time is PST. Market hours are ET.

## STARTUP PROTOCOL (every session, no exceptions)
1. Get-Date — state current date/time in ET and market status
2. Read CHANGELOG.md (last 20 lines) — state what was last done
3. Read logs/sessions.json — find active session log and tail it
4. Confirm with Ron: "Last session: [X]. Ready to continue with [Y]?"
5. Only then begin work

## SHUTDOWN PROTOCOL (every session, no exceptions)
When Ron says "done", "stop", "close", "end", "wrap up", or similar:
1. STOP current work immediately
2. Append to CHANGELOG.md:
   ## [DATE TIME] — <one-line summary>
   ### Done
   - <completed items>
   ### State
   - <in-progress status>
   ### Next
   - <recommended next steps>
   ### Files Changed
   - <list>
3. Update ## Current Status in CLAUDE.md
4. Confirm: "Session documented. Safe to close."

## SELF-CHECK RULE
Every 15 tool calls, pause and re-read this file (AGENT_RULES.md).
If you have lost track of the startup/shutdown protocol, re-read CLAUDE.md.

## TEMP FILES
ALL scratch/debug/one-off files go in temp/ — never in project root or src dirs.

## DEPLOYMENT SAFETY
NEVER run docker build or gcloud deploy without first checking context size.
NEVER disable V1 ingest jobs (orats_ingest, price_ingest).
NEVER drop or truncate shared tables (orats_daily, orats_daily_returns, spot_prices).

## LOGGING
Use core/session.py for all script logging.
Pattern: from core.session import Session; session = Session.resume_or_create("name")

## IF UNSURE
Ask Ron before making irreversible changes.
When in doubt: read first, ask second, act third.
