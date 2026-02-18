"""
AAOI Jan 27, 2026 - THE DAY OF THE BIG MOVE

Stock went: $37.87 open -> $46.30 high -> $45.23 close (+19.4%)
Let's see when the options flow started!
"""

import requests
from datetime import datetime
from collections import defaultdict

API_KEY = "8byQS7ronQSqOjDXQq4JPUU1R64Prvsm"
BASE_URL = "https://api.polygon.io"

print("="*70)
print("AAOI OPTIONS FLOW ANALYSIS - JAN 27, 2026")
print("="*70)
print("Stock Action: Open $37.87 -> High $46.30 -> Close $45.23 (+19.4%)")
print("="*70)

# Get all AAOI call contracts that were active on Jan 27
url = f"{BASE_URL}/v3/reference/options/contracts"
params = {
    "underlying_ticker": "AAOI",
    "expiration_date.gte": "2026-01-27",
    "expiration_date.lte": "2026-02-28",
    "contract_type": "call",
    "limit": 100,
    "apiKey": API_KEY
}

response = requests.get(url, params=params)
contracts = response.json().get("results", []) if response.status_code == 200 else []

# Filter for relevant strikes ($35-55 range around the action)
relevant_contracts = [c for c in contracts if 35 <= c.get("strike_price", 0) <= 55]

print(f"\nFound {len(relevant_contracts)} relevant call contracts (strike $35-55)")

# Aggregate trades across all contracts for Jan 27
all_trades = []
contract_volumes = {}

for contract in relevant_contracts[:30]:  # Limit to avoid rate limits
    ticker = contract.get("ticker")
    strike = contract.get("strike_price")
    
    trades_url = f"{BASE_URL}/v3/trades/{ticker}"
    trades_params = {
        "timestamp.gte": "2026-01-27T04:00:00Z",  # Premarket
        "timestamp.lte": "2026-01-27T20:00:00Z",  # After hours
        "limit": 500,
        "apiKey": API_KEY
    }
    
    trades_response = requests.get(trades_url, params=trades_params)
    if trades_response.status_code == 200:
        trades = trades_response.json().get("results", [])
        total_vol = sum(t.get("size", 0) for t in trades)
        contract_volumes[ticker] = {"strike": strike, "trades": len(trades), "volume": total_vol}
        
        for t in trades:
            t["contract"] = ticker
            t["strike"] = strike
            all_trades.append(t)

print(f"\nTotal trades collected: {len(all_trades)}")

# Sort all trades by timestamp
all_trades.sort(key=lambda x: x.get("sip_timestamp", 0))

# Aggregate by 5-minute intervals
interval_volume = defaultdict(int)
interval_contracts = defaultdict(set)

for t in all_trades:
    ts_ns = t.get("sip_timestamp", 0)
    ts_sec = ts_ns / 1e9
    dt = datetime.fromtimestamp(ts_sec)
    
    # Round to 5-minute interval
    minute = (dt.minute // 5) * 5
    interval = dt.replace(minute=minute, second=0, microsecond=0)
    interval_str = interval.strftime("%H:%M")
    
    interval_volume[interval_str] += t.get("size", 0)
    interval_contracts[interval_str].add(t.get("contract"))

print("\n" + "-"*70)
print("CALL VOLUME BY 5-MINUTE INTERVAL (Jan 27, 2026)")
print("-"*70)

# Filter to market hours
market_intervals = sorted([i for i in interval_volume.keys() if "09:" <= i <= "16:" or "06:" <= i <= "09:"])

for interval in market_intervals:
    vol = interval_volume[interval]
    contracts_count = len(interval_contracts[interval])
    bar = "#" * min(60, vol // 10)
    print(f"{interval} | Vol: {vol:>6,} | Contracts: {contracts_count:>2} | {bar}")

# Top volume contracts
print("\n" + "-"*70)
print("TOP VOLUME CONTRACTS")
print("-"*70)

sorted_contracts = sorted(contract_volumes.items(), key=lambda x: x[1]["volume"], reverse=True)
for ticker, data in sorted_contracts[:10]:
    print(f"{ticker} | Strike: ${data['strike']:>5} | Trades: {data['trades']:>4} | Volume: {data['volume']:>6,}")

# Timeline of first trades
print("\n" + "-"*70)
print("EARLIEST TRADES (DETECTING THE START OF THE FLOW)")
print("-"*70)

for t in all_trades[:20]:
    ts_ns = t.get("sip_timestamp", 0)
    dt = datetime.fromtimestamp(ts_ns / 1e9)
    print(f"{dt.strftime('%H:%M:%S')} | ${t.get('strike'):>5} strike | "
          f"${t.get('price'):>6.2f} x {t.get('size'):>3} | {t.get('contract')[-15:]}")

print("\n" + "="*70)
print("KEY FINDINGS")
print("="*70)
