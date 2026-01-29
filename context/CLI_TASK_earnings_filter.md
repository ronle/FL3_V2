# CLI Task: Implement Filters (5.5, 5.6, 5.7) - URGENT

**Priority:** HIGH - Needed for today's trading day
**Time:** Late night 2026-01-28

## Why This Matters

Today's scan showed THREE problems:
1. **8 of 20 candidates were earnings plays** (IBM, SBUX, etc.) - false positives
2. **PLRX flagged as candidate but it's a DUMP** - 1000 puts, 13.89 P/C ratio, price declining
3. **PLRX is a $1.27 penny stock with 1,072 avg volume** - untradeable garbage

We need ALL THREE filters before market open.

---

## Part 1: Component 5.7 - Liquidity & Price Filter (DO THIS FIRST)

This should be the FIRST filter in the pipeline - no point scoring illiquid junk.

### 5.7.1: Create liquidity filter
**File:** `analysis/liquidity_filter.py` (new)

```python
from typing import Tuple

# Default thresholds - load from config/filters.json if exists
MIN_PRICE = 5.00          # Avoid penny stocks
MIN_AVG_VOLUME = 10000    # Ensure liquidity

def passes_liquidity_filter(
    stock_price: float, 
    avg_daily_volume: int
) -> Tuple[bool, str]:
    """
    Check if ticker passes liquidity requirements.
    Returns: (passes, reason)
    - reason is empty string if passes, otherwise 'PENNY_STOCK' or 'LOW_VOLUME'
    """
    if stock_price < MIN_PRICE:
        return (False, 'PENNY_STOCK')
    if avg_daily_volume < MIN_AVG_VOLUME:
        return (False, 'LOW_VOLUME')
    return (True, '')


def load_filter_config():
    """Load thresholds from config/filters.json if exists"""
    import json
    import os
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'filters.json')
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return {'min_price': MIN_PRICE, 'min_avg_volume': MIN_AVG_VOLUME}
```

### 5.7.2: Create config file
**File:** `config/filters.json`

```json
{
  "liquidity": {
    "min_price": 5.00,
    "min_avg_volume": 10000,
    "max_price": 500.00
  },
  "earnings": {
    "proximity_days": 3,
    "confidence_multiplier": 0.3
  },
  "direction": {
    "bullish_pc_threshold": 0.5,
    "bearish_pc_threshold": 2.0,
    "flat_trend_threshold": 0.01
  }
}
```

---

## Part 2: Component 5.5 - Earnings Proximity Filter

### 5.5.1: Create earnings filter
**File:** `analysis/earnings_filter.py` (new)

```python
from typing import Tuple, Optional

def is_earnings_adjacent(symbol: str, days: int = 3, db_conn=None) -> Tuple[bool, Optional[int]]:
    """
    Check if symbol has earnings within +/- days window.
    Returns: (is_adjacent, days_to_earnings)
    - days_to_earnings: negative for past, positive for future, None if no earnings
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
    # Execute and return (True, days) if found, (False, None) if not
```

---

## Part 3: Component 5.6 - Signal Direction Classifier

### 5.6.1: Create price trend function
**File:** `analysis/direction_classifier.py` (new)

```python
from typing import Tuple

def get_price_trend(symbol: str, days: int = 5, db_conn=None) -> Tuple[float, str]:
    """
    Calculate price trend over N days.
    Returns: (pct_change, trend_label)
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


def classify_direction(put_call_ratio: float, price_trend: str) -> Tuple[str, str]:
    """
    Classify signal direction based on options flow + price trend.
    Returns: (direction, entry_side)
    """
    if put_call_ratio < 0.5 and price_trend in ('UP', 'FLAT'):
        return ('BULLISH', 'LONG')
    elif put_call_ratio > 2.0 and price_trend == 'DOWN':
        return ('BEARISH', 'SHORT')
    else:
        return ('NEUTRAL', 'SKIP')
```

---

## Part 4: Integration Pipeline

**Modify:** `scripts/identify_uoa_candidates.py`

```python
from analysis.liquidity_filter import passes_liquidity_filter
from analysis.earnings_filter import is_earnings_adjacent
from analysis.direction_classifier import get_price_trend, classify_direction

def process_candidates(raw_candidates):
    results = {
        'filtered_liquidity': [],
        'filtered_earnings': [],
        'bullish': [],
        'bearish': [],
        'neutral': []
    }
    
    for c in raw_candidates:
        # STEP 1: Liquidity filter (first!)
        passes, reason = passes_liquidity_filter(c.stock_price, c.avg_daily_volume)
        if not passes:
            results['filtered_liquidity'].append((c.symbol, reason, c.stock_price, c.avg_daily_volume))
            continue
        
        # STEP 2: Score and apply earnings filter
        score = calculate_base_score(c)
        is_earnings, days_to = is_earnings_adjacent(c.symbol)
        if is_earnings:
            c.earnings_flag = True
            c.earnings_days = days_to
            score *= 0.3
            results['filtered_earnings'].append(c)
            continue  # Or keep in separate bucket
        
        # STEP 3: Direction classification
        pct_change, trend = get_price_trend(c.symbol)
        direction, entry_side = classify_direction(c.put_call_ratio, trend)
        
        c.signal_direction = direction
        c.entry_side = entry_side
        c.final_score = score
        
        # STEP 4: Bucket by direction
        if direction == 'BULLISH':
            results['bullish'].append(c)
        elif direction == 'BEARISH':
            results['bearish'].append(c)
        else:
            results['neutral'].append(c)
    
    return results
```

---

## Expected Output Format

```
================================================================================
                    FL3 V2 UOA CANDIDATES - 2026-01-29
================================================================================

=== FILTERED: LIQUIDITY (Not Tradeable) ===
Symbol | Price  | Avg Vol | Reason
-------|--------|---------|------------
PLRX   | $1.27  | 1,072   | PENNY_STOCK
TBPH   | $19.03 | 4,048   | LOW_VOLUME
VTEB   | $50.59 | 5,417   | LOW_VOLUME

=== FILTERED: EARNINGS (0.3x Confidence) ===
Symbol | Raw Score | Adj Score | Direction | Earnings
-------|-----------|-----------|-----------|----------
IBM    | 110       | 33        | BULLISH   | TODAY (0d)
SBUX   | 102       | 31        | BULLISH   | TODAY (0d)
WDC    | 103       | 31        | NEUTRAL   | TOMORROW (+1d)

=== BULLISH CANDIDATES (LONG) ===
Symbol | Score | P/C Ratio | 5d Trend | Price  | Avg Vol
-------|-------|-----------|----------|--------|--------
AAOI   | 109   | 0.32      | +2.1%    | $44.63 | 32,928
UUUU   | 109   | 0.41      | +5.3%    | $27.48 | 127,475
NET    | 92    | 0.38      | +1.2%    | $184.75| 29,398

=== BEARISH CANDIDATES (SHORT) ===
Symbol | Score | P/C Ratio | 5d Trend | Price  | Avg Vol
-------|-------|-----------|----------|--------|--------
(none after PLRX filtered)

=== NEUTRAL (SKIP) ===
Symbol | Score | P/C Ratio | 5d Trend | Reason
-------|-------|-----------|----------|--------
VIAV   | 46    | 1.20      | -0.5%    | Mixed signals

================================================================================
PIPELINE SUMMARY
================================================================================
Raw candidates:      100
Liquidity filtered:  12 (PENNY_STOCK: 5, LOW_VOLUME: 7)
Earnings filtered:   8
Remaining:           80
  - BULLISH (LONG):  42
  - BEARISH (SHORT): 15
  - NEUTRAL (SKIP):  23
================================================================================
```

---

## Test After Implementation

```bash
cd C:\Users\levir\Documents\FL3_V2
python scripts/identify_uoa_candidates.py
```

### Verify:
- [ ] PLRX filtered out (penny stock $1.27)
- [ ] TBPH, VTEB filtered out (low volume)
- [ ] IBM/SBUX flagged as earnings
- [ ] AAOI/UUUU show as BULLISH/LONG
- [ ] Pipeline summary shows filter counts
