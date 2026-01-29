# CLI Task: Implement Earnings Filter (5.5) + Direction Classifier (5.6)

**Priority:** HIGH - Needed for today's trading day
**Time:** Late night 2026-01-28

## Why This Matters

Today's scan showed two problems:
1. **8 of 20 candidates were earnings plays** (IBM, SBUX, etc.) - false positives
2. **PLRX flagged as candidate but it's a DUMP** - 1000 puts, 13.89 P/C ratio, price declining

We need BOTH filters before market open.

---

## Part 1: Component 5.5 - Earnings Proximity Filter

### 5.5.1: Create earnings filter
**File:** `analysis/earnings_filter.py` (new)

```python
from typing import Tuple, Optional
import os

def is_earnings_adjacent(symbol: str, days: int = 3, db_conn=None) -> Tuple[bool, Optional[int]]:
    """
    Check if symbol has earnings within +/- days window.
    Returns: (is_adjacent, days_to_earnings) where days_to_earnings is negative for past, positive for future
    """
    query = """
        SELECT event_date - CURRENT_DATE as days_until
        FROM earnings_calendar 
        WHERE symbol = %s 
          AND event_date BETWEEN CURRENT_DATE - %s AND CURRENT_DATE + %s
          AND is_current = true
        ORDER BY ABS(event_date - CURRENT_DATE)
        LIMIT 1
    """
    # Execute query, return (True, days) if found, (False, None) if not
```

### 5.5.2-5.5.5: Integrate into scoring
- 0.3x confidence multiplier for earnings-adjacent
- Add `earnings_flag` and `earnings_days` columns
- Log separately

---

## Part 2: Component 5.6 - Signal Direction Classifier

### 5.6.1: Create price trend function
**File:** `analysis/direction_classifier.py` (new)

```python
def get_price_trend(symbol: str, days: int = 5, db_conn=None) -> Tuple[float, str]:
    """
    Calculate price trend over N days.
    Returns: (pct_change, trend_label)
    - pct_change: e.g., -0.03 for -3%
    - trend_label: 'UP', 'DOWN', or 'FLAT' (within +/- 1%)
    """
    query = """
        WITH prices AS (
            SELECT underlying as price,
                   ROW_NUMBER() OVER (ORDER BY trade_date DESC) as rn
            FROM spot_prices 
            WHERE ticker = %s 
              AND trade_date >= CURRENT_DATE - %s
            ORDER BY trade_date DESC
        )
        SELECT 
            (SELECT price FROM prices WHERE rn = 1) as latest,
            (SELECT price FROM prices ORDER BY rn DESC LIMIT 1) as oldest
    """
    # Calculate: (latest - oldest) / oldest
    # Return ('UP' if > 1%, 'DOWN' if < -1%, else 'FLAT')
```

### 5.6.2: Create direction classifier

```python
def classify_direction(put_call_ratio: float, price_trend: str) -> Tuple[str, str]:
    """
    Classify signal direction based on options flow + price trend.
    Returns: (direction, entry_side)
    
    Logic:
    - Call-heavy (P/C < 0.5) + UP/FLAT trend = BULLISH -> LONG
    - Put-heavy (P/C > 2.0) + DOWN trend = BEARISH -> SHORT
    - Everything else = NEUTRAL -> SKIP or reduced size
    """
    if put_call_ratio < 0.5 and price_trend in ('UP', 'FLAT'):
        return ('BULLISH', 'LONG')
    elif put_call_ratio > 2.0 and price_trend == 'DOWN':
        return ('BEARISH', 'SHORT')
    else:
        return ('NEUTRAL', 'SKIP')
```

### 5.6.3: Schema update

```sql
-- Add to uoa_triggers_v2
ALTER TABLE uoa_triggers_v2 
ADD COLUMN IF NOT EXISTS signal_direction TEXT CHECK (signal_direction IN ('BULLISH', 'BEARISH', 'NEUTRAL')),
ADD COLUMN IF NOT EXISTS entry_side TEXT CHECK (entry_side IN ('LONG', 'SHORT', 'SKIP')),
ADD COLUMN IF NOT EXISTS price_trend_5d NUMERIC(8,4),
ADD COLUMN IF NOT EXISTS earnings_flag BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS earnings_days INTEGER;
```

---

## Part 3: Integration into identify_uoa_candidates.py

```python
from analysis.earnings_filter import is_earnings_adjacent
from analysis.direction_classifier import get_price_trend, classify_direction

def score_candidate(candidate):
    score = candidate.base_score
    
    # 1. Earnings filter
    is_earnings, days_to = is_earnings_adjacent(candidate.symbol)
    if is_earnings:
        candidate.earnings_flag = True
        candidate.earnings_days = days_to
        score *= 0.3
    
    # 2. Direction classification
    pct_change, trend = get_price_trend(candidate.symbol)
    direction, entry_side = classify_direction(candidate.put_call_ratio, trend)
    
    candidate.signal_direction = direction
    candidate.entry_side = entry_side
    candidate.price_trend_5d = pct_change
    
    # 3. Neutral signals get reduced confidence
    if direction == 'NEUTRAL':
        score *= 0.5
    
    candidate.final_score = score
    return candidate
```

---

## Expected Output Format

```
================================================================================
                    FL3 V2 UOA CANDIDATES - 2026-01-29
================================================================================

=== EARNINGS-ADJACENT (0.3x Confidence) ===
Symbol | Raw Score | Adj Score | Direction | Earnings
-------|-----------|-----------|-----------|----------
IBM    | 110       | 33        | BULLISH   | TODAY (0d)
SBUX   | 102       | 31        | BULLISH   | TODAY (0d)
WDC    | 103       | 31        | NEUTRAL   | TOMORROW (+1d)

=== BULLISH CANDIDATES (LONG) ===
Symbol | Score | P/C Ratio | 5d Trend | IV Rank
-------|-------|-----------|----------|--------
AAOI   | 109   | 0.32      | +2.1%    | 98
UUUU   | 109   | 0.41      | +5.3%    | 98
NET    | 92    | 0.38      | +1.2%    | 79

=== BEARISH CANDIDATES (SHORT) ===
Symbol | Score | P/C Ratio | 5d Trend | IV Rank
-------|-------|-----------|----------|--------
PLRX   | 102   | 13.89     | -3.1%    | 84

=== NEUTRAL (SKIP or 0.5x Size) ===
Symbol | Score | P/C Ratio | 5d Trend | Reason
-------|-------|-----------|----------|--------
VIAV   | 46    | 1.20      | -0.5%    | Mixed signals

================================================================================
SUMMARY: 100 candidates -> 23 earnings-filtered, 42 BULLISH, 12 BEARISH, 23 NEUTRAL
================================================================================
```

---

## Test After Implementation

```bash
cd C:\Users\levir\Documents\FL3_V2
python scripts/identify_uoa_candidates.py
```

### Verify:
- [ ] PLRX shows as BEARISH/SHORT (P/C 13.89, declining)
- [ ] IBM/SBUX show earnings flag with reduced score
- [ ] AAOI/UUUU show as BULLISH/LONG (call-heavy, stable/rising)
- [ ] Output clearly separates by direction

---

## DB Connection Reference
```python
import os
import psycopg2

DATABASE_URL = os.environ.get('DATABASE_URL')
# or use: postgresql://FR3_User:xxx@127.0.0.1:5433/fl3
```
