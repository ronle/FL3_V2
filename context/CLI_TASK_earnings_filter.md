# CLI Task: Implement Earnings Proximity Filter (Component 5.5)

**Priority:** HIGH - Needed for today's trading day
**Time:** Late night 2026-01-28

## Context
Today's UOA scan showed 8 of top 20 candidates were earnings plays (IBM, SBUX, WDC, etc.). 
IBM spiked 9% AH on earnings - this is NOT a P&D signal, it's legitimate earnings volatility.
We need to filter/flag these before market open.

## What Exists
- `earnings_calendar` table in database (26 MB, populated)
- V1 job `fr3-earnings-calendar` updates it daily at 4am PT
- Table schema: symbol, event_date, hour, eps_estimate, eps_actual, is_current

## Implement Component 5.5 (5 steps)

### 5.5.1: Create is_earnings_adjacent() function
Location: `analysis/earnings_filter.py` (new file)

```python
def is_earnings_adjacent(symbol: str, days: int = 3, db_conn=None) -> tuple[bool, int | None]:
    """
    Check if symbol has earnings within +/- days window.
    Returns: (is_adjacent, days_to_earnings)
    """
    query = """
        SELECT event_date, 
               event_date - CURRENT_DATE as days_until
        FROM earnings_calendar 
        WHERE symbol = %s 
          AND event_date BETWEEN CURRENT_DATE - %s AND CURRENT_DATE + %s
          AND is_current = true
        ORDER BY ABS(event_date - CURRENT_DATE)
        LIMIT 1
    """
    # Return (True, days_until) if found, (False, None) if not
```

### 5.5.2: Integrate into UOA scoring
Location: Modify `uoa/detector_v2.py` or `scripts/identify_uoa_candidates.py`

```python
from analysis.earnings_filter import is_earnings_adjacent

def score_candidate(candidate):
    # Existing scoring...
    
    is_adjacent, days_to = is_earnings_adjacent(candidate.symbol)
    if is_adjacent:
        candidate.earnings_flag = True
        candidate.earnings_days = days_to
        candidate.confidence *= 0.3  # Heavy penalty
    
    return candidate
```

### 5.5.3: Add earnings_flag to schema
Check if `uoa_triggers_v2` needs column:
```sql
ALTER TABLE uoa_triggers_v2 
ADD COLUMN IF NOT EXISTS earnings_flag BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS earnings_days_to INTEGER;
```

### 5.5.4: Log earnings-filtered candidates separately
In the UOA candidate output, add section:
```
=== EARNINGS-ADJACENT (Reduced Confidence) ===
IBM  | Score: 33 (was 110) | Earnings: TODAY
SBUX | Score: 31 (was 102) | Earnings: TODAY
...

=== CLEAN CANDIDATES (Full Confidence) ===  
AAOI | Score: 109 | No earnings nearby
UUUU | Score: 109 | No earnings nearby
...
```

### 5.5.5: Validate filter effectiveness
Add to output:
```
Filter Stats:
- Total candidates: 100
- Earnings-adjacent: 23 (filtered)
- Clean candidates: 77
```

## Test Command
After implementation, run:
```bash
python scripts/identify_uoa_candidates.py
```

Should show earnings-flagged candidates separately with reduced scores.

## DB Connection
```python
# Use existing pattern from other scripts
import os
DATABASE_URL = os.environ.get('DATABASE_URL')
# or
from config import get_db_connection
```

## Success Criteria
- [ ] IBM, SBUX, WDC, CMCSA flagged as earnings-adjacent
- [ ] Their scores reduced by 0.3x multiplier
- [ ] AAOI, UUUU, NET remain at full confidence
- [ ] Output clearly separates earnings vs clean candidates
