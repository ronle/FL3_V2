# CLI Bug Fixes — January 31, 2026

**Priority:** CRITICAL — Must complete before Monday market open
**Estimated Time:** 1-2 hours
**Test Mode:** Use `--dry-run` for all testing

---

## Context

First paper trading day (Jan 30) was profitable (+$24.12) but exposed 4 critical bugs:
- Bought SPY 3x, AMZN 2x, AAPL 2x (duplicate symbols)
- 6 positions opened instead of max 3
- ETFs traded (our edge is on individual stocks)
- `trade_placed` not updated in DB after fills

---

## Bug #1: Duplicate Symbol Prevention (HIGHEST PRIORITY)

**File:** `paper_trading/main.py`

**Problem:** The `open_position()` call doesn't check if we already have that symbol.

**Root Cause:** Even though `position_manager.has_position()` exists, it may not be called before `open_position()` in the signal processing flow.

**Fix Location:** Find where signals trigger trades in `main.py` (likely in `process_signal()` or similar).

**Required Check (add BEFORE calling open_position):**
```python
# Check 1: Already have this symbol?
if position_manager.has_position(signal.symbol):
    logger.info(f"Skipping {signal.symbol}: already have position")
    return

# Check 2: At max positions?
if not position_manager.can_open_position:
    logger.info(f"Skipping {signal.symbol}: max positions reached ({position_manager.num_positions})")
    return
```

**Verification:** The `has_position()` method in `position_manager.py` already checks both `active_trades` and `_pending_buys` — that's correct. Just ensure it's being called.

---

## Bug #2: Max 3 Positions Enforcement

**File:** `paper_trading/position_manager.py`

**Current Code (line ~100):**
```python
@property
def can_open_position(self) -> bool:
    """Check if we can open a new position (includes pending orders)."""
    total_positions = self.num_positions + self.num_pending
    return total_positions < self.config.MAX_CONCURRENT_POSITIONS
```

**Status:** Logic looks correct. The bug is likely that `can_open_position` isn't being checked at the right place in the flow.

**Fix:** Ensure the check happens in `main.py` BEFORE any position opening logic:
```python
if not position_manager.can_open_position:
    logger.info(f"Max positions reached ({position_manager.num_positions} active + {position_manager.num_pending} pending)")
    return
```

---

## Bug #3: ETF Exclusion Filter

**File:** `paper_trading/signal_filter.py`

**Add this constant at top of file (after imports):**
```python
# ETFs to exclude — our edge is on individual stocks
ETF_EXCLUSIONS = {
    'SPY', 'QQQ', 'IWM', 'DIA',  # Major index ETFs
    'XLE', 'XLF', 'XLK', 'XLV', 'XLI', 'XLU', 'XLP', 'XLY', 'XLB', 'XLRE',  # Sector SPDRs
    'VTI', 'VOO', 'VXX', 'UVXY', 'SQQQ', 'TQQQ', 'SPXU', 'SPXS',  # Other common ETFs
    'GLD', 'SLV', 'USO', 'UNG',  # Commodity ETFs
    'TLT', 'HYG', 'LQD', 'JNK',  # Bond ETFs
    'EEM', 'EFA', 'VWO', 'IEMG',  # International ETFs
    'ARKK', 'ARKG', 'ARKW', 'ARKF',  # ARK ETFs
}
```

**Add this check in the `apply()` method, at the START (before other filters):**
```python
def apply(self, signal: Signal) -> FilterResult:
    """Apply all filters to a signal."""
    self.total_signals += 1
    reasons = []

    # Check ETF exclusion FIRST
    if signal.symbol in ETF_EXCLUSIONS:
        reasons.append(f"ETF excluded ({signal.symbol})")
        self.filter_reasons["etf"] += 1
        # Log and return early
        self._log_evaluation(signal, False, "ETF excluded")
        return FilterResult(signal=signal, passed=False, reasons=reasons)

    # ... rest of existing filters ...
```

**Also add to `filter_reasons` dict in `__init__`:**
```python
self.filter_reasons: Dict[str, int] = {
    "etf": 0,  # ADD THIS
    "score": 0,
    "trend": 0,
    "rsi": 0,
    "notional": 0,
    "sentiment_mentions": 0,
    "sentiment_negative": 0,
}
```

---

## Bug #4: Update `trade_placed` in DB After Order Fill

**File:** `paper_trading/position_manager.py`

**Current Code (around line 240):** There's already a call to `update_signal_trade_placed()`:
```python
# Update active_signals in DB
db_url = os.environ.get("DATABASE_URL")
if db_url:
    update_signal_trade_placed(db_url, symbol, trade.entry_price)
```

**Check:** Verify `update_signal_trade_placed()` in `dashboard.py` actually sets `trade_placed = TRUE`.

**File:** `paper_trading/dashboard.py`

**Find the `update_signal_trade_placed()` function and verify it does:**
```python
def update_signal_trade_placed(db_url: str, symbol: str, entry_price: float):
    """Update active_signals when trade is placed."""
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            UPDATE active_signals 
            SET trade_placed = TRUE, 
                entry_price = %s,
                updated_at = NOW()
            WHERE symbol = %s 
            AND trade_placed = FALSE
            AND DATE(detected_at) = CURRENT_DATE
        """, (entry_price, symbol))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to update trade_placed for {symbol}: {e}")
```

If this function doesn't exist or doesn't set `trade_placed = TRUE`, create/fix it.

---

## Testing Checklist

After making changes:

1. **Dry run test:**
   ```bash
   cd C:\Users\levir\Documents\FL3_V2
   python start_paper_trading.py --dry-run
   ```

2. **Verify ETF filtering:** Send a fake SPY signal, confirm it's rejected with "ETF excluded"

3. **Verify duplicate prevention:** Try to process same symbol twice, confirm second is rejected

4. **Verify max positions:** With 3 positions open, confirm 4th signal is rejected

5. **Check logs for:** `"Skipping {symbol}: already have position"` and `"Max positions reached"`

---

## Files to Modify

| File | Changes |
|------|---------|
| `paper_trading/signal_filter.py` | Add `ETF_EXCLUSIONS` set, add ETF check in `apply()` |
| `paper_trading/main.py` | Add `has_position()` and `can_open_position` checks before trading |
| `paper_trading/dashboard.py` | Verify/fix `update_signal_trade_placed()` function |

---

## Updated Filter Stack (After Fixes)

```
Signal passes ALL:
1. NOT in ETF_EXCLUSIONS  ✅ NEW
2. Score >= 10            ✅
3. Uptrend                ✅
4. RSI < 50               ✅
5. Notional >= $50K       ✅
6. Mentions < 5           ✅ (sentiment)
7. Sentiment >= 0         ✅ (sentiment)
8. Time < 3:50 PM ET      ✅ (already in v23)
9. No existing position   ✅ NEW (position_manager check)
10. < 3 positions open    ✅ NEW (position_manager check)
```

---

## Completion Criteria

- [x] ETF signals rejected with clear log message
- [x] Duplicate symbol signals rejected
- [x] 4th position attempt rejected when 3 open
- [x] `trade_placed` field updates in DB after fills
- [x] All tests pass in `--dry-run` mode
- [x] Code committed and ready for Monday

---

## Do NOT Change

- Exit strategy (hold to close at 3:55 PM) — WORKING
- Trailing stops — DO NOT ADD (they fail adversarial testing)
- Score threshold (10) — VALIDATED
- RSI threshold (50) — VALIDATED
- Sentiment filter — VALIDATED
