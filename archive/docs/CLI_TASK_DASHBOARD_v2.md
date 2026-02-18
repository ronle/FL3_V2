# CLI Task: Active Signals Dashboard

## Overview

Real-time dashboard showing ONLY signals that pass all filters. No rejection logging.

---

## Part 1: Active Signals Table

### Create Table

```sql
CREATE TABLE active_signals (
    id SERIAL PRIMARY KEY,
    detected_at TIMESTAMP NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    
    -- Signal metrics
    notional DECIMAL(15,2),
    ratio DECIMAL(10,2),
    call_pct DECIMAL(5,4),
    sweep_pct DECIMAL(5,4),
    num_strikes INTEGER,
    contracts INTEGER,
    
    -- TA at signal time
    rsi_14 DECIMAL(5,2),
    trend INTEGER,
    price_at_signal DECIMAL(10,2),
    
    -- Score
    score INTEGER,
    
    -- Action status
    action VARCHAR(10) DEFAULT 'BUY',  -- BUY, HOLDING, CLOSED
    trade_placed BOOLEAN DEFAULT FALSE,
    entry_price DECIMAL(10,2),
    exit_price DECIMAL(10,2),
    exit_time TIMESTAMP,
    pnl_pct DECIMAL(6,3),
    
    CONSTRAINT idx_active_signals_unique UNIQUE (detected_at, symbol)
);

CREATE INDEX idx_active_signals_date ON active_signals (DATE(detected_at));
```

### Signal Filter Logic

Only log when ALL filters pass:

```python
def evaluate_and_log(signal, ta_data):
    """Only log signals that pass ALL filters"""
    
    score = calculate_score(signal)
    
    # Check all filters
    if (score >= 10 and 
        ta_data.trend == 1 and 
        ta_data.rsi_14 < 50 and 
        signal.notional >= 50000):
        
        # PASSED - log to database
        db.insert('active_signals', {
            'detected_at': signal.timestamp,
            'symbol': signal.symbol,
            'notional': signal.notional,
            'ratio': signal.ratio,
            'call_pct': signal.call_pct,
            'sweep_pct': signal.sweep_pct,
            'num_strikes': signal.num_strikes,
            'contracts': signal.contracts,
            'rsi_14': ta_data.rsi_14,
            'trend': ta_data.trend,
            'price_at_signal': signal.price,
            'score': score,
            'action': 'BUY'
        })
        
        # Also push to Google Sheet
        dashboard.log_signal(signal, ta_data, score)
        
        return True
    
    return False  # Don't log rejections
```

---

## Part 2: Google Sheet Dashboard

### Sheet Structure

**Tab 1: Active Signals**
| Time | Symbol | Score | RSI | Ratio | Notional | Price | Action |
|------|--------|-------|-----|-------|----------|-------|--------|
| 11:58 | AMZN | 12 | 42.1 | 177x | $1.77M | $232.50 | BUY |

**Tab 2: Positions**
| Symbol | Entry | Current | P/L % | Status |
|--------|-------|---------|-------|--------|
| AMZN | $232.50 | $234.10 | +0.69% | HOLDING |

**Tab 3: Closed Today**
| Symbol | Entry | Exit | P/L % | Result |
|--------|-------|------|-------|--------|
| META | $612.30 | $618.50 | +1.01% | WIN |

### Integration Code

```python
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET_ID = 'your-sheet-id-here'

class Dashboard:
    def __init__(self):
        creds = Credentials.from_service_account_file(
            'credentials.json', scopes=SCOPES
        )
        self.client = gspread.authorize(creds)
        self.sheet = self.client.open_by_key(SHEET_ID)
        self.signals_tab = self.sheet.worksheet('Active Signals')
        self.positions_tab = self.sheet.worksheet('Positions')
        self.closed_tab = self.sheet.worksheet('Closed Today')
    
    def log_signal(self, signal, ta_data, score):
        """Add passed signal to Active Signals tab"""
        row = [
            signal.timestamp.strftime('%H:%M:%S'),
            signal.symbol,
            score,
            f"{ta_data.rsi_14:.1f}",
            f"{signal.ratio:.1f}x",
            f"${signal.notional:,.0f}",
            f"${signal.price:.2f}",
            'BUY'
        ]
        self.signals_tab.append_row(row)
    
    def update_position(self, symbol, entry_price, current_price, status='HOLDING'):
        """Update or add position"""
        pnl = (current_price - entry_price) / entry_price * 100
        row = [symbol, f"${entry_price:.2f}", f"${current_price:.2f}", 
               f"{pnl:+.2f}%", status]
        
        try:
            cell = self.positions_tab.find(symbol)
            self.positions_tab.update(f'A{cell.row}:E{cell.row}', [row])
        except:
            self.positions_tab.append_row(row)
    
    def close_position(self, symbol, entry_price, exit_price):
        """Move position to Closed tab"""
        pnl = (exit_price - entry_price) / entry_price * 100
        result = 'WIN' if pnl > 0 else 'LOSS'
        
        # Add to Closed tab
        self.closed_tab.append_row([
            symbol, f"${entry_price:.2f}", f"${exit_price:.2f}",
            f"{pnl:+.2f}%", result
        ])
        
        # Remove from Positions tab
        try:
            cell = self.positions_tab.find(symbol)
            self.positions_tab.delete_rows(cell.row)
        except:
            pass
    
    def clear_daily(self):
        """Clear at start of day"""
        for tab in [self.signals_tab, self.positions_tab, self.closed_tab]:
            tab.clear()
        
        # Add headers
        self.signals_tab.append_row(['Time', 'Symbol', 'Score', 'RSI', 'Ratio', 'Notional', 'Price', 'Action'])
        self.positions_tab.append_row(['Symbol', 'Entry', 'Current', 'P/L %', 'Status'])
        self.closed_tab.append_row(['Symbol', 'Entry', 'Exit', 'P/L %', 'Result'])


dashboard = Dashboard()
```

---

## Part 3: Integration Points

### On Signal Pass
```python
# In signal_filter.py
if passes_all_filters(signal, ta_data):
    dashboard.log_signal(signal, ta_data, score)
```

### On Trade Placed
```python
# In alpaca_trader.py
def place_order(symbol, price):
    # ... execute order ...
    dashboard.update_position(symbol, price, price, 'HOLDING')
```

### On Price Update (every 1 min)
```python
# In position_manager.py
def update_positions():
    for position in active_positions:
        current_price = get_current_price(position.symbol)
        dashboard.update_position(position.symbol, position.entry, current_price)
```

### On EOD Close
```python
# In eod_closer.py
def close_all_positions():
    for position in active_positions:
        exit_price = close_position(position.symbol)
        dashboard.close_position(position.symbol, position.entry, exit_price)
```

---

## Deployment

1. Create Google Sheet with 3 tabs
2. Setup service account + share sheet
3. Add `gspread` to requirements
4. Deploy updated Cloud Run
5. Test with `--dry-run`

---

## What You'll See

**Active Signals (only passed):**
| Time | Symbol | Score | RSI | Ratio | Notional | Price | Action |
|------|--------|-------|-----|-------|----------|-------|--------|
| 11:58 | AMZN | 12 | 42.1 | 177x | $1.77M | $232.50 | BUY |
| 14:22 | META | 11 | 38.5 | 95x | $950K | $612.30 | BUY |

**Positions:**
| Symbol | Entry | Current | P/L % | Status |
|--------|-------|---------|-------|--------|
| AMZN | $232.50 | $234.10 | +0.69% | HOLDING |

**Closed Today:**
| Symbol | Entry | Exit | P/L % | Result |
|--------|-------|------|-------|--------|
| META | $612.30 | $618.50 | +1.01% | WIN |

Clean, actionable, mobile-friendly.
