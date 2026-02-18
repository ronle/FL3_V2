# FL3 V2 — CLI Handoff: V28 Improvements

**Date:** February 3, 2026
**Context:** Post-market analysis of V27 trading performance on a -1.4% S&P / -2.3% Nasdaq rotation day
**Priority:** Implement before next market open (Feb 4)

---

## Background

On Feb 3, the V27 system opened 5 new positions (BAC, PHM, ITB, CRH, DELL) plus carried AMZN overnight. The market opened strong then reversed hard in the afternoon — S&P -1.4%, Nasdaq -2.3%, software stocks crushed. Key observations:

- **BAC** (financial) — correct sector, financials outperformed
- **PHM + CRH** (housing/building materials) — reasonable cyclical plays, but 2 positions in same sector = concentration risk
- **ITB** — ETF that slipped through before v27 deployed. Already fixed.
- **DELL** — tech stock in multi-week decline (-11% trailing month), entered on a tech rotation day. This is the most concerning trade.
- **AMZN** — overnight carry bug from prior version, separate issue

The filter is working well (1.1% pass rate, 435/440 rejected). The improvements below add **macro awareness** and **sector diversification** to the existing signal-level filters.

---

## FIX 1: Sector Concentration Limit

**Problem:** System opened PHM, ITB, and CRH — all construction/housing. Correlated positions amplify drawdowns.

**Rule:** Max 2 positions in the same GICS sector. If a new signal would create a 3rd position in the same sector, reject it.

### File: `paper_trading/signal_filter.py`

**Step 1:** Add a sector mapping dictionary after the `ETF_EXCLUSIONS` set (around line 46). This is a lightweight lookup — no API calls needed. Map the most commonly traded symbols. Unknown symbols default to `"Unknown"` and are always allowed (don't penalize missing data).

```python
# Simplified GICS sector mapping for position concentration checks
# Only need sectors for symbols that commonly pass our filters
# Unknown symbols get "Unknown" sector and are always allowed
SECTOR_MAP = {
    # Technology
    'AAPL': 'Technology', 'MSFT': 'Technology', 'NVDA': 'Technology',
    'AMD': 'Technology', 'INTC': 'Technology', 'AVGO': 'Technology',
    'QCOM': 'Technology', 'MU': 'Technology', 'TSM': 'Technology',
    'DELL': 'Technology', 'HPQ': 'Technology', 'IBM': 'Technology',
    'CRM': 'Technology', 'ORCL': 'Technology', 'ADBE': 'Technology',
    'NOW': 'Technology', 'PLTR': 'Technology', 'SNOW': 'Technology',
    'NET': 'Technology', 'CRWD': 'Technology', 'ZS': 'Technology',
    'PANW': 'Technology', 'FTNT': 'Technology', 'MRVL': 'Technology',
    'ON': 'Technology', 'NXPI': 'Technology', 'LRCX': 'Technology',
    'AMAT': 'Technology', 'KLAC': 'Technology', 'SNPS': 'Technology',
    'CDNS': 'Technology', 'TXN': 'Technology', 'ADI': 'Technology',
    'MCHP': 'Technology', 'SMCI': 'Technology',

    # Communication Services
    'META': 'Communication Services', 'GOOG': 'Communication Services',
    'GOOGL': 'Communication Services', 'NFLX': 'Communication Services',
    'DIS': 'Communication Services', 'CMCSA': 'Communication Services',
    'T': 'Communication Services', 'VZ': 'Communication Services',
    'TMUS': 'Communication Services', 'SPOT': 'Communication Services',
    'ROKU': 'Communication Services', 'SNAP': 'Communication Services',
    'PINS': 'Communication Services', 'TTWO': 'Communication Services',
    'EA': 'Communication Services', 'RBLX': 'Communication Services',

    # Consumer Discretionary
    'AMZN': 'Consumer Discretionary', 'TSLA': 'Consumer Discretionary',
    'HD': 'Consumer Discretionary', 'LOW': 'Consumer Discretionary',
    'NKE': 'Consumer Discretionary', 'SBUX': 'Consumer Discretionary',
    'MCD': 'Consumer Discretionary', 'TGT': 'Consumer Discretionary',
    'TJX': 'Consumer Discretionary', 'BKNG': 'Consumer Discretionary',
    'ABNB': 'Consumer Discretionary', 'MAR': 'Consumer Discretionary',
    'GM': 'Consumer Discretionary', 'F': 'Consumer Discretionary',
    'RIVN': 'Consumer Discretionary', 'LCID': 'Consumer Discretionary',
    'CMG': 'Consumer Discretionary', 'YUM': 'Consumer Discretionary',
    'LULU': 'Consumer Discretionary', 'DECK': 'Consumer Discretionary',
    'RCL': 'Consumer Discretionary', 'CCL': 'Consumer Discretionary',
    'ETSY': 'Consumer Discretionary', 'W': 'Consumer Discretionary',
    'DHI': 'Consumer Discretionary', 'LEN': 'Consumer Discretionary',
    'PHM': 'Consumer Discretionary', 'TOL': 'Consumer Discretionary',
    'KBH': 'Consumer Discretionary', 'NVR': 'Consumer Discretionary',
    'MTH': 'Consumer Discretionary', 'MDC': 'Consumer Discretionary',
    'MHO': 'Consumer Discretionary', 'GRMN': 'Consumer Discretionary',

    # Financials
    'JPM': 'Financials', 'BAC': 'Financials', 'WFC': 'Financials',
    'GS': 'Financials', 'MS': 'Financials', 'C': 'Financials',
    'SCHW': 'Financials', 'BLK': 'Financials', 'AXP': 'Financials',
    'V': 'Financials', 'MA': 'Financials', 'PYPL': 'Financials',
    'COF': 'Financials', 'USB': 'Financials', 'PNC': 'Financials',
    'TFC': 'Financials', 'HOOD': 'Financials', 'SOFI': 'Financials',
    'AFRM': 'Financials', 'SQ': 'Financials', 'COIN': 'Financials',

    # Healthcare
    'UNH': 'Healthcare', 'JNJ': 'Healthcare', 'LLY': 'Healthcare',
    'PFE': 'Healthcare', 'ABBV': 'Healthcare', 'MRK': 'Healthcare',
    'TMO': 'Healthcare', 'ABT': 'Healthcare', 'BMY': 'Healthcare',
    'AMGN': 'Healthcare', 'GILD': 'Healthcare', 'REGN': 'Healthcare',
    'VRTX': 'Healthcare', 'MRNA': 'Healthcare', 'ISRG': 'Healthcare',
    'MDT': 'Healthcare', 'SYK': 'Healthcare', 'BSX': 'Healthcare',
    'ELV': 'Healthcare', 'CI': 'Healthcare', 'HCA': 'Healthcare',
    'NVO': 'Healthcare', 'AZN': 'Healthcare',

    # Industrials
    'CAT': 'Industrials', 'DE': 'Industrials', 'BA': 'Industrials',
    'HON': 'Industrials', 'RTX': 'Industrials', 'LMT': 'Industrials',
    'GE': 'Industrials', 'MMM': 'Industrials', 'UPS': 'Industrials',
    'FDX': 'Industrials', 'UNP': 'Industrials', 'CSX': 'Industrials',
    'WM': 'Industrials', 'EMR': 'Industrials', 'ETN': 'Industrials',
    'ITW': 'Industrials', 'GD': 'Industrials', 'NOC': 'Industrials',
    'CRH': 'Industrials', 'VMC': 'Industrials', 'MLM': 'Industrials',

    # Consumer Staples
    'PG': 'Consumer Staples', 'KO': 'Consumer Staples', 'PEP': 'Consumer Staples',
    'COST': 'Consumer Staples', 'WMT': 'Consumer Staples', 'PM': 'Consumer Staples',
    'MO': 'Consumer Staples', 'CL': 'Consumer Staples', 'MDLZ': 'Consumer Staples',
    'KHC': 'Consumer Staples', 'GIS': 'Consumer Staples', 'STZ': 'Consumer Staples',

    # Energy
    'XOM': 'Energy', 'CVX': 'Energy', 'COP': 'Energy',
    'SLB': 'Energy', 'EOG': 'Energy', 'MPC': 'Energy',
    'PSX': 'Energy', 'VLO': 'Energy', 'OXY': 'Energy',
    'DVN': 'Energy', 'HAL': 'Energy', 'FANG': 'Energy',

    # Materials
    'LIN': 'Materials', 'APD': 'Materials', 'SHW': 'Materials',
    'ECL': 'Materials', 'DD': 'Materials', 'NEM': 'Materials',
    'FCX': 'Materials', 'NUE': 'Materials', 'STLD': 'Materials',
    'CLF': 'Materials', 'AA': 'Materials', 'X': 'Materials',

    # Utilities
    'NEE': 'Utilities', 'DUK': 'Utilities', 'SO': 'Utilities',
    'D': 'Utilities', 'AEP': 'Utilities', 'EXC': 'Utilities',
    'SRE': 'Utilities', 'XEL': 'Utilities', 'CEG': 'Utilities',
    'VST': 'Utilities',

    # Real Estate
    'AMT': 'Real Estate', 'PLD': 'Real Estate', 'CCI': 'Real Estate',
    'EQIX': 'Real Estate', 'SPG': 'Real Estate', 'O': 'Real Estate',
    'PSA': 'Real Estate', 'DLR': 'Real Estate',
}

MAX_SECTOR_CONCENTRATION = 2  # Max positions per sector
```

**Important note on PHM vs CRH:** Under GICS, PHM (PulteGroup, homebuilder) is Consumer Discretionary, while CRH (building materials) is Industrials. So they're technically different sectors. However, they're highly correlated in practice. The mapping above uses standard GICS which keeps them separate. If you want tighter control, you could add a `SUBSECTOR_MAP` later — but GICS sectors is the right starting point.

**Step 2:** The sector check should NOT go in `SignalFilter.apply()` because the filter doesn't know about current positions. Instead, add it to `PositionManager.open_position()` where we already check `can_open_position`. 

### File: `paper_trading/position_manager.py`

Add this import at the top of the file (around line 10):

```python
from .signal_filter import SECTOR_MAP, MAX_SECTOR_CONCENTRATION
```

Add this method to the `PositionManager` class (after the `has_position` method, around line 110):

```python
def would_exceed_sector_limit(self, symbol: str) -> bool:
    """Check if adding this symbol would exceed sector concentration limit."""
    new_sector = SECTOR_MAP.get(symbol, "Unknown")

    # Unknown sectors are always allowed
    if new_sector == "Unknown":
        return False

    # Count existing positions in the same sector
    sector_count = 0
    for existing_symbol in self.active_trades:
        if SECTOR_MAP.get(existing_symbol, "Unknown") == new_sector:
            sector_count += 1

    # Also count pending buys
    for pending_symbol in self._pending_buys:
        if SECTOR_MAP.get(pending_symbol, "Unknown") == new_sector:
            sector_count += 1

    if sector_count >= MAX_SECTOR_CONCENTRATION:
        logger.info(
            f"Sector limit: {symbol} ({new_sector}) blocked - "
            f"already {sector_count} positions in {new_sector}"
        )
        return True

    return False
```

Then add the sector check in the `open_position` method. Insert it right after the `has_position` check (around line 250, before the Alpaca position check):

```python
# Check sector concentration
if self.would_exceed_sector_limit(symbol):
    return None
```

### Verification

After implementation, run this test scenario mentally:
- Positions: BAC (Financials), PHM (Consumer Discretionary)
- New signal: CRH (Industrials) → ALLOWED (only 0 Industrials positions)
- New signal: GS (Financials) → ALLOWED (only 1 Financials position, limit is 2)
- New signal: WFC (Financials) → BLOCKED (already BAC + GS = 2 Financials)
- New signal: ZXYZ (Unknown) → ALLOWED (unknown sector, don't penalize)

---

## FIX 2: Market Regime Filter (SPY Intraday Check)

**Problem:** System opened positions during morning strength, then the market reversed. No awareness of broad market direction.

**Rule:** Before opening a new position, check SPY's intraday performance. If SPY is down more than 0.5% from the open, pause new entries. This is a lightweight guard — one REST call to Alpaca for SPY's daily bar.

### File: `paper_trading/config.py`

Add these config parameters to `TradingConfig`:

```python
# Market regime filter
USE_MARKET_REGIME_FILTER: bool = True
MARKET_REGIME_SYMBOL: str = "SPY"       # Benchmark to check
MARKET_REGIME_MAX_DECLINE: float = -0.005  # -0.5% from open = pause entries
```

### File: `paper_trading/position_manager.py`

Add this method to the `PositionManager` class (after `would_exceed_sector_limit`):

```python
async def is_market_regime_ok(self) -> bool:
    """
    Check if broad market is in acceptable regime for new entries.

    Fetches SPY intraday performance. If SPY is down > 0.5% from open,
    block new entries to avoid buying into broad weakness.

    Returns True if OK to enter, False if market is too weak.
    """
    if not self.config.USE_MARKET_REGIME_FILTER:
        return True

    try:
        symbol = self.config.MARKET_REGIME_SYMBOL

        # Get today's open price and current price from Alpaca
        # Use snapshot endpoint for efficiency (1 call)
        import aiohttp
        headers = {
            "APCA-API-KEY-ID": self.trader.api_key,
            "APCA-API-SECRET-KEY": self.trader.secret_key,
        }

        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/snapshot"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Market regime check failed (HTTP {resp.status}), allowing entry")
                    return True

                data = await resp.json()

                daily_bar = data.get("dailyBar", {})
                open_price = daily_bar.get("o", 0)
                close_price = daily_bar.get("c", 0)  # Current/latest in intraday context

                if open_price <= 0:
                    logger.warning("Market regime: no open price, allowing entry")
                    return True

                change_pct = (close_price - open_price) / open_price

                if change_pct < self.config.MARKET_REGIME_MAX_DECLINE:
                    logger.warning(
                        f"Market regime WEAK: {symbol} {change_pct*100:+.2f}% "
                        f"(threshold: {self.config.MARKET_REGIME_MAX_DECLINE*100:.1f}%) "
                        f"— blocking new entries"
                    )
                    return False

                logger.debug(f"Market regime OK: {symbol} {change_pct*100:+.2f}%")
                return True

    except Exception as e:
        logger.warning(f"Market regime check error: {e}, allowing entry")
        return True  # Fail open — don't block on errors
```

Add the market regime check in `open_position`, right after the sector concentration check:

```python
# Check market regime
if not await self.is_market_regime_ok():
    logger.info(f"Skipping {symbol}: market regime too weak")
    return None
```

**Note:** The `self.trader.api_key` and `self.trader.secret_key` attributes need to exist on `AlpacaTrader`. Check the `AlpacaTrader.__init__` method — the keys are likely stored as `self.api_key` and `self.secret_key` already. If not, expose them.

### Verification

- SPY opens at $695, currently at $694 (−0.14%) → entries ALLOWED
- SPY opens at $695, currently at $690 (−0.72%) → entries BLOCKED
- SPY snapshot API fails → entries ALLOWED (fail open)

---

## FIX 3: Multi-Week Momentum Guard

**Problem:** DELL was entered despite being down 11% over the trailing month and near 52-week lows. The uptrend filter (price > 20d SMA) was satisfied, but DELL was in a broader structural decline.

**Rule:** Add a check against the 50-day SMA in addition to the existing 20-day SMA check. Signal must have price above BOTH the 20d and 50d SMA to pass.

### File: `paper_trading/signal_filter.py`

**Step 1:** Update the `Signal` dataclass to include a 50d SMA field. Add after the `sma_20_prior` field (around line 73):

```python
sma_50_prior: Optional[float] = None
```

**Step 2:** Update the `SignalFilter.apply()` method. Add a new check after the existing trend check block (around line 175, after the trend/uptrend check):

```python
# Check 50d SMA (multi-week momentum)
if signal.sma_50_prior is not None and signal.price_at_signal is not None:
    if signal.price_at_signal < signal.sma_50_prior:
        reasons.append(f"below 50d SMA ({signal.price_at_signal:.2f} < {signal.sma_50_prior:.2f})")
        self.filter_reasons.setdefault("sma50", 0)
        self.filter_reasons["sma50"] += 1
```

Also initialize the counter in `__init__` filter_reasons dict and `reset_stats`:

```python
# In __init__, add to self.filter_reasons:
"sma50": 0,
```

**Step 3:** Update `SignalGenerator` to calculate and populate 50d SMA.

In the `fetch_ta_for_symbol` method (around line 250), after the `sma_20` calculation, add:

```python
sma_50 = self._calculate_sma(closes, 50)
```

And include it in the `ta_data` dict:

```python
ta_data = {
    "rsi_14": rsi_14,
    "macd_hist": macd_hist,
    "sma_20": sma_20,
    "sma_50": sma_50,        # ADD THIS
    "last_close": last_close,
    "trend": trend,
}
```

**Note:** The `fetch_ta_for_symbol` method currently fetches 45 days of bar data. For a 50d SMA, we need at least 50 bars. Update the fetch call:

```python
# Change from:
bar_data = await fetcher.get_bars(symbol, days=45)
# Change to:
bar_data = await fetcher.get_bars(symbol, days=70)
```

And update the minimum bars check:

```python
# Change from:
if not bar_data.bars or len(bar_data.bars) < 15:
# Change to:
if not bar_data.bars or len(bar_data.bars) < 20:
```

(Keep 20 as minimum — the 50d SMA will simply be None if insufficient data, which is handled gracefully.)

**Step 4:** Update `create_signal` method to populate the new field. In the return statement (around line 345):

```python
return Signal(
    symbol=symbol,
    detection_time=datetime.now(),
    score=score,
    notional=notional,
    contracts=contracts,
    rsi_14_prior=ta.get("rsi_14"),
    macd_hist_prior=ta.get("macd_hist"),
    sma_20_prior=ta.get("sma_20"),
    sma_50_prior=ta.get("sma_50"),    # ADD THIS
    price_at_signal=price,
    trend=final_trend,
    # ... rest unchanged
)
```

**Step 5:** Also update `premarket_ta_cache.py` if it pre-computes TA data. It should also calculate and store `sma_50` so the cache has it available for the morning signals (which are the most common).

### File: `paper_trading/premarket_ta_cache.py`

Search for where `sma_20` is calculated and add `sma_50` alongside it. Then include `"sma_50"` in the output dictionary that gets saved to `daily_ta_cache.json`.

### Verification

- DELL: price $119.46, 20d SMA maybe ~$122 (barely above), 50d SMA likely ~$128+ → BLOCKED by 50d check
- AAPL: price $235, 20d SMA $230, 50d SMA $228 → ALLOWED (above both)
- New IPO with only 30 days data: sma_50 = None → check skipped, ALLOWED

---

## FIX 4: Fix Numeric Overflow in signal_evaluations

**Problem:** ~50+ errors from notional values exceeding column precision in `signal_evaluations` table.

### SQL DDL (execute against GCP PostgreSQL):

First, check the current column type:

```sql
SELECT column_name, data_type, numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_name = 'signal_evaluations' AND column_name = 'notional';
```

Then alter:

```sql
ALTER TABLE signal_evaluations
    ALTER COLUMN notional TYPE NUMERIC(15,2);
```

If other numeric columns also have overflow issues (ratio, call_pct, etc.), widen them too:

```sql
ALTER TABLE signal_evaluations
    ALTER COLUMN ratio TYPE NUMERIC(12,4),
    ALTER COLUMN call_pct TYPE NUMERIC(8,4),
    ALTER COLUMN sweep_pct TYPE NUMERIC(8,4);
```

---

## FIX 5: Add Logging for New Filters

To track the effectiveness of the new filters, add dashboard/log visibility.

### File: `paper_trading/position_manager.py`

Add to the `DailyStats` dataclass:

```python
sector_blocks: int = 0
regime_blocks: int = 0
```

And increment them in `open_position` when each check blocks:

```python
# After sector check blocks:
self.daily_stats.sector_blocks += 1

# After regime check blocks:
self.daily_stats.regime_blocks += 1
```

Include these in `get_daily_summary()`:

```python
"sector_blocks": stats.sector_blocks,
"regime_blocks": stats.regime_blocks,
```

---

## Implementation Order

1. **FIX 4** (SQL) — Quick win, fixes data integrity. No code changes needed.
2. **FIX 1** (Sector concentration) — Add `SECTOR_MAP` dict and `would_exceed_sector_limit()` method.
3. **FIX 3** (50d SMA) — Add `sma_50` field and filter check. Update bar fetch to 70 days.
4. **FIX 2** (Market regime) — Add `is_market_regime_ok()` with SPY snapshot check.
5. **FIX 5** (Logging) — Add counters to DailyStats.

## Files Modified

| File | Changes |
|------|---------|
| `paper_trading/signal_filter.py` | Add `SECTOR_MAP`, `MAX_SECTOR_CONCENTRATION`, `sma_50_prior` field, `sma50` filter check, `sma50` counter |
| `paper_trading/position_manager.py` | Add `would_exceed_sector_limit()`, `is_market_regime_ok()`, sector/regime checks in `open_position()`, `DailyStats` counters |
| `paper_trading/config.py` | Add `USE_MARKET_REGIME_FILTER`, `MARKET_REGIME_SYMBOL`, `MARKET_REGIME_MAX_DECLINE` |
| `paper_trading/premarket_ta_cache.py` | Add `sma_50` calculation and cache storage |
| `sql/create_tables_v2.sql` | No change needed (ALTER runs directly) |

## Testing Checklist

- [ ] SECTOR_MAP covers all 6 symbols from today (AMZN, BAC, PHM, ITB, CRH, DELL)
- [ ] Sector limit blocks 3rd position in same sector
- [ ] Unknown symbols are allowed through sector filter
- [ ] SPY regime check returns True when SPY > -0.5%
- [ ] SPY regime check returns True on API failure (fail open)
- [ ] SPY regime check returns False when SPY < -0.5%
- [ ] 50d SMA filter rejects symbols below 50d SMA
- [ ] 50d SMA filter allows symbols when sma_50 is None (insufficient data)
- [ ] Bars fetched increased to 70 days (from 45)
- [ ] signal_evaluations.notional column widened to NUMERIC(15,2)
- [ ] DailyStats includes sector_blocks and regime_blocks counts
- [ ] All new filter blocks are logged at INFO level

## Deployment

After all fixes verified:

```powershell
cd C:\Users\levir\Documents\FL3_V2
# Run the ALTER TABLE SQL against GCP PostgreSQL first
# Then build and deploy:
docker build -t us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2/firehose:v28 .
docker push us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2/firehose:v28
# Update Cloud Run job with :v28 tag
```
