"""
Quick verification of AAOI data discrepancy

We expected:
- Jan 27: $37.19 -> Jan 28: $45.23 (+21%)

Polygon shows:
- Jan 27: $26.67 -> Jan 28: $26.53 (flat)

Let's verify with multiple sources
"""

import requests
from datetime import datetime

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"

def get_stock_history(ticker, start, end):
    """Get stock price history from Polygon."""
    url = f"{BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "apiKey": API_KEY}
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        return data.get("results", [])
    return []

print("="*70)
print("AAOI PRICE VERIFICATION")
print("="*70)

# Get AAOI prices for Jan 2025
bars = get_stock_history("AAOI", "2025-01-20", "2025-01-31")

print("\nAAOI Daily Prices (Polygon Adjusted):")
print("-"*70)
for bar in bars:
    ts_ms = bar.get("t", 0)
    dt = datetime.fromtimestamp(ts_ms / 1000)
    o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
    vol = bar.get("v", 0)
    
    change_pct = ((c - o) / o * 100) if o else 0
    
    print(f"{dt.strftime('%Y-%m-%d %a')} | Open: ${o:>7.2f} | High: ${h:>7.2f} | "
          f"Low: ${l:>7.2f} | Close: ${c:>7.2f} | Change: {change_pct:>+6.1f}% | Vol: {vol:>12,.0f}")

print("\n" + "="*70)
print("WHAT WE EXPECTED (from earlier discussion):")
print("-"*70)
print("Jan 27: $37.19 (before signal)")
print("Jan 28: $45.23 (+21%) - Zacks Buy report, AI hype, 8-year high")
print("Jan 29: $38.98 (-13.8%) - Profit taking, reversal")

print("\n" + "="*70)
print("POSSIBLE EXPLANATIONS:")
print("-"*70)
print("""
1. DATE MISMATCH: The big move happened on a different date
2. SPLIT/ADJUSTMENT: Polygon's adjusted prices differ from raw
3. WRONG TICKER: We may have confused AAOI with another symbol
4. DATA ERROR: Either our earlier data or Polygon is wrong

Let's check unadjusted prices too...
""")

# Get unadjusted prices
url = f"{BASE_URL}/v2/aggs/ticker/AAOI/range/1/day/2025-01-20/2025-01-31"
params = {"adjusted": "false", "sort": "asc", "apiKey": API_KEY}
response = requests.get(url, params=params)

if response.status_code == 200:
    data = response.json()
    bars = data.get("results", [])
    
    print("\nAAOI Daily Prices (UNADJUSTED):")
    print("-"*70)
    for bar in bars:
        ts_ms = bar.get("t", 0)
        dt = datetime.fromtimestamp(ts_ms / 1000)
        o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
        
        print(f"{dt.strftime('%Y-%m-%d %a')} | Open: ${o:>7.2f} | High: ${h:>7.2f} | "
              f"Low: ${l:>7.2f} | Close: ${c:>7.2f}")

# Let's also check if there was news/split
print("\n" + "="*70)
print("CHECKING FOR STOCK SPLITS...")
print("-"*70)

url = f"{BASE_URL}/v3/reference/splits"
params = {"ticker": "AAOI", "apiKey": API_KEY}
response = requests.get(url, params=params)

if response.status_code == 200:
    data = response.json()
    splits = data.get("results", [])
    if splits:
        print("Found splits:")
        for s in splits:
            print(f"  {s.get('execution_date')} | {s.get('split_from')}:{s.get('split_to')}")
    else:
        print("No splits found for AAOI")
