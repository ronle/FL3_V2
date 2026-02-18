"""
AAOI Options Flow Analysis - Jan 28, 2026 (THE BIG MOVE DAY)

Stock: $38.25 open -> $46.30 high -> $45.25 close (+18.3%)
Question: When did the call buying start? Could we have detected it early?
"""

import requests
from datetime import datetime
from collections import defaultdict

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"

print("="*70)
print("AAOI OPTIONS FLOW - JAN 28, 2026 (THE +18% DAY)")
print("="*70)
print("Stock: Open $38.25 -> High $46.30 -> Close $45.25 (+18.3%)")
print("="*70)

# Get all AAOI call contracts expiring soon
url = f"{BASE_URL}/v3/reference/options/contracts"
params = {
    "underlying_ticker": "AAOI",
    "expiration_date.gte": "2026-01-28",
    "expiration_date.lte": "2026-02-28",
    "contract_type": "call",
    "limit": 100,
    "apiKey": API_KEY
}

response = requests.get(url, params=params)
contracts = response.json().get("results", []) if response.status_code == 200 else []

# Filter for ATM/OTM calls ($38-55 range - stock opened at $38.25)
relevant_contracts = [c for c in contracts if 38 <= c.get("strike_price", 0) <= 55]
print(f"\nFound {len(relevant_contracts)} relevant call contracts (strike $38-55)")

# Collect trades across all contracts
all_trades = []
contract_volumes = {}

print("\nFetching trades for each contract...")
for i, contract in enumerate(relevant_contracts[:25]):  # Limit to top 25
    ticker = contract.get("ticker")
    strike = contract.get("strike_price")
    expiry = contract.get("expiration_date")
    
    trades_url = f"{BASE_URL}/v3/trades/{ticker}"
    trades_params = {
        "timestamp.gte": "2026-01-28T04:00:00Z",  # Include premarket
        "timestamp.lte": "2026-01-28T20:00:00Z",  # Include after hours
        "limit": 1000,
        "apiKey": API_KEY
    }
    
    trades_response = requests.get(trades_url, params=trades_params)
    if trades_response.status_code == 200:
        trades = trades_response.json().get("results", [])
        total_vol = sum(t.get("size", 0) for t in trades)
        
        if total_vol > 0:
            contract_volumes[ticker] = {
                "strike": strike, 
                "expiry": expiry,
                "trades": len(trades), 
                "volume": total_vol
            }
            
            for t in trades:
                t["contract"] = ticker
                t["strike"] = strike
                t["expiry"] = expiry
                all_trades.append(t)
    
    if (i+1) % 10 == 0:
        print(f"  Processed {i+1}/{len(relevant_contracts[:25])} contracts...")

print(f"\nTotal trades collected: {len(all_trades)}")
print(f"Total call volume: {sum(t.get('size', 0) for t in all_trades):,}")

# Sort by timestamp
all_trades.sort(key=lambda x: x.get("sip_timestamp", 0))

# Aggregate by 5-minute intervals
interval_volume = defaultdict(int)
interval_value = defaultdict(float)  # $ value of trades

for t in all_trades:
    ts_ns = t.get("sip_timestamp", 0)
    ts_sec = ts_ns / 1e9
    dt = datetime.fromtimestamp(ts_sec)
    
    # Round to 5-minute interval
    minute = (dt.minute // 5) * 5
    interval = dt.replace(minute=minute, second=0, microsecond=0)
    interval_str = interval.strftime("%H:%M")
    
    size = t.get("size", 0)
    price = t.get("price", 0)
    
    interval_volume[interval_str] += size
    interval_value[interval_str] += size * price * 100  # Contract value

print("\n" + "-"*70)
print("CALL VOLUME BY 5-MIN INTERVAL (showing 9:30 AM - 4:00 PM)")
print("-"*70)
print("Time  | Volume | $ Value      | Bar")
print("-"*70)

# Show market hours
for hour in range(4, 20):
    for minute in [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]:
        interval = f"{hour:02d}:{minute:02d}"
        if interval in interval_volume:
            vol = interval_volume[interval]
            val = interval_value[interval]
            bar = "#" * min(50, vol // 20)
            
            # Highlight if volume is high
            marker = " <<<" if vol > 200 else ""
            print(f"{interval} | {vol:>6,} | ${val:>10,.0f} | {bar}{marker}")

# Top volume contracts
print("\n" + "-"*70)
print("TOP VOLUME CONTRACTS")
print("-"*70)

sorted_contracts = sorted(contract_volumes.items(), key=lambda x: x[1]["volume"], reverse=True)
for ticker, data in sorted_contracts[:15]:
    print(f"${data['strike']:>5} {data['expiry']} | Trades: {data['trades']:>4} | Volume: {data['volume']:>6,}")

# First trades of the day - when did it start?
print("\n" + "-"*70)
print("EARLIEST TRADES (When did the flow start?)")
print("-"*70)

for t in all_trades[:25]:
    ts_ns = t.get("sip_timestamp", 0)
    dt = datetime.fromtimestamp(ts_ns / 1e9)
    print(f"{dt.strftime('%H:%M:%S')} | ${t.get('strike'):>5} strike | "
          f"${t.get('price'):>6.2f} x {t.get('size'):>4} | Exp: {t.get('expiry')}")

print("\n" + "="*70)
print("KEY QUESTION: Could we have detected this BEFORE the big move?")
print("="*70)
