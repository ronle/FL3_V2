"""
AAOI Verification - CORRECT DATES (Jan 2026, not 2025!)

The big AAOI move was YESTERDAY Jan 28, 2026 - not Jan 2025!
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
print("AAOI PRICE - JANUARY 2026 (CORRECT DATE RANGE)")
print("="*70)

# Get AAOI prices for Jan 2026 - the CORRECT date range
bars = get_stock_history("AAOI", "2026-01-20", "2026-01-31")

print("\nAAOI Daily Prices (Jan 2026):")
print("-"*70)
for bar in bars:
    ts_ms = bar.get("t", 0)
    dt = datetime.fromtimestamp(ts_ms / 1000)
    o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
    vol = bar.get("v", 0)
    
    change_pct = ((c - o) / o * 100) if o else 0
    
    marker = " <-- BIG MOVE!" if change_pct > 15 else ""
    
    print(f"{dt.strftime('%Y-%m-%d %a')} | Open: ${o:>7.2f} | High: ${h:>7.2f} | "
          f"Low: ${l:>7.2f} | Close: ${c:>7.2f} | Change: {change_pct:>+6.1f}% | Vol: {vol:>12,.0f}{marker}")

print("\n" + "="*70)
print("NOW TESTING HISTORICAL OPTIONS FOR JAN 28, 2026...")
print("="*70)

# Find AAOI options contracts for Jan 2026
url = f"{BASE_URL}/v3/reference/options/contracts"
params = {
    "underlying_ticker": "AAOI",
    "expiration_date.gte": "2026-01-28",
    "expiration_date.lte": "2026-03-31",
    "contract_type": "call",
    "limit": 50,
    "apiKey": API_KEY
}

response = requests.get(url, params=params)
if response.status_code == 200:
    data = response.json()
    contracts = data.get("results", [])
    
    # Filter for ATM calls (AAOI was ~$37 before the move, ~$45 after)
    atm_calls = [c for c in contracts if 35 <= c.get("strike_price", 0) <= 50]
    
    print(f"\nFound {len(contracts)} call contracts, {len(atm_calls)} ATM ($35-50 strike)")
    
    if atm_calls:
        print("\nATM Call Contracts:")
        for c in atm_calls[:10]:
            print(f"  {c.get('ticker')} | Strike: ${c.get('strike_price')} | Exp: {c.get('expiration_date')}")
        
        # Test one contract
        test_contract = atm_calls[0].get("ticker")
        print(f"\n--- Testing trades for {test_contract} on 2026-01-28 ---")
        
        trades_url = f"{BASE_URL}/v3/trades/{test_contract}"
        trades_params = {
            "timestamp.gte": "2026-01-28T09:30:00Z",
            "timestamp.lte": "2026-01-28T16:00:00Z",
            "limit": 100,
            "apiKey": API_KEY
        }
        
        trades_response = requests.get(trades_url, params=trades_params)
        if trades_response.status_code == 200:
            trades_data = trades_response.json()
            trades = trades_data.get("results", [])
            print(f"Found {len(trades)} trades!")
            
            if trades:
                # Aggregate by hour
                hourly_vol = {}
                for t in trades:
                    ts_ns = t.get("sip_timestamp", 0)
                    dt = datetime.fromtimestamp(ts_ns / 1e9)
                    hour = dt.strftime("%H:00")
                    hourly_vol[hour] = hourly_vol.get(hour, 0) + t.get("size", 0)
                
                print("\nHourly Volume:")
                for hour in sorted(hourly_vol.keys()):
                    vol = hourly_vol[hour]
                    bar = "#" * min(50, vol // 5)
                    print(f"  {hour}: {vol:>5,} {bar}")
                
                print("\nFirst 10 trades:")
                for t in trades[:10]:
                    ts_ns = t.get("sip_timestamp", 0)
                    dt = datetime.fromtimestamp(ts_ns / 1e9)
                    print(f"  {dt.strftime('%H:%M:%S')} | ${t.get('price'):.2f} x {t.get('size')}")
        else:
            print(f"Trades error: {trades_response.status_code} - {trades_response.text}")

print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print("""
If we can see AAOI options trades for Jan 28, 2026:
  -> We can backtest on RECENT data (not just historical)
  -> We can validate the detection algorithm on this exact case
  -> We have tick-level data to see WHEN the call buying started
""")
